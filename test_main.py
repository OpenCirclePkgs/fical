# -*- coding: utf-8 -*-
import base64
import unittest
from unittest.mock import patch
import json

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

ICS_SAMPLE_TWO = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//EN
BEGIN:VEVENT
UID:2
DTSTAMP:20240102T000000Z
DTSTART:20240102T000000Z
DTEND:20240102T010000Z
SUMMARY:event two
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

    @patch("main._is_private_host", return_value=False)
    @patch("main.requests.get")
    def test_legacy_endpoint_can_create_short_link(self, mock_get, _):
        mock_get.return_value.text = ICS_SAMPLE
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FICAL_DB_PATH"] = os.path.join(tmp, "cache.db")
            url = _encode_unpadded("https://example.com/calendar.ics")
            allowlist = _encode_unpadded("测试")

            create_resp = self.client.get(
                f"/calendar/{url}/{allowlist}/filtered.ics",
                params={"short": True},
            )
            self.assertEqual(create_resp.status_code, 200)
            short_url = create_resp.json()["short"]
            key = short_url.rsplit("/", 1)[-1]

            resolve_resp = self.client.get(f"/s/{key}")
            self.assertEqual(resolve_resp.status_code, 200)
            self.assertIn("SUMMARY:测试 event", resolve_resp.text)

    @patch("main._is_private_host", return_value=False)
    @patch("main.requests.get")
    def test_combines_multiple_calendars(self, mock_get, _):
        mock_get.side_effect = [
            type("Resp", (), {"text": ICS_SAMPLE}),
            type("Resp", (), {"text": ICS_SAMPLE_TWO}),
        ]
        body = {
            "calendars": [
                {"url": "https://example.com/one.ics", "allowlist": ["测试"], "blocklist": []},
                {"url": "https://example.com/two.ics", "allowlist": ["event"], "blocklist": []},
            ]
        }
        response = self.client.post("/calendars/combined.ics", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("SUMMARY:测试 event", response.text)
        self.assertIn("SUMMARY:event two", response.text)

    @patch("main._is_private_host", return_value=False)
    @patch("main.requests.get")
    def test_loads_combined_payload_with_multiple_calendars(self, mock_get, _):
        mock_get.side_effect = [
            type("Resp", (), {"text": ICS_SAMPLE}),
            type("Resp", (), {"text": ICS_SAMPLE_TWO}),
        ]
        body = {
            "calendars": [
                {"url": "https://example.com/one.ics", "allowlist": [], "blocklist": []},
                {"url": "https://example.com/two.ics", "allowlist": ["event"], "blocklist": []},
            ],
            "short": False,
        }
        payload = _encode_unpadded(json.dumps(body))
        resp = self.client.get(f"/calendars/combined.ics?payload={payload}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("SUMMARY:测试 event", resp.text)
        self.assertIn("SUMMARY:event two", resp.text)

    @patch("main._is_private_host", return_value=False)
    @patch("main.requests.get")
    def test_short_link_flow(self, mock_get, _):
        mock_get.return_value = type("Resp", (), {"text": ICS_SAMPLE})
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FICAL_DB_PATH"] = os.path.join(tmp, "cache.db")
            body = {
                "calendars": [{"url": "https://example.com/one.ics", "allowlist": ["测试"], "blocklist": []}],
                "short": True,
            }
            create_resp = self.client.post("/calendars/combined.ics", json=body)
            self.assertEqual(create_resp.status_code, 200)
            short_url = create_resp.json()["short"]
            key = short_url.rsplit("/", 1)[-1]

            resolve_resp = self.client.get(f"/s/{key}")
            self.assertEqual(resolve_resp.status_code, 200)
            self.assertIn("SUMMARY:测试 event", resolve_resp.text)

    @patch("main._is_private_host", return_value=False)
    @patch("main.requests.get")
    def test_get_combined_via_payload_query(self, mock_get, _):
        mock_get.return_value = type("Resp", (), {"text": ICS_SAMPLE})
        body = {
            "calendars": [{"url": "https://example.com/one.ics", "allowlist": ["测试"], "blocklist": []}],
            "short": False,
        }
        payload = _encode_unpadded(json.dumps(body))
        resp = self.client.get(f"/calendars/combined.ics?payload={payload}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("SUMMARY:测试 event", resp.text)
