import asyncio
import unittest

import app


class SecurityBoundaryTests(unittest.TestCase):
    def test_accepts_absolute_and_relative_ahjo_https_urls(self):
        self.assertEqual(
            app._normalize_ahjo_url("/fi/asia/test"),
            "https://paatokset.hel.fi/fi/asia/test",
        )
        self.assertEqual(
            app._normalize_ahjo_url("https://paatokset.hel.fi/fi/asia/test"),
            "https://paatokset.hel.fi/fi/asia/test",
        )

    def test_rejects_non_https_external_and_credentialed_urls(self):
        rejected = (
            "http://paatokset.hel.fi/fi/asia/test",
            "https://example.com/fi/asia/test",
            "http://169.254.169.254/latest/meta-data",
            "https://user@paatokset.hel.fi/fi/asia/test",
            "https://paatokset.hel.fi:8443/fi/asia/test",
        )
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                app._normalize_ahjo_url(value)

    def test_function_key_is_sent_in_header(self):
        original_key = app.PDF_PROCESSOR_KEY
        try:
            app.PDF_PROCESSOR_KEY = "test-key"
            self.assertEqual(app._function_headers(), {"x-functions-key": "test-key"})
        finally:
            app.PDF_PROCESSOR_KEY = original_key

    def test_agenda_rejects_invalid_meeting_url(self):
        response = asyncio.run(app.get_agenda(meeting_link="http://169.254.169.254/latest/meta-data"))

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"paatokset.hel.fi", response.body)


if __name__ == "__main__":
    unittest.main()