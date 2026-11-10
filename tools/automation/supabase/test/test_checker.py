import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location("checker", Path(__file__).parents[1] / "check_readiness.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class CheckerTests(unittest.TestCase):
    def test_wrong_project_and_missing_configuration_fail_closed(self):
        env = {"SUPABASE_URL": "https://abcdefghijklmno.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret_fixture", "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_fixture"}
        self.assertEqual(checker.config("abcdefghijklmno", env)[0], env["SUPABASE_URL"])
        for reference in ("differentproject", "../danger", ""):
            with self.assertRaises(ValueError):
                checker.config(reference, env)
        with self.assertRaises(ValueError):
            checker.config("abcdefghijklmno", {})

    def test_get_only_zero_row_probe_does_not_use_secret_as_bearer(self):
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.status = 200
        self.assertEqual(checker.probe(opener, "https://abcdefghijklmno.supabase.co", "sb_secret_fixture", "/rest/v1/predictions", {"limit": 0}), 200)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(request.get_header("Apikey"), "sb_secret_fixture")
        response.read.assert_not_called()

    def test_errors_return_status_without_reading_upstream_body(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://example.test", 403, "private details", {}, None)
        self.assertEqual(checker.probe(opener, "https://abcdefghijklmno.supabase.co", "sb_publishable_fixture", "/rest/v1/predictions", {}), 403)
        self.assertIsNone(checker.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))


if __name__ == "__main__":
    unittest.main()
