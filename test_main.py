import base64
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app


def _encode_unpadded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


ICS_SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//EN
BEGIN:VEVENT
UID:1
DTSTAMP:20240101T000000Z
DTSTART:20240101T000000Z
DTEND:20240101T010000Z
SUMMARY:测试 event
END:VEVENT
END:VCALENDAR
"""


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("main._is_private_host", return_value=False)
    @patch("main.requests.get")
    def test_blocklist_allows_utf8_with_missing_padding(self, mock_get, _):
        mock_get.return_value.text = ICS_SAMPLE

        url = _encode_unpadded("https://example.com/calendar.ics")
        allowlist = _encode_unpadded("测试")
        blocklist = _encode_unpadded("禁止")

        response = self.client.get(
            f"/calendar/{url}/{allowlist}/filtered.ics",
            params={"b64blocklist": blocklist},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("SUMMARY:测试 event", response.text)

    @patch("main._is_private_host", return_value=False)
    @patch("main.requests.get")
    def test_invalid_blocklist_base64_reports_specific_error(self, mock_get, _):
        mock_get.return_value.text = ICS_SAMPLE

        url = _encode_unpadded("https://example.com/calendar.ics")
        allowlist = _encode_unpadded("test")

        response = self.client.get(
            f"/calendar/{url}/{allowlist}/filtered.ics",
            params={"b64blocklist": "not-base64!!"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid base64 data for blocklist.")
