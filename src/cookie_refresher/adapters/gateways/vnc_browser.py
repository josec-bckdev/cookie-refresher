"""
VncBrowserGateway — implements IBrowserGateway via HTTP calls to the VNC sandbox.

Lifecycle: start() spins up the Docker container and polls /health until Chromium
is ready. close() stops the container and releases the httpx client. The container
is idle (stopped) between refresh cycles — it only consumes resources while the
ReAct loop is running.

Expected VNC container endpoints:
  GET  /health              → {"status": "ok", "browser": "running"}
  GET  /screenshot          → PNG bytes
  POST /mouse/click         → {"x": int, "y": int}
  POST /mouse/double_click  → {"x": int, "y": int}
  POST /mouse/right_click   → {"x": int, "y": int}
  POST /mouse/drag          → {"start_x": int, "start_y": int, "end_x": int, "end_y": int}
  POST /keyboard/type       → {"text": str}
  POST /keyboard/key        → {"key": str}
  POST /navigate            → {"url": str, "wait_seconds": float}
  POST /scroll              → {"x": int, "y": int, "direction": str, "amount": int}
  GET  /cookies             → {"cf_clearance": str, "ci_session": str}
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import docker  # type: ignore
import docker.errors  # type: ignore
from opentelemetry import trace

from cookie_refresher.domain.ports import IBrowserGateway

logger = logging.getLogger(__name__)

_HEALTH_POLL_INTERVAL = 2.0
_HEALTH_TIMEOUT = 60.0


class VncBrowserGateway(IBrowserGateway):
    def __init__(self, base_url: str, container_name: str, timeout: float = 30.0, screenshots_dir: Optional[str] = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._container_name = container_name
        self._timeout = timeout
        self._screenshots_dir = screenshots_dir
        self._client: httpx.AsyncClient | None = None
        self._docker = docker.from_env()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        logger.info("Starting VNC browser container: %s", self._container_name)
        try:
            container = self._docker.containers.get(self._container_name)
        except docker.errors.NotFound:
            raise RuntimeError(
                f"VNC browser container '{self._container_name}' not found. "
                "Run: docker compose --profile on-demand up --build --no-start"
            )

        if container.status != "running":
            try:
                container.start()
            except docker.errors.NotFound as exc:
                # Stale container whose network was deleted (e.g. after `docker compose down`).
                # Remove it so the operator can recreate with `docker compose ... up --no-start`.
                logger.warning(
                    "Container '%s' has a missing network — removing stale container: %s",
                    self._container_name,
                    exc,
                )
                try:
                    container.remove(force=True)
                except Exception as remove_exc:
                    logger.warning("Failed to remove stale container: %s", remove_exc)
                raise RuntimeError(
                    f"VNC browser container '{self._container_name}' had a stale network and was removed. "
                    "Recreate it with: docker compose --profile on-demand up --build --no-start"
                ) from exc
            logger.info("Container started — waiting for Chromium to be ready")
        else:
            logger.info("Container already running — waiting for health check")

        await self._wait_for_health()
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        logger.info("VNC browser ready")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Stopping VNC browser container: %s", self._container_name)
        try:
            container = self._docker.containers.get(self._container_name)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.stop(timeout=10))
            logger.info("VNC browser container stopped")
        except docker.errors.NotFound:
            logger.warning("Container '%s' not found during stop — already gone", self._container_name)
        except Exception as exc:
            logger.warning("Failed to stop VNC browser container: %s", exc)

    async def _wait_for_health(self) -> None:
        deadline = asyncio.get_event_loop().time() + _HEALTH_TIMEOUT
        async with httpx.AsyncClient(base_url=self._base_url) as probe:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    resp = await probe.get("/health", timeout=3.0)
                    if resp.status_code == 200 and resp.json().get("browser") == "running":
                        return
                except Exception:
                    pass
                await asyncio.sleep(_HEALTH_POLL_INTERVAL)
        raise RuntimeError(
            f"VNC browser container did not become healthy within {_HEALTH_TIMEOUT}s"
        )

    # ── browser actions ───────────────────────────────────────────────────────

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("VncBrowserGateway.start() must be called before using the browser")
        return self._client

    async def navigate(self, url: str, wait_seconds: float = 6.0) -> None:
        logger.debug("Browser navigate → %s (wait=%.1fs)", url, wait_seconds)
        await self._http().post("/navigate", json={"url": url, "wait_seconds": wait_seconds})

    async def take_screenshot(self) -> bytes:
        response = await self._http().get("/screenshot")
        response.raise_for_status()
        if not response.content:
            raise RuntimeError("VNC browser returned an empty screenshot")
        data = response.content
        if self._screenshots_dir:
            path = self._save_screenshot(data)
            trace.get_current_span().set_attribute("screenshot.path", path)
        return data

    def _save_screenshot(self, data: bytes) -> str:
        dir_path = Path(self._screenshots_dir)  # type: ignore[arg-type]
        dir_path.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = dir_path / f"{ts}.png"
        path.write_bytes(data)
        return str(path)

    async def click(self, x: int, y: int) -> None:
        logger.debug("Browser left_click (%d, %d)", x, y)
        await self._http().post("/mouse/click", json={"x": x, "y": y})

    async def double_click(self, x: int, y: int) -> None:
        logger.debug("Browser double_click (%d, %d)", x, y)
        await self._http().post("/mouse/double_click", json={"x": x, "y": y})

    async def triple_click(self, x: int, y: int) -> None:
        logger.debug("Browser triple_click (%d, %d)", x, y)
        await self._http().post("/mouse/triple_click", json={"x": x, "y": y})

    async def type_text(self, text: str) -> None:
        logger.debug("Browser type: %r", text[:20] + "…" if len(text) > 20 else text)
        await self._http().post("/keyboard/type", json={"text": text})

    async def press_key(self, key: str) -> None:
        logger.debug("Browser key: %s", key)
        await self._http().post("/keyboard/key", json={"key": key})

    async def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        logger.debug("Browser scroll (%d, %d) %s ×%d", x, y, direction, amount)
        await self._http().post(
            "/scroll", json={"x": x, "y": y, "direction": direction, "amount": amount}
        )

    async def right_click(self, x: int, y: int) -> None:
        logger.debug("Browser right_click (%d, %d)", x, y)
        await self._http().post("/mouse/right_click", json={"x": x, "y": y})

    async def left_click_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        logger.debug("Browser left_click_drag (%d,%d)→(%d,%d)", start_x, start_y, end_x, end_y)
        await self._http().post(
            "/mouse/drag",
            json={"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y},
        )

    async def get_cookies(self, names: list[str]) -> "SessionCookies":
        from cookie_refresher.domain.entities import SessionCookies
        logger.debug("Browser get_cookies: %s", names)
        response = await self._http().get("/cookies", params={"names": ",".join(names)})
        response.raise_for_status()
        data = response.json()
        return SessionCookies(
            cf_clearance=data["cf_clearance"],
            ci_session=data["ci_session"],
        )