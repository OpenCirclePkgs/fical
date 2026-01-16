from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
import requests
from ics import Calendar
import base64
import binascii
import ipaddress
import socket
from urllib.parse import urlparse

app = FastAPI()
REQUEST_TIMEOUT = 30
EMPTY_ALLOWLIST_TOKEN = "__empty_allowlist__"


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


@app.get("/")
def index():
    return FileResponse('index.html')


@app.get("/calendar/{b64url}/{b64allowlist}/filtered.ics")
async def get_calendar(b64url: str, b64allowlist: str, b64blocklist: str = Query(default="")):
    try:
        raw_url = base64.urlsafe_b64decode(b64url).decode("utf-8")
        allowlist_raw = base64.urlsafe_b64decode(b64allowlist).decode("utf-8")
        blocklist_raw = base64.urlsafe_b64decode(b64blocklist).decode("utf-8") if b64blocklist else ""
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 data.")

    allowlist = [
        w.strip() for w in allowlist_raw.split(",") if w.strip() and w.strip() != EMPTY_ALLOWLIST_TOKEN
    ]
    blocklist = [w.strip() for w in blocklist_raw.split(",") if w.strip()] if blocklist_raw else []

    parsed_url = urlparse(raw_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="Only http and https URLs are supported.")
    if not parsed_url.hostname or _is_private_host(parsed_url.hostname):
        raise HTTPException(status_code=400, detail="URL host is not allowed.")
    safe_url = parsed_url.geturl()

    try:
        remote_cal = requests.get(safe_url, timeout=REQUEST_TIMEOUT).text
    except requests.Timeout:
        raise HTTPException(status_code=400,
                            detail=f"Timed out while getting the URL, timeout is {REQUEST_TIMEOUT} seconds.")
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail="Error getting the URL.")
    except:
        raise HTTPException(status_code=500, detail="Error while fetching calendar contents.")

    try:
        cal = Calendar(remote_cal)
    except ValueError:
        raise HTTPException(status_code=400, detail="Server response not a valid ics format.")
    except Exception:
        raise HTTPException(status_code=500, detail="Error while parsing calendar contents.")

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
    return Response(content=str(cal), media_type="text/calendar")
