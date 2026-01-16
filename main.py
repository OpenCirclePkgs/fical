from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
import requests
from ics import Calendar
from ics.parse import ParseError
import base64

app = FastAPI()
REQUEST_TIMEOUT = 30


@app.get("/")
def index():
    return FileResponse('index.html')


@app.get("/calendar/{b64url}/{b64allowlist}/filtered.ics")
@app.get("/calendar/{b64url}/{b64allowlist}/{b64blocklist}/filtered.ics")
async def get_calendar(b64url: str, b64allowlist: str, b64blocklist: str = ""):
    try:
        url = base64.urlsafe_b64decode(b64url).decode("utf-8")
        allowlist = [w.strip() for w in base64.urlsafe_b64decode(b64allowlist).decode("utf-8").split(",") if w.strip()]
        blocklist = []
        if b64blocklist:
            blocklist = [w.strip() for w in base64.urlsafe_b64decode(b64blocklist).decode("utf-8").split(",") if w.strip()]
    except:
        raise HTTPException(status_code=400, detail="Invalid base64 data.")

    try:
        remote_cal = requests.get(url, timeout=REQUEST_TIMEOUT).text
    except requests.Timeout:
        raise HTTPException(status_code=400,
                            detail=f"Timed out while getting the URL, timeout is {REQUEST_TIMEOUT} seconds.")
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail="Error getting the URL.")
    except:
        raise HTTPException(status_code=500, detail="Error while fetching calendar contents.")

    try:
        cal = Calendar(remote_cal)
    except ParseError:
        raise HTTPException(status_code=400, detail="Server response not a valid ics format.")
    except:
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
