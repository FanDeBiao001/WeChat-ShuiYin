import os
import unittest
from unittest.mock import patch

os.environ['API_ONLY'] = 'true'

from app import app  # noqa: E402
from src.api.response import make_response  # noqa: E402


class MiniappCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_legacy_parse_wraps_video_in_same_origin_links(self):
        with app.app_context():
            upstream = make_response(200, '成功', {
                'platform': '抖音',
                'title': '示例',
                'video_url': 'https://example.org/video.mp4',
                'cover_url': 'https://example.org/cover.jpg',
                'author': {'nickname': '作者'},
            }, True)
        with patch('src.api.parse._execute_parse', return_value=(upstream, 200)):
            response = self.client.post('/api/parse', json={
                'text': 'https://v.douyin.com/example/', 'mode': 'native',
            })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()['data']
        self.assertTrue(data['preview_video_url'].startswith('http://localhost/api/media?token='))
        self.assertTrue(data['no_watermark_download_url'].startswith('http://localhost/api/download?token='))
        self.assertEqual(data['title'], '示例')

    def test_legacy_parse_preserves_error_message(self):
        with app.app_context():
            upstream = make_response(400, '需要登录', None, False, 'COOKIE_REQUIRED')
        with patch('src.api.parse._execute_parse', return_value=(upstream, 400)):
            response = self.client.post('/api/parse', json={
                'text': 'https://xhslink.cn/example', 'mode': 'native',
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['detail']['message'], '需要登录')


if __name__ == '__main__':
    unittest.main()
