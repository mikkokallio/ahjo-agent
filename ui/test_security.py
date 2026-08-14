import asyncio
import unittest

import httpx

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

    def test_attachment_urls_allow_only_ahjo_document_hosts(self):
        self.assertEqual(
            app._normalize_attachment_url("https://ahjojulkaisu.hel.fi/document.pdf"),
            "https://ahjojulkaisu.hel.fi/document.pdf",
        )
        self.assertEqual(
            app._normalize_attachment_url("/document.pdf"),
            "https://paatokset.hel.fi/document.pdf",
        )
        for value in ("https://example.com/document.pdf", "http://ahjojulkaisu.hel.fi/document.pdf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app._normalize_attachment_url(value)

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

    def test_extracts_nested_agent_failure_message(self):
        event = {"response": {"error": {"code": "server_error", "message": "Tool timed out"}}}

        self.assertEqual(app._response_error_message(event), "Tool timed out")

    def test_extracts_completed_text_when_deltas_are_absent(self):
        event = {"response": {"output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": "Valmis vastaus."}],
        }]}}

        self.assertEqual(app._completed_response_text(event), "Valmis vastaus.")

    def test_chat_rejects_missing_agent_endpoint(self):
        async def request_chat():
            transport = httpx.ASGITransport(app=app.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/chat", json={"message": "test"})

        original_endpoint = app.AGENT_ENDPOINT
        try:
            app.AGENT_ENDPOINT = ""
            response = asyncio.run(request_chat())
        finally:
            app.AGENT_ENDPOINT = original_endpoint

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "AGENT_ENDPOINT is not configured"})


if __name__ == "__main__":
    unittest.main()