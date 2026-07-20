import io
import json
import unittest
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
            FakeResponse({"candidates": []}),
            FakeResponse({"features": []}),
        ]

        result = lookup.lookup("123 Imaginary St, Seattle, WA")

        self.assertEqual("refine", result["action"])
        self.assertIsNone(result["parcel_number"])
        self.assertEqual(2, urlopen.call_count)

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
