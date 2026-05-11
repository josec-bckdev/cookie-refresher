"""VtrackHttpGateway — delivers cookies to the vtrack FastAPI service."""
import logging
import httpx

from cookie_refresher.domain.entities import SessionCookies
from cookie_refresher.domain.ports import IVtrackGateway

logger = logging.getLogger(__name__)


class VtrackHttpGateway(IVtrackGateway):
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def post_cookies(self, cookies: SessionCookies) -> bool:
        payload = {
            "cf_clearance": cookies.cf_clearance,
            "ci_session": cookies.ci_session,
        }
        try:
            response = await self._client.post("/session/set-cookies", json=payload)
            response.raise_for_status()
            logger.info(
                "Cookies posted to vtrack — status %d, session_valid=%s",
                response.status_code,
                response.json().get("session_valid"),
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.error("vtrack rejected cookies: %s %s", exc.response.status_code, exc.response.text)
            return False
        except httpx.RequestError as exc:
            logger.error("Network error posting cookies to vtrack: %s", exc)
            return False

    async def close(self) -> None:
        await self._client.aclose()
