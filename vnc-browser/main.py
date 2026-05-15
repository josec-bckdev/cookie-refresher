"""
VNC Browser API — headful Chromium controlled via xdotool + scrot.

Every endpoint is a thin shell command. No WebDriver, no CDP.
Cloudflare sees a plain desktop browser — because it is one.
"""
import asyncio
import json as _json
import os
import subprocess
import time
import logging
import urllib.request
from pathlib import Path

import websockets
from fastapi import FastAPI, Response
from pydantic import BaseModel

DISPLAY = os.getenv("DISPLAY", ":99")
os.environ["DISPLAY"] = DISPLAY

# Debian bullseye: binary is "chromium" (Ubuntu 22.04 snap version won't start in Docker)
CHROMIUM_BIN = os.getenv("CHROMIUM_BIN", "chromium")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] vnc-browser — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="VNC Browser API")

_ENV = {**os.environ, "DISPLAY": DISPLAY}


def _sh(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=_ENV, capture_output=True, text=True, timeout=timeout)


def _chromium_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-x", CHROMIUM_BIN], capture_output=True, text=True
    )
    return result.returncode == 0


# ── startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _launch_browser() -> None:
    logger.info("Launching %s on display %s", CHROMIUM_BIN, DISPLAY)
    subprocess.Popen(
        [
            CHROMIUM_BIN,
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-software-rasterizer",
            "--window-size=1280,900",
            "--remote-debugging-port=9222",
            "about:blank",
        ],
        env=_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Use asyncio.sleep — time.sleep in async blocks the event loop
    for attempt in range(10):
        await asyncio.sleep(1)
        if _chromium_running():
            logger.info("Chromium ready (after %ds)", attempt + 1)
            await asyncio.sleep(2)  # let first paint complete
            return
    logger.error("Chromium did not start after 10s — screenshots will be blank")


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    browser_up = _chromium_running()
    return {
        "status": "ok" if browser_up else "degraded",
        "display": DISPLAY,
        "browser": "running" if browser_up else "not running",
        "binary": CHROMIUM_BIN,
    }


@app.get("/screenshot")
async def screenshot():
    # Do NOT use NamedTemporaryFile — scrot 1.x refuses to overwrite an existing file.
    path = f"/tmp/scr_{int(time.time() * 1000)}.png"
    # -o = overwrite if it somehow exists; no --silent so errors appear in stderr
    result = _sh(["scrot", "-o", path])
    if result.returncode != 0:
        logger.error("scrot failed (rc=%d) stderr=%r", result.returncode, result.stderr)
    img_path = Path(path)
    size = img_path.stat().st_size if img_path.exists() else 0
    if size == 0:
        logger.error("Screenshot is empty (size=0) — browser may not be running")
        return Response(status_code=503, content=b"screenshot unavailable")
    data = img_path.read_bytes()
    img_path.unlink(missing_ok=True)
    logger.debug("Screenshot captured: %d bytes", len(data))
    return Response(content=data, media_type="image/png")


@app.get("/debug/screenshot")
async def debug_screenshot():
    """Returns the current screen as a PNG you can open in a browser tab for inspection."""
    return await screenshot()


class _XY(BaseModel):
    x: int
    y: int


class _Text(BaseModel):
    text: str


class _Key(BaseModel):
    key: str


class _Nav(BaseModel):
    url: str
    wait_seconds: float = 4.0


class _Scroll(BaseModel):
    x: int
    y: int
    direction: str = "down"
    amount: int = 3


class _Drag(BaseModel):
    start_x: int
    start_y: int
    end_x: int
    end_y: int


@app.post("/mouse/click")
async def click(body: _XY):
    logger.info("click (%d, %d)", body.x, body.y)
    _sh(["xdotool", "mousemove", str(body.x), str(body.y)])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "click", "1"])
    await asyncio.sleep(0.3)
    return {"ok": True}


@app.post("/mouse/double_click")
async def double_click(body: _XY):
    logger.info("double_click (%d, %d)", body.x, body.y)
    _sh(["xdotool", "mousemove", str(body.x), str(body.y)])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "click", "--repeat", "2", "--delay", "100", "1"])
    await asyncio.sleep(0.3)
    return {"ok": True}


@app.post("/mouse/triple_click")
async def triple_click(body: _XY):
    logger.info("triple_click (%d, %d)", body.x, body.y)
    _sh(["xdotool", "mousemove", str(body.x), str(body.y)])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "click", "--repeat", "3", "--delay", "80", "1"])
    await asyncio.sleep(0.3)
    return {"ok": True}


@app.post("/mouse/right_click")
async def right_click(body: _XY):
    logger.info("right_click (%d, %d)", body.x, body.y)
    _sh(["xdotool", "mousemove", str(body.x), str(body.y)])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "click", "3"])
    await asyncio.sleep(0.3)
    return {"ok": True}


@app.post("/mouse/drag")
async def drag(body: _Drag):
    logger.info("drag (%d,%d)→(%d,%d)", body.start_x, body.start_y, body.end_x, body.end_y)
    _sh(["xdotool", "mousemove", str(body.start_x), str(body.start_y)])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "mousedown", "1"])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "mousemove", str(body.end_x), str(body.end_y)])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "mouseup", "1"])
    await asyncio.sleep(0.2)
    return {"ok": True}


@app.post("/keyboard/type")
async def type_text(body: _Text):
    preview = body.text[:30] + "…" if len(body.text) > 30 else body.text
    logger.info("type: %r", preview)
    _sh(["xdotool", "type", "--clearmodifiers", "--delay", "80", body.text])
    await asyncio.sleep(0.2)
    return {"ok": True}


@app.post("/keyboard/key")
async def press_key(body: _Key):
    logger.info("key: %s", body.key)
    _sh(["xdotool", "key", body.key])
    await asyncio.sleep(0.3)
    return {"ok": True}


@app.post("/navigate")
async def navigate(body: _Nav):
    logger.info("navigate → %s (wait=%.1fs)", body.url, body.wait_seconds)
    _sh(["xdotool", "key", "ctrl+l"])
    await asyncio.sleep(0.4)
    _sh(["xdotool", "key", "ctrl+a"])
    await asyncio.sleep(0.1)
    _sh(["xdotool", "type", "--clearmodifiers", body.url])
    await asyncio.sleep(0.2)
    _sh(["xdotool", "key", "Return"])
    # Wait for page load — longer for first navigation (Cloudflare challenge)
    await asyncio.sleep(body.wait_seconds)
    return {"ok": True}


@app.post("/scroll")
async def scroll(body: _Scroll):
    logger.info("scroll (%d, %d) %s ×%d", body.x, body.y, body.direction, body.amount)
    button = "4" if body.direction == "up" else "5"
    _sh(["xdotool", "mousemove", str(body.x), str(body.y)])
    for _ in range(body.amount):
        _sh(["xdotool", "click", button])
        await asyncio.sleep(0.05)
    return {"ok": True}


@app.get("/cookies")
async def get_cookies(names: str = "cf_clearance,ci_session"):
    """Read named cookies from Chromium's cookie jar via CDP."""
    name_list = [n.strip() for n in names.split(",") if n.strip()]

    # Discover the first page target's CDP WebSocket URL
    try:
        with urllib.request.urlopen("http://localhost:9222/json", timeout=5) as resp:
            targets = _json.loads(resp.read())
    except Exception as exc:
        logger.error("CDP unreachable: %s", exc)
        return Response(status_code=503, content=f"CDP unavailable: {exc}".encode())

    page = next((t for t in targets if t.get("type") == "page"), None)
    if not page:
        logger.error("No page target found in CDP")
        return Response(status_code=503, content=b"No page target found")

    ws_url = page["webSocketDebuggerUrl"]
    logger.debug("CDP target: %s", ws_url)

    # Fetch all cookies from the page context
    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(_json.dumps({"id": 1, "method": "Network.getAllCookies"}))
            raw = await ws.recv()
    except Exception as exc:
        logger.error("CDP WebSocket error: %s", exc)
        return Response(status_code=503, content=f"CDP error: {exc}".encode())

    result = _json.loads(raw)
    all_cookies = {c["name"]: c["value"] for c in result["result"]["cookies"]}

    missing = [n for n in name_list if n not in all_cookies]
    if missing:
        logger.warning("Cookies not found: %s", missing)
        return Response(status_code=404, content=f"Missing cookies: {missing}".encode())

    logger.info("Returning cookies: %s", name_list)
    return {name: all_cookies[name] for name in name_list}
