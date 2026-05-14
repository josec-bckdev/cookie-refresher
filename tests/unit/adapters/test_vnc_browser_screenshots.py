"""RED: VncBrowserGateway saves screenshots to disk when screenshots_dir is configured."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cookie_refresher.adapters.gateways.vnc_browser import VncBrowserGateway

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def _make_gateway(tmp_path) -> VncBrowserGateway:
    with patch("cookie_refresher.adapters.gateways.vnc_browser.docker"):
        gw = VncBrowserGateway("http://vnc:8080", "vnc_browser", screenshots_dir=str(tmp_path))
    mock_response = MagicMock()
    mock_response.content = FAKE_PNG
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    gw._client = mock_client
    return gw


class TestVncBrowserScreenshots:
    async def test_screenshot_saved_when_dir_configured(self, tmp_path):
        gw = _make_gateway(tmp_path)
        await gw.take_screenshot()
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ".png"

    async def test_screenshot_content_matches_response(self, tmp_path):
        gw = _make_gateway(tmp_path)
        await gw.take_screenshot()
        saved = next(tmp_path.iterdir()).read_bytes()
        assert saved == FAKE_PNG

    async def test_multiple_screenshots_create_separate_files(self, tmp_path):
        gw = _make_gateway(tmp_path)
        await gw.take_screenshot()
        await gw.take_screenshot()
        assert len(list(tmp_path.iterdir())) == 2

    async def test_no_file_saved_when_dir_not_configured(self, tmp_path):
        with patch("cookie_refresher.adapters.gateways.vnc_browser.docker"):
            gw = VncBrowserGateway("http://vnc:8080", "vnc_browser", screenshots_dir=None)
        mock_response = MagicMock()
        mock_response.content = FAKE_PNG
        mock_response.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        gw._client = mock_client
        await gw.take_screenshot()
        assert list(tmp_path.iterdir()) == []

    async def test_returns_screenshot_bytes_regardless_of_dir(self, tmp_path):
        gw = _make_gateway(tmp_path)
        result = await gw.take_screenshot()
        assert result == FAKE_PNG
