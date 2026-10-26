import hashlib
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import requests

os.environ['API_ONLY'] = 'true'
from app import create_app
from src.db import get_db
from src.api.legacy_media import _signer
from src.api.response import make_response
from src.utils.parse_cache import ParseCache, BusyError, WaitTimeout


class AppCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'API_ONLY': True, 'SECRET_KEY': 'test-only',
                               'DATABASE': os.path.join(self.temp.name, 'test.db'),
                               'WX_APP_ID': 'test-app', 'WX_APP_SECRET': 'test-secret'})
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def login(self, openid='wechat-user-a'):
        remote = Mock()
        remote.json.return_value = {'openid': openid, 'session_key': 'never-return-this'}
        with patch('src.api.miniapp_auth.requests.get', return_value=remote):
            result = self.client.post('/api/auth/wechat', json={'code': 'fresh-code'})
        self.assertEqual(result.status_code, 200)
        return result.get_json()['data']


class AuthCases(AppCase):
    def test_trusted_proxy_clients_have_separate_login_rate_buckets(self):
        self.app.config['TRUST_PROXY_HEADERS'] = True
        remote = Mock()
        remote.json.return_value = {'errcode': 40029}
        with patch('src.api.miniapp_auth.requests.get', return_value=remote):
            for _ in range(10):
                self.client.post('/api/auth/wechat', json={'code': 'code'}, headers={'X-Forwarded-For': '203.0.113.1'})
            self.assertEqual(self.client.post('/api/auth/wechat', json={'code': 'code'}, headers={'X-Forwarded-For': '203.0.113.1'}).status_code, 429)
            self.assertEqual(self.client.post('/api/auth/wechat', json={'code': 'code'}, headers={'X-Forwarded-For': '203.0.113.2'}).status_code, 401)

    def test_login_uses_wechat_code_and_stores_only_token_hash(self):
        data = self.login()
        self.assertEqual(set(data), {'token', 'expires_at', 'user'})
        self.assertNotIn('openid', str(data))
        self.assertNotIn('session_key', str(data))
        with self.app.app_context():
            row = get_db().execute('SELECT * FROM miniapp_sessions').fetchone()
            self.assertEqual(row['token_hash'], hashlib.sha256(data['token'].encode()).hexdigest())
            self.assertNotIn(data['token'], str(dict(row)))
        response = self.client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + data['token']})
        self.assertEqual(response.get_json()['data']['user'], data['user'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_forged_expired_and_revoked_sessions_are_rejected(self):
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        self.assertEqual(self.client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + 'x' * 43}).status_code, 401)
        data = self.login()
        headers = {'Authorization': 'Bearer ' + data['token']}
        self.assertEqual(self.client.post('/api/auth/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me', headers=headers).status_code, 401)
        data = self.login()
        with self.app.app_context():
            get_db().execute('UPDATE miniapp_sessions SET expires_at=0')
            get_db().commit()
        self.assertEqual(self.client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + data['token']}).status_code, 401)

    def test_identity_is_stable_but_different_wechat_users_are_isolated(self):
        first = self.login()
        second = self.login()
        third = self.login('wechat-user-b')
        self.assertEqual(first['user'], second['user'])
        self.assertNotEqual(first['token'], second['token'])
        self.assertNotEqual(first['user'], third['user'])

    def test_missing_config_invalid_input_and_provider_rejection(self):
        with patch('src.api.miniapp_auth.requests.get') as remote:
            self.assertEqual(self.client.post('/api/auth/wechat', json={}).status_code, 400)
            remote.assert_not_called()
            self.app.config['WX_APP_SECRET'] = ''
            self.assertEqual(self.client.post('/api/auth/wechat', json={'code': 'code'}).status_code, 503)
            remote.assert_not_called()
            self.app.config['WX_APP_SECRET'] = 'test-secret'
            remote.return_value.json.return_value = {'errcode': 40029, 'errmsg': 'bad code'}
            self.assertEqual(self.client.post('/api/auth/wechat', json={'code': 'code'}).status_code, 401)

    def test_wechat_timeout_does_not_return_secret_or_accept_client_identity(self):
        with patch('src.api.miniapp_auth.requests.get', side_effect=requests.Timeout('secret-in-url')):
            response = self.client.post('/api/auth/wechat', json={'code': 'code', 'openid': 'forged'})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-in-url', response.text)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM miniapp_sessions').fetchone()[0], 0)

    def test_login_rate_limit_is_shared_in_database(self):
        for _ in range(10):
            self.login()
        self.assertEqual(self.client.post('/api/auth/wechat', json={'code': 'another'}).status_code, 429)


class CacheCases(unittest.TestCase):
    def test_concurrent_same_link_performs_one_parse(self):
        cache = ParseCache()
        barrier = threading.Barrier(4)
        count = 0
        def compute():
            nonlocal count
            count += 1
            time.sleep(0.04)
            return {'succ': True, 'data': {'title': 'test'}}, 200
        def run():
            barrier.wait(timeout=2)
            return cache.run('https://xhslink.cn/o/test?xsec_token=a', compute)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: run(), range(4)))
        self.assertEqual(count, 1)
        self.assertEqual(sum(state == 'miss' for _, state in results), 1)
        self.assertTrue(all(result[0]['succ'] for result, _ in results))

    def test_cache_expires_preserves_query_and_refresh_bypasses(self):
        cache = ParseCache(ttl=10)
        compute = Mock(return_value=({'succ': True}, 200))
        cache.run('url?token=a', compute)
        _, status = cache.run('url?token=a', compute)
        self.assertEqual(status, 'hit')
        cache.run('url?token=b', compute)
        cache.run('url?token=a', compute, refresh=True)
        with patch('src.utils.parse_cache.time.monotonic', return_value=time.monotonic() + 20):
            cache.run('url?token=a', compute)
        self.assertEqual(compute.call_count, 4)

    def test_failure_is_not_cached_and_capacity_is_bounded(self):
        cache = ParseCache(capacity=2)
        failed = Mock(return_value=({'succ': False}, 502))
        cache.run('error', failed); cache.run('error', failed)
        self.assertEqual(failed.call_count, 2)
        for index in range(3):
            cache.run(str(index), lambda: ({'succ': True}, 200))
        self.assertEqual(len(cache.cache), 2)

    def test_busy_and_wait_timeout_do_not_start_more_upstream_work(self):
        cache = ParseCache(concurrent=1)
        entered = threading.Event()
        release = threading.Event()
        def compute():
            entered.set()
            release.wait(2)
            return {'succ': True}, 200
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(cache.run, 'a', compute)
            self.assertTrue(entered.wait(1))
            try:
                with self.assertRaises(BusyError): cache.run('b', compute)
                with self.assertRaises(WaitTimeout): cache.run('a', compute, wait_seconds=0.005)
            finally:
                release.set()
            future.result(timeout=2)
        self.assertEqual(cache.pending, {})
        self.assertEqual(cache.run('b', lambda: ({'succ': True}, 200))[1], 'miss')

    def test_exception_releases_slot_and_next_request_recovers(self):
        cache = ParseCache(concurrent=1)
        with self.assertRaises(ValueError):
            cache.run('a', Mock(side_effect=ValueError('bad')))
        self.assertEqual(cache.run('b', lambda: ({'succ': True}, 200))[1], 'miss')


class EndpointCases(AppCase):
    def test_miniapp_cache_reuses_native_data_and_refresh_fetches_again(self):
        with self.app.app_context():
            response = make_response(200, '成功', {'video_url': 'https://media.example.org/v.mp4', 'title': 'title'}, True)
        with patch('src.api.parse._execute_parse', return_value=(response, 200)) as parse:
            one = self.client.post('/api/parse', json={'text': 'https://xhslink.cn/o/test', 'mode': 'native'})
            two = self.client.post('/api/parse', json={'text': 'share https://xhslink.cn/o/test', 'mode': 'native'})
            self.assertEqual(parse.call_count, 1)
            self.assertIn('hit', two.headers['Server-Timing'])
            self.assertEqual(two.get_json()['data']['raw_url'], 'share https://xhslink.cn/o/test')
            self.assertIn('/api/download?token=', one.get_json()['data']['no_watermark_download_url'])
            self.client.post('/api/parse', json={'text': 'https://xhslink.cn/o/test', 'mode': 'native', 'refresh': True})
            self.assertEqual(parse.call_count, 2)

    def test_images_and_description_use_same_origin_signed_links(self):
        with self.app.app_context():
            response = make_response(200, '成功', {'image_list': ['https://media.example.org/a.jpg'], 'desc': '文案'}, True)
        with patch('src.api.parse._execute_parse', return_value=(response, 200)):
            response = self.client.post('/api/parse', json={'text': 'https://xhslink.cn/o/images', 'mode': 'native'})
        data = response.get_json()['data']
        self.assertEqual(data['media_type'], 'images')
        self.assertEqual(data['description'], '文案')
        self.assertIn('/api/media?token=', data['image_list'][0])


class MediaCases(AppCase):
    def token(self, kind='video'):
        with self.app.app_context():
            return _signer().dumps({'url': 'https://media.example.org/v.mp4', 'title': 'video', 'kind': kind})

    def upstream(self, status=200, headers=None):
        response = Mock(status_code=status, headers=headers or {'Content-Type': 'video/mp4', 'Content-Length': '4'})
        response.iter_content.return_value = iter([b'data'])
        return response

    def test_range_headers_and_partial_status_are_preserved(self):
        upstream = self.upstream(206, {'Content-Type': 'video/mp4', 'Content-Length': '4', 'Content-Range': 'bytes 0-3/100', 'Accept-Ranges': 'bytes'})
        transport = Mock(); transport.request.return_value = upstream
        with patch('src.api.legacy_media._public_https_url', return_value=True), patch('src.api.legacy_media.media_session', return_value=transport):
            response = self.client.get('/api/media?token=' + self.token(), headers={'Range': 'bytes=0-3'}, buffered=True)
            self.assertEqual(response.data, b'data')
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.headers['Content-Range'], 'bytes 0-3/100')
            self.assertEqual(transport.request.call_args.kwargs['headers']['Range'], 'bytes=0-3')
            response.close()
        upstream.close.assert_called()

    def test_http_error_and_invalid_file_are_closed(self):
        for upstream in [self.upstream(502), self.upstream(200, {'Content-Type': 'text/html'})]:
            if upstream.status_code == 502:
                upstream.raise_for_status.side_effect = requests.HTTPError('bad')
            transport = Mock(); transport.request.return_value = upstream
            with patch('src.api.legacy_media._public_https_url', return_value=True), patch('src.api.legacy_media.media_session', return_value=transport):
                self.assertEqual(self.client.get('/api/download?token=' + self.token('download')).status_code, 502)
            upstream.close.assert_called()

    def test_head_closes_without_reading_and_range_416_is_preserved(self):
        transport = Mock()
        with patch('src.api.legacy_media._public_https_url', return_value=True), patch('src.api.legacy_media.media_session', return_value=transport):
            upstream = self.upstream(); transport.request.return_value = upstream
            response = self.client.head('/api/media?token=' + self.token())
            self.assertEqual(response.data, b'')
            upstream.iter_content.assert_not_called(); upstream.close.assert_called()
            self.assertEqual(transport.request.call_args.args[0], 'HEAD')
            upstream = self.upstream(416, {'Content-Range': 'bytes */100'}); transport.request.return_value = upstream
            response = self.client.get('/api/media?token=' + self.token())
            self.assertEqual(response.status_code, 416)
            self.assertEqual(response.headers['Content-Range'], 'bytes */100')
            upstream.close.assert_called()

    def test_invalid_token_private_redirect_and_oversized_media_do_not_stream(self):
        self.assertEqual(self.client.get('/api/media?token=forged').status_code, 422)
        upstream = self.upstream(302, {'Location': 'https://127.0.0.1/private'})
        transport = Mock(); transport.request.return_value = upstream
        with patch('src.api.legacy_media._public_https_url', side_effect=[True, False]), patch('src.api.legacy_media.media_session', return_value=transport):
            self.assertEqual(self.client.get('/api/media?token=' + self.token()).status_code, 502)
            self.assertEqual(transport.request.call_count, 1)
        upstream.close.assert_called()
        upstream = self.upstream(200, {'Content-Type': 'video/mp4', 'Content-Length': str(201 * 1024 * 1024)})
        transport.request.return_value = upstream
        with patch('src.api.legacy_media._public_https_url', return_value=True), patch('src.api.legacy_media.media_session', return_value=transport):
            self.assertEqual(self.client.get('/api/media?token=' + self.token()).status_code, 413)
        upstream.close.assert_called()


if __name__ == '__main__':
    unittest.main()
