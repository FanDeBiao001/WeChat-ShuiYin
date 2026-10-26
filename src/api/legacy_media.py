"""Short-lived, signed media links for the existing WeChat mini-program."""

import os
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlparse

import requests
from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from src.utils.media_transport import media_session


bp = Blueprint('legacy_media', __name__)
MAX_AGE_SECONDS = 30 * 60


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='qingying-media-v1')


def media_link(url, title, kind):
    if not url:
        return ''
    token = _signer().dumps({'url': url, 'title': title[:100], 'kind': kind})
    base = os.getenv('RENDER_EXTERNAL_URL') or request.host_url.rstrip('/')
    route = 'download' if kind == 'download' else 'media'
    return f'{base.rstrip("/")}/api/{route}?token={token}'


def _public_https_url(url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        if parsed.port not in (None, 443):
            return False
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(item[4][0]).is_global for item in addresses
        )
    except (OSError, ValueError):
        return False


def _stream_token(token, expected_kind):
    started = time.monotonic()
    try:
        payload = _signer().loads(token, max_age=MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return jsonify({'detail': {'message': '链接已失效，请重新解析'}}), 422

    url = payload.get('url') or ''
    parsed = urlparse(url)
    if not _public_https_url(url):
        return jsonify({'detail': {'message': '资源地址无效'}}), 422
    if expected_kind == 'download' and payload.get('kind') != 'download':
        return jsonify({'detail': {'message': '资源类型不匹配'}}), 422

    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': f'{parsed.scheme}://{parsed.hostname}/',
        'Accept-Encoding': 'identity',
    }
    if request.headers.get('Range'):
        headers['Range'] = request.headers['Range']
    upstream = None
    transport = media_session()
    try:
        for _ in range(5):
            upstream = transport.request(
                'HEAD' if request.method == 'HEAD' else 'GET', url,
                headers=headers, stream=True, timeout=(3, 15), allow_redirects=False,
            )
            if upstream.status_code not in (301, 302, 303, 307, 308):
                break
            next_url = urljoin(url, upstream.headers.get('Location', ''))
            upstream.close()
            if not _public_https_url(next_url):
                return jsonify({'detail': {'message': '资源跳转地址无效'}}), 502
            url = next_url
        else:
            return jsonify({'detail': {'message': '资源跳转次数过多'}}), 502
        if upstream.status_code == 416:
            range_header = upstream.headers.get('Content-Range', '')
            upstream.close()
            return Response(status=416, headers={'Content-Range': range_header, 'Cache-Control': 'no-store'})
        upstream.raise_for_status()
        content_type = upstream.headers.get('Content-Type', '').split(';')[0].lower()
        if content_type and not (content_type.startswith(('video/', 'image/', 'audio/')) or content_type == 'application/octet-stream'):
            upstream.close()
            return jsonify({'detail': {'message': '素材服务返回了无效文件，请重新解析'}}), 502
        if int(upstream.headers.get('Content-Length', 0)) > 200 * 1024 * 1024:
            upstream.close()
            return jsonify({'detail': {'message': '素材超过200MB，暂不支持保存'}}), 413
    except (requests.RequestException, ValueError):
        if upstream is not None:
            upstream.close()
        return jsonify({'detail': {'message': '资源暂时无法读取，请重新解析'}}), 502

    def chunks():
        transferred = 0
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                transferred += len(chunk)
                if time.monotonic() - started > 180 or transferred > 200 * 1024 * 1024:
                    raise requests.Timeout('Media transfer budget exceeded')
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            transport.cookies.clear()

    response_headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff', 'Server-Timing': f'upstream;dur={int((time.monotonic() - started) * 1000)}'}
    for name in ('Content-Length', 'Content-Range', 'Accept-Ranges'):
        if upstream.headers.get(name):
            response_headers[name] = upstream.headers[name]
    if expected_kind == 'download':
        response_headers['Content-Disposition'] = 'attachment; filename="video.mp4"'
    content_type = upstream.headers.get('Content-Type') or (
        'image/jpeg' if payload.get('kind') == 'image' else 'video/mp4'
    )
    response = Response(
        stream_with_context(chunks()) if request.method != 'HEAD' else None,
        status=upstream.status_code,
        content_type=content_type,
        headers=response_headers,
    )
    # Also closes upstream if the client disconnects before the first chunk.
    response.call_on_close(upstream.close)
    if request.method == 'HEAD':
        upstream.close()
    return response


@bp.get('/media')
def media():
    return _stream_token(request.args.get('token', ''), 'media')


@bp.get('/download')
def download():
    return _stream_token(request.args.get('token', ''), 'download')
