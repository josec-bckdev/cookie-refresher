"""RED: VncBrowserGateway.get_cookies calls GET /cookies?names=... and parses result."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cookie_refresher.adapters.gateways.vnc_browser import VncBrowserGateway


def _make_gateway(response_json: dict) -> VncBrowserGateway:
    with patch("cookie_refresher.adapters.gateways.vnc_browser.docker"):
        gw = VncBrowserGateway("http://vnc:8080", "vnc_browser")
    mock_response = MagicMock()
    mock_response.json.return_value = response_json
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    gw._client = mock_client
    return gw


@pytest.mark.asyncio
class TestVncBrowserGetCookies:
    async def test_calls_get_cookies_endpoint(self):
        gw = _make_gateway({"cf_clearance": "cf_val", "ci_session": "ci_val"})
        await gw.get_cookies(["cf_clearance", "ci_session"])
        gw._client.get.assert_awaited_once()
        url = gw._client.get.await_args[0][0]
        assert "/cookies" in url

    async def test_passes_names_as_query_param(self):
        gw = _make_gateway({"cf_clearance": "cf_val", "ci_session": "ci_val"})
        await gw.get_cookies(["cf_clearance", "ci_session"])
        call_kwargs = gw._client.get.await_args[1]
        params = call_kwargs.get("params", {})
        assert "names" in params

    async def test_returns_session_cookies(self):
        gw = _make_gateway({"cf_clearance": "cf_val", "ci_session": "ci_val"})
        result = await gw.get_cookies(["cf_clearance", "ci_session"])
        assert result.cf_clearance == "cf_val"
        assert result.ci_session == "ci_val"

    async def test_raises_on_http_error(self):
        with patch("cookie_refresher.adapters.gateways.vnc_browser.docker"):
            gw = VncBrowserGateway("http://vnc:8080", "vnc_browser")
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("500 Server Error")
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        gw._client = mock_client
        with pytest.raises(Exception):
            await gw.get_cookies(["cf_clearance", "ci_session"])
