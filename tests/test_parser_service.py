import unittest
from unittest.mock import AsyncMock, Mock

from starlette.requests import Request

from server.parsers.base import ParsedVideo
from server.services.parser_service import ParserService
from server.utils.exceptions import ParseError


def request_stub() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/parse",
        "scheme": "https",
        "server": ("example.com", 443),
        "headers": [(b"host", b"example.com")],
    })


class ParserServiceCases(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = ParserService()
        self.service.url_service.extract_and_resolve = AsyncMock(
            return_value=("https://example.com/share", "https://www.douyin.com/video/123")
        )
        self.service.url_service.detect_platform = Mock(return_value="douyin")
        self.native_error = ParseError("VIDEO_DETAIL_UNAVAILABLE", "无法获取详情", status_code=422)
        self.service.parsers["douyin"].parse = AsyncMock(side_effect=self.native_error)

    async def test_auto_uses_configured_fallback_after_native_failure(self):
        self.service.third_party_service.is_configured = Mock(return_value=True)
        self.service.third_party_service.parse = AsyncMock(return_value=ParsedVideo(
            platform="douyin",
            platform_label="抖音",
            title="测试视频",
            author="作者",
            cover_url="",
            video_url="https://media.example.com/video.mp4",
            raw_url="https://example.com/share",
            resolved_url="https://www.douyin.com/video/123",
            watermark_video_url="https://media.example.com/video.mp4",
            no_watermark_video_url="https://media.example.com/video.mp4",
        ))

        result = await self.service.parse("https://example.com/share", request_stub(), mode="auto")

        self.assertEqual(result.parse_source, "fallback")
        self.assertTrue(result.no_watermark_verified)
        self.service.third_party_service.parse.assert_awaited_once()

    async def test_auto_preserves_native_error_if_fallback_fails(self):
        self.service.third_party_service.is_configured = Mock(return_value=True)
        self.service.third_party_service.parse = AsyncMock(return_value=None)

        with self.assertRaises(ParseError) as caught:
            await self.service.parse("https://example.com/share", request_stub(), mode="auto")

        self.assertIs(caught.exception, self.native_error)

    async def test_native_never_calls_third_party(self):
        self.service.third_party_service.parse = AsyncMock()

        with self.assertRaises(ParseError):
            await self.service.parse("https://example.com/share", request_stub(), mode="native")

        self.service.third_party_service.parse.assert_not_awaited()
