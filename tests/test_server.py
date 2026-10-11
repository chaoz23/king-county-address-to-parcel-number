import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import server  # noqa: E402

FAKE = {"action": "use", "parcel_number": "7222000353", "parcel_id": "king:7222000353", "county": "king",
        "major": "722200", "minor": "0353", "matched_address": "1817 MORRIS AVE S, RENTON, WA, 98055",
        "score": 100, "message": "Matched", "candidates": [],
        "jurisdiction": {"city": "Renton", "basis": "city-limits", "unincorporated": False},
        "address_resolution": {"match_type": "point", "partial_match": False, "street_number_snapped": False,
                               "parcel_basis": "address-point"}}


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        def fake(q):
            cls.calls.append(q)
            return dict(FAKE, input=q) if q.strip() else {"action": "reject", "message": "blank"}
        cls._patch = patch.object(server, "run_lookup", side_effect=fake); cls._patch.start()
        urllib.request.install_opener(urllib.request.build_opener())
        server.CACHE = server.TTLCache(300)
        server.LIMITER = server.RateLimiter(per_key=50, global_limit=500)
        cls.httpd = server.serve("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls._patch.stop()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, json.loads(r.read()) if r.headers["Content-Type"].startswith("application/json") else r.read().decode()

    def rpc(self, body):
        req = urllib.request.Request(self.base + "/mcp", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw else None)

    def test_docs_and_health(self):
        self.assertTrue(self.get("/healthz")[1]["ok"])
        spec = self.get("/openapi.json")[1]
        self.assertIn("/api/parcel", spec["paths"]); self.assertIn("ParcelResult", spec["components"]["schemas"])
        self.assertIn("jurisdiction", spec["components"]["schemas"]["ParcelResult"]["properties"])
        self.assertIn("/api/parcel", self.get("/llms.txt")[1])
        self.assertEqual(self.get("/tool.json")[1]["name"], "king_county_address_to_parcel")

    def test_rest_and_cache(self):
        n = len(self.calls)
        d = self.get("/api/parcel?q=600+S+Grady+Way,+Renton+WA")[1]      # not used by the MCP test
        self.assertEqual((d["parcel_id"], d["cached"]), ("king:7222000353", False))
        d = self.get("/api/parcel?q=600+s+grady+way,+renton+wa")[1]
        self.assertTrue(d["cached"]); self.assertEqual(len(self.calls), n + 1)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/parcel")
        self.assertEqual(cm.exception.code, 400)

    def test_mcp_flow(self):
        _, r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        self.assertEqual(r["result"]["serverInfo"]["name"], "king-county-address-to-parcel-number")
        _, r = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool = r["result"]["tools"][0]
        self.assertEqual(tool["name"], "king_county_address_to_parcel"); self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertIn("address", tool["inputSchema"]["properties"])
        _, r = self.rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "king_county_address_to_parcel", "arguments": {"address": "1817 Morris Ave S, Renton WA"}}})
        self.assertFalse(r["result"]["isError"]); self.assertEqual(r["result"]["structuredContent"]["jurisdiction"]["city"], "Renton")
        _, r = self.rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "king_county_address_to_parcel", "arguments": {}}})
        self.assertTrue(r["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
