import json
import os
import secrets
import sqlite3
from typing import Iterable, List

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import requests
from ics import Calendar
import base64
import binascii
import re
import ipaddress
import socket
from urllib.parse import urlparse

app = FastAPI()
REQUEST_TIMEOUT = 30
EMPTY_ALLOWLIST_TOKEN = "__empty_allowlist__"
_BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]*$")
_DB_DEFAULT_PATH = "cache.db"


def _decode_b64url_param(encoded_value: str, param_name: str) -> str:
    if not _BASE64URL_PATTERN.fullmatch(encoded_value):
        raise HTTPException(status_code=400, detail=f"Invalid base64 data for {param_name}.")

    # Base64 strings must be padded to a length divisible by 4
    padded_value = encoded_value + "=" * (-len(encoded_value) % 4)
    try:
        raw_bytes = base64.urlsafe_b64decode(padded_value)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 data for {param_name}.") from exc
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{param_name} must be valid UTF-8.") from exc


def _is_private_host(hostname: str) -> bool:
    try:
        addresses = {addr[4][0] for addr in socket.getaddrinfo(hostname, None)}
    except socket.gaierror:
        return True

    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return True

    return False


def _validate_and_normalize_url(raw_url: str) -> str:
    parsed_url = urlparse(raw_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="Only http and https URLs are supported.")
    if not parsed_url.hostname or _is_private_host(parsed_url.hostname):
        raise HTTPException(status_code=400, detail="URL host is not allowed.")
    return parsed_url.geturl()


def _fetch_calendar_body(safe_url: str) -> str:
    try:
        return requests.get(safe_url, timeout=REQUEST_TIMEOUT).text
    except requests.Timeout:
        raise HTTPException(
            status_code=400, detail=f"Timed out while getting the URL, timeout is {REQUEST_TIMEOUT} seconds."
        )
    except requests.RequestException:
        raise HTTPException(status_code=400, detail="Error getting the URL.")
    except Exception:
        raise HTTPException(status_code=500, detail="Error while fetching calendar contents.")


def _filter_calendar_from_text(cal_text: str, allowlist: Iterable[str], blocklist: Iterable[str]) -> Calendar:
    try:
        cal = Calendar(cal_text)
    except ValueError:
        raise HTTPException(status_code=400, detail="Server response not a valid ics format.")
    except Exception:
        raise HTTPException(status_code=500, detail="Error while parsing calendar contents.")

    allowlist = [w.strip() for w in allowlist if w.strip() and w.strip() != EMPTY_ALLOWLIST_TOKEN]
    blocklist = [w.strip() for w in blocklist if w.strip()]

    valid_events = []
    for c in cal.events:
        name = c.name or ""
        is_allowed = True
        if allowlist:
            is_allowed = any(word in name for word in allowlist)

        is_blocked = any(word in name for word in blocklist) if blocklist else False

        if is_allowed and not is_blocked:
            valid_events.append(c)

    cal.events = set(valid_events)
    return cal


def _filtered_calendar_from_url(raw_url: str, allowlist: Iterable[str], blocklist: Iterable[str]) -> Calendar:
    safe_url = _validate_and_normalize_url(raw_url)
    remote_cal = _fetch_calendar_body(safe_url)
    return _filter_calendar_from_text(remote_cal, allowlist, blocklist)


def _combine_calendars(inputs: Iterable["CalendarInput"]) -> Calendar:
    combined = Calendar()
    for entry in inputs:
        filtered = _filtered_calendar_from_url(entry.url, entry.allowlist, entry.blocklist)
        for ev in filtered.events:
            combined.events.add(ev)
    return combined


def _get_db_path() -> str:
    return os.environ.get("FICAL_DB_PATH", _DB_DEFAULT_PATH)


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(_get_db_path())
    db.execute(
        "CREATE TABLE IF NOT EXISTS short_links (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return db


def _save_short_payload(payload: str) -> str:
    key = secrets.token_urlsafe(6)
    conn = _get_db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO short_links (id, payload) VALUES (?, ?)", (key, payload)
        )
    conn.close()
    return key


def _load_short_payload(key: str) -> str:
    conn = _get_db()
    try:
        row = conn.execute("SELECT payload FROM short_links WHERE id = ?", (key,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Short link not found.")
    return row[0]


class CalendarInput(BaseModel):
    url: str
    allowlist: List[str] = Field(default_factory=list)
    blocklist: List[str] = Field(default_factory=list)


class CombinedCalendarRequest(BaseModel):
    calendars: List[CalendarInput]
    short: bool = False


@app.get("/")
def index():
    return FileResponse('index.html')


@app.post("/calendars/combined.ics")
async def combined_calendar(request: CombinedCalendarRequest, req: Request):
    if not request.calendars:
        raise HTTPException(status_code=400, detail="At least one calendar is required.")
    if len(request.calendars) > 5:
        raise HTTPException(status_code=400, detail="Maximum of 5 calendars are allowed per request.")

    if request.short:
        key = _save_short_payload(request.model_dump_json())
        base = str(req.base_url).rstrip("/")
        return {"short": f"{base}/s/{key}"}

    cal = _combine_calendars(request.calendars)
    return Response(content=cal.serialize(), media_type="text/calendar")


@app.get("/s/{key}")
async def resolve_short_link(key: str):
    payload = _load_short_payload(key)
    data = CombinedCalendarRequest.model_validate_json(payload)
    cal = _combine_calendars(data.calendars)
    return Response(content=cal.serialize(), media_type="text/calendar")


@app.get("/calendar/{b64url}/{b64allowlist}/filtered.ics")
async def get_calendar(b64url: str, b64allowlist: str, b64blocklist: str = Query(default="")):
    raw_url = _decode_b64url_param(b64url, "calendar URL")
    allowlist_raw = _decode_b64url_param(b64allowlist, "allowlist")
    blocklist_raw = _decode_b64url_param(b64blocklist, "blocklist") if b64blocklist else ""

    allowlist = allowlist_raw.split(",")
    blocklist = blocklist_raw.split(",") if blocklist_raw else []

    cal = _filtered_calendar_from_url(raw_url, allowlist, blocklist)
    return Response(content=cal.serialize(), media_type="text/calendar")
