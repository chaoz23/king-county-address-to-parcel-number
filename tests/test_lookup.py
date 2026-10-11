import io
import json
import unittest
import urllib.parse
from unittest import mock

import lookup


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class InputValidationTests(unittest.TestCase):
    def test_non_kc_city_name_in_street_does_not_reject(self):
        addresses = [
            "3617 Lakewood Ave S, Seattle, WA 98144",
            "123 Everett Ave, Seattle, WA",
            "123 Vancouver Ave S, Seattle, WA",
            "123 Oregon Ave, Seattle, WA",
            "123 California Ave SW, Seattle, WA",
        ]

        for address in addresses:
            with self.subTest(address=address):
                self.assertIsNone(lookup.check_input(address))

    def test_explicit_non_kc_city_is_rejected(self):
        addresses = [
            "123 Main St, Lakewood, WA 98499",
            "123 Main St, Mountlake Terrace, Washington 98043",
            "123 Main St Tacoma WA 98402",
        ]

        for address in addresses:
            with self.subTest(address=address):
                result = lookup.check_input(address)
                self.assertEqual("reject", result["action"])

    def test_city_name_substring_is_not_treated_as_locality(self):
        self.assertIsNone(lookup.check_input("123 Vancouvering St, Seattle, WA"))

    def test_explicit_non_wa_state_is_rejected(self):
        addresses = [
            "123 Main St, Portland, OR 97201",
            "123 Main St Portland Oregon 97201",
            "123 Main St, Boise, Idaho",
        ]

        for address in addresses:
            with self.subTest(address=address):
                result = lookup.check_input(address)
                self.assertEqual("reject", result["action"])

    def test_ordinal_street_without_house_number_is_rejected(self):
        addresses = [
            "5th Ave Seattle",
            "NE 8th St, Bellevue, WA",
            "I-5 Seattle WA",
        ]

        for address in addresses:
            with self.subTest(address=address):
                result = lookup.check_input(address)
                self.assertEqual("reject", result["action"])
                self.assertIn("No house number", result["message"])

    def test_leading_house_number_is_still_valid(self):
        addresses = [
            "1817 Morris Ave S, Renton, WA 98055",
            "123A Main St, Seattle, WA",
            "123-125 Main St, Seattle, WA",
        ]

        for address in addresses:
            with self.subTest(address=address):
                self.assertIsNone(lookup.check_input(address))


class LookupTests(unittest.TestCase):
    candidate_fields = {
        "address",
        "parcel_number",
        "score",
        "house_number_distance",
    }

    def assert_candidate_contract(self, candidate):
        self.assertEqual(self.candidate_fields, set(candidate))

    @mock.patch("lookup.urllib.request.urlopen")
    def test_street_name_collision_reaches_geocoder(self, urlopen):
        urlopen.return_value = FakeResponse({
            "candidates": [{
                "score": 100,
                "attributes": {
                    "PIN": "1234567890",
                    "Match_addr": "3617 LAKEWOOD AVE S, Seattle, WA, 98144",
                },
            }],
        })

        result = lookup.lookup("3617 Lakewood Ave S, Seattle, WA 98144")

        self.assertEqual("use", result["action"])
        self.assertEqual("1234567890", result["parcel_number"])
        urlopen.assert_called_once()

    @mock.patch("lookup.urllib.request.urlopen")
    def test_explicit_non_kc_city_does_not_call_geocoder(self, urlopen):
        result = lookup.lookup("123 Main St, Tacoma, WA 98402")

        self.assertEqual("reject", result["action"])
        urlopen.assert_not_called()

    @mock.patch("lookup.urllib.request.urlopen")
    def test_ordinal_street_without_house_number_does_not_call_geocoder(self, urlopen):
        result = lookup.lookup("5th Ave Seattle")

        self.assertEqual("reject", result["action"])
        self.assertIn("No house number", result["message"])
        urlopen.assert_not_called()

    @mock.patch("lookup.urllib.request.urlopen")
    def test_no_geocoder_or_nearby_results_requests_refinement(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"candidates": []}),      # locator
            FakeResponse({"candidates": []}),      # ', WA' retry
            FakeResponse({"features": []}),        # nearby address points
        ]

        result = lookup.lookup("123 Imaginary St, Seattle, WA")

        self.assertEqual("refine", result["action"])
        self.assertIsNone(result["parcel_number"])
        self.assertEqual(3, urlopen.call_count)
        retry = urlopen.call_args_list[1].args[0].full_url
        self.assertIn(urllib.parse.quote_plus("123 Imaginary St, WA"), retry)

    @mock.patch("lookup.urllib.request.urlopen")
    def test_geocoder_pick_uses_canonical_candidate_contract(self, urlopen):
        urlopen.return_value = FakeResponse({
            "candidates": [{
                "score": 82,
                "attributes": {
                    "PIN": "1234567890",
                    "Match_addr": "123 MAIN ST, Seattle, WA, 98101",
                },
            }],
        })

        result = lookup.lookup("123 Main St, Seattle, WA")

        self.assertEqual("pick", result["action"])
        candidate = result["candidates"][0]
        self.assert_candidate_contract(candidate)
        self.assertEqual(82, candidate["score"])
        self.assertIsNone(candidate["house_number_distance"])

    @mock.patch("lookup.urllib.request.urlopen")
    def test_nearby_pick_uses_canonical_candidate_contract(self, urlopen):
        urlopen.side_effect = [
            FakeResponse({"candidates": []}),
            FakeResponse({"candidates": []}),      # ', WA' retry
            FakeResponse({
                "features": [{
                    "attributes": {
                        "ADDR_FULL": "125 MAIN ST, SEATTLE, WA 98101",
                        "PIN": "1234567890",
                        "ADDR_HN": 125,
                    },
                }],
            }),
        ]

        result = lookup.lookup("123 Main St, Seattle, WA")

        self.assertEqual("pick", result["action"])
        candidate = result["candidates"][0]
        self.assert_candidate_contract(candidate)
        self.assertIsNone(candidate["score"])
        self.assertEqual(2, candidate["house_number_distance"])


class PinLookupTests(unittest.TestCase):
    @mock.patch("lookup.urllib.request.urlopen")
    def test_bare_pin_is_verified_against_parcel_layer(self, urlopen):
        urlopen.return_value = FakeResponse({
            "features": [{
                "attributes": {
                    "PIN": "7222000353",
                    "MAJOR": "722200",
                    "MINOR": "0353",
                },
            }],
        })

        result = lookup.lookup("7222000353")

        self.assertEqual("use", result["action"])
        self.assertEqual("7222000353", result["parcel_number"])
        self.assertEqual("722200", result["major"])
        self.assertEqual("0353", result["minor"])
        request = urlopen.call_args.args[0]
        self.assertIn("KingCo_Parcels", request.full_url)
        self.assertIn("PIN%3D%277222000353%27", request.full_url)

    @mock.patch("lookup.urllib.request.urlopen")
    def test_pin_prefix_is_normalized_before_verification(self, urlopen):
        urlopen.return_value = FakeResponse({
            "features": [{
                "attributes": {
                    "PIN": "7222000353",
                    "MAJOR": "722200",
                    "MINOR": "0353",
                },
            }],
        })

        result = lookup.lookup("PIN: 7222000353")

        self.assertEqual("use", result["action"])
        self.assertEqual("PIN: 7222000353", result["input"])

    @mock.patch("lookup.urllib.request.urlopen")
    def test_unknown_pin_is_rejected(self, urlopen):
        urlopen.return_value = FakeResponse({"features": []})

        result = lookup.lookup("0000000000")

        self.assertEqual("reject", result["action"])
        self.assertIsNone(result["parcel_number"])
        self.assertIn("No King County parcel exists", result["message"])

    @mock.patch("lookup.urllib.request.urlopen")
    def test_parcel_service_failure_does_not_reject_pin(self, urlopen):
        urlopen.side_effect = OSError("service unavailable")

        result = lookup.lookup("7222000353")

        self.assertEqual("refine", result["action"])
        self.assertIsNone(result["parcel_number"])
        self.assertIn("Could not verify", result["message"])

    @mock.patch("lookup.urllib.request.urlopen")
    def test_parcel_service_error_payload_does_not_reject_pin(self, urlopen):
        urlopen.return_value = FakeResponse({
            "error": {"code": 503, "message": "Service unavailable"},
        })

        result = lookup.lookup("7222000353")

        self.assertEqual("refine", result["action"])
        self.assertIsNone(result["parcel_number"])

    @mock.patch("lookup.urllib.request.urlopen")
    def test_malformed_parcel_feature_does_not_crash_or_reject(self, urlopen):
        urlopen.return_value = FakeResponse({"features": [{"attributes": None}]})

        result = lookup.lookup("7222000353")

        self.assertEqual("refine", result["action"])
        self.assertIsNone(result["parcel_number"])


class SchemaTests(unittest.TestCase):
    def test_checked_in_tool_schema_matches_runtime_schema(self):
        with open("tool.json", encoding="utf-8") as schema_file:
            self.assertEqual(lookup.TOOL_SCHEMA, json.load(schema_file))

    def test_candidate_schema_requires_canonical_fields(self):
        candidate_schema = (
            lookup.TOOL_SCHEMA["output_schema"]["properties"]["candidates"]["items"]
        )

        self.assertEqual(
            {"address", "parcel_number", "score", "house_number_distance"},
            set(candidate_schema["required"]),
        )


class CliTests(unittest.TestCase):
    def run_main(self, action):
        result = {"action": action, "message": action, "parcel_number": None}
        output = io.StringIO()
        with (
            mock.patch.object(lookup.sys, "argv", ["lookup.py", "--pipe", "address"]),
            mock.patch.object(lookup, "lookup", return_value=result),
            mock.patch("sys.stdout", output),
            self.assertRaises(SystemExit) as exit_context,
        ):
            lookup.main()
        return exit_context.exception.code, json.loads(output.getvalue())

    def test_action_exit_codes(self):
        expected_codes = {"use": 0, "pick": 1, "refine": 1, "reject": 2}

        for action, expected_code in expected_codes.items():
            with self.subTest(action=action):
                exit_code, result = self.run_main(action)
                self.assertEqual(expected_code, exit_code)
                self.assertEqual(action, result["action"])


if __name__ == "__main__":
    unittest.main()


def _cand(addr_type="PointAddress", pin="7222000353", county="KING", city="RENTON", score=100,
          address="1817 MORRIS AVE S, RENTON, WA, 98055", addnum="1817", loc=True):
    return {"address": address, "score": score,
            "location": {"x": 1299539.4, "y": 172002.5} if loc else {},
            "attributes": {"Addr_type": addr_type, "PIN": pin, "Subregion": county,
                           "City": city, "AddNum": addnum, "Match_addr": address}}


class ResolverTests(unittest.TestCase):
    """The resolver ported from king-county-permit-status (#10–#14)."""

    def _fake(self, geocodes, polygon=("Renton", 0), parcels=None):
        """_arcgis_json stand-in: geocoder calls pop from `geocodes`; polygon
        and parcel-layer calls answer from the other args."""
        calls = []
        def fake(url, params, timeout=15):
            calls.append((url, params))
            if url == lookup.GEOCODER_URL:
                return {"candidates": geocodes.pop(0) if geocodes else []}
            if url == lookup.CITY_LIMITS_URL:
                return {"features": [{"attributes": {"NAME": polygon[0], "UNINC": polygon[1]}}]} if polygon else {"features": []}
            if url == lookup.PARCELS_URL:
                return {"features": [{"attributes": {"PIN": p}} for p in (parcels or {}).get(params["distance"], [])]}
            raise AssertionError(url)
        return fake, calls

    def test_exact_point_is_use_with_namespaced_pin_and_polygon_jurisdiction(self):
        fake, calls = self._fake([[_cand()]])
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("1817 Morris Ave S, Renton, WA 98055")
        self.assertEqual(r["action"], "use")
        self.assertEqual(r["parcel_id"], "king:7222000353")
        self.assertEqual(r["county"], "king")
        self.assertEqual(r["jurisdiction"], {"city": "Renton", "basis": "city-limits", "unincorporated": False})
        ar = r["address_resolution"]
        self.assertEqual((ar["match_type"], ar["partial_match"], ar["street_number_snapped"], ar["parcel_basis"]),
                         ("point", False, False, "address-point"))
        self.assertTrue(all(u != lookup.PARCELS_URL for u, _ in calls))        # no parcel-layer call needed

    def test_mailing_city_kent_but_unincorporated_polygon(self):
        fake, _ = self._fake([[_cand(city="KENT", pin="9825700835")]], polygon=("King County", 1))
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("220 4th Ave S, Kent, WA 98032")
        self.assertEqual(r["jurisdiction"], {"city": None, "basis": "city-limits", "unincorporated": True})

    def test_interpolated_match_takes_parcel_polygon_and_is_a_pick(self):
        fake, _ = self._fake([[_cand(addr_type="StreetAddress", pin="", city="Beaux Arts", score=98)]],
                             polygon=("Beaux Arts", 0), parcels={"0": [], "40": ["0629000510"]})
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("10550 SE 27th St, Beaux Arts Village, WA")
        self.assertEqual(r["action"], "pick")
        self.assertEqual(r["parcel_number"], "0629000510")
        self.assertEqual(r["address_resolution"]["parcel_basis"], "nearby-parcel")
        self.assertEqual(r["address_resolution"]["match_type"], "interpolated")
        self.assertEqual(r["jurisdiction"]["city"], "Beaux Arts Village")
        self.assertIn("interpolated", r["message"])

    def test_multiple_nearby_parcels_are_exposed(self):
        fake, _ = self._fake([[_cand(addr_type="StreetAddress", pin="", city="Black Diamond", score=97)]],
                             polygon=("Black Diamond", 0), parcels={"0": ["1121069104", "1121069037"]})
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("25008 Roberts Dr, Black Diamond, WA")
        self.assertEqual(r["address_resolution"]["candidate_parcels"], ["1121069104", "1121069037"])

    def test_locality_plus_zip_miss_retries_as_house_street_wa(self):
        vashon = _cand(addr_type="Parcel", pin="1823039057", city="", score=99, address="13205 VASHON HWY SW, WA", addnum="13205")
        fake, calls = self._fake([[], [vashon]], polygon=("King County", 1))
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("13205 Vashon Hwy SW, Vashon, WA 98070")
        geocodes = [p["SingleLine"] for u, p in calls if u == lookup.GEOCODER_URL]
        self.assertEqual(geocodes, ["13205 Vashon Hwy SW, Vashon, WA 98070", "13205 Vashon Hwy SW, WA"])
        self.assertEqual(r["action"], "use")                     # a Parcel candidate is an exact point
        self.assertTrue(r["jurisdiction"]["unincorporated"])

    def test_exact_point_accepted_at_75_but_locality_rejected(self):
        self.assertTrue(lookup.usable(_cand(score=75)))
        self.assertFalse(lookup.usable(_cand(score=75, addr_type="StreetAddress")))
        self.assertFalse(lookup.usable(_cand(score=72, addr_type="Locality")))

    def test_ambiguous_cityless_address_prefers_king_or_asks(self):
        kent = _cand(city="KENT", pin="9825700835", address="220 4TH AVE S, KENT, WA")
        edmonds = _cand(city="EDMONDS", county="SNOHOMISH", pin="27032300406600", address="220 4TH AVE S, EDMONDS, WA")
        fake, _ = self._fake([[], [edmonds, kent]], polygon=("Kent", 0))
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("220 4th Ave S")
        self.assertEqual((r["action"], r["parcel_id"]), ("use", "king:9825700835"))
        auburn = _cand(city="AUBURN", pin="1111111111", address="220 4TH AVE S, AUBURN, WA")
        fake, _ = self._fake([[], [auburn, kent]], polygon=("Kent", 0))
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("220 4th Ave S")
        self.assertEqual(r["action"], "pick")
        self.assertEqual(len(r["candidates"]), 2)
        self.assertIn("more than one city", r["message"])

    def test_pierce_side_parcel_is_namespaced_and_noted(self):
        milton = _cand(county="PIERCE", city="MILTON", pin="5985001690", address="1000 LAUREL ST, MILTON, WA, 98354", addnum="1000")
        fake, _ = self._fake([[milton]], polygon=("Milton", 0))
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("1000 Laurel St, Milton, WA 98354")
        self.assertEqual(r["parcel_id"], "pierce:5985001690")
        self.assertIn("Pierce County", r["note"])

    def test_polygon_failure_falls_back_to_geocoder_city(self):
        fake, _ = self._fake([[_cand()]], polygon=None)
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("1817 Morris Ave S, Renton, WA 98055")
        self.assertEqual(r["jurisdiction"], {"city": "Renton", "basis": "geocoder-city", "unincorporated": False})

    def test_snapped_house_number_is_flagged(self):
        fake, _ = self._fake([[_cand(addnum="1815")]])
        with mock.patch.object(lookup, "_arcgis_json", side_effect=fake):
            r = lookup.lookup("1817 Morris Ave S, Renton, WA 98055")
        self.assertTrue(r["address_resolution"]["street_number_snapped"])

    def test_verified_pin_carries_namespace(self):
        with mock.patch("lookup.urllib.request.urlopen") as urlopen:
            urlopen.return_value = FakeResponse({"features": [{"attributes": {"PIN": "7222000353", "MAJOR": "722200", "MINOR": "0353"}}]})
            r = lookup.lookup("7222000353")
        self.assertEqual((r["parcel_id"], r["county"]), ("king:7222000353", "king"))

