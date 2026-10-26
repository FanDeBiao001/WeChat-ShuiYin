"""WeChat code exchange and revocable, opaque mini-program sessions."""
import hashlib
import secrets
import time

import requests
from flask import Blueprint, current_app, jsonify, request

from src.api.access import consume_rate_limit, get_client_ip
from src.db import get_db

bp = Blueprint('miniapp_auth', __name__)
SESSION_SECONDS = 7 * 86400


def _error(message, status, code):
    return jsonify({'success': False, 'detail': {'code': code, 'message': message}}), status


def _session():
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer ') or not 30 <= len(header[7:]) <= 128:
        return None
    digest = hashlib.sha256(header[7:].encode()).hexdigest()
    return get_db().execute(
        'SELECT token_hash,user_id,expires_at FROM miniapp_sessions WHERE token_hash=? AND expires_at>?',
        (digest, int(time.time())),
    ).fetchone()


@bp.post('/auth/wechat')
def login():
    appid = current_app.config.get('WX_APP_ID')
    secret = current_app.config.get('WX_APP_SECRET')
    if not appid or not secret:
        return _error('微信登录尚未配置，请联系开发者', 503, 'LOGIN_NOT_CONFIGURED')
    body = request.get_json(silent=True)
    code = body.get('code') if isinstance(body, dict) else None
    if not isinstance(code, str) or not code.strip() or len(code) > 256:
        return _error('微信登录凭证无效，请重新登录', 400, 'INVALID_CODE')
    if not consume_rate_limit(f'miniapp_login:{get_client_ip()}', 10, window_seconds=60):
        return _error('登录过于频繁，请稍后重试', 429, 'LOGIN_RATE_LIMITED')
    try:
        response = requests.get('https://api.weixin.qq.com/sns/jscode2session', params={
            'appid': appid, 'secret': secret, 'js_code': code, 'grant_type': 'authorization_code',
        }, timeout=(3, 8))
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        # Never log exception URLs: WeChat's query includes the AppSecret and code.
        return _error('微信登录服务暂不可用，请稍后重试', 502, 'WECHAT_UNAVAILABLE')
    if not isinstance(data, dict) or data.get('errcode') or not isinstance(data.get('openid'), str) or not data['openid']:
        return _error('微信登录凭证已失效，请重试', 401, 'WECHAT_CODE_REJECTED')
    # Stable across restarts and SECRET_KEY rotations; do not return openid/session_key.
    user_id = hashlib.sha256(f"wechat:{appid}:{data['openid']}".encode()).hexdigest()[:32]
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_SECONDS
    db = get_db()
    db.execute('DELETE FROM miniapp_sessions WHERE expires_at<=?', (int(time.time()),))
    db.execute('INSERT INTO miniapp_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
               (hashlib.sha256(token.encode()).hexdigest(), user_id, expires_at))
    db.commit()
    response = jsonify({'success': True, 'data': {'token': token, 'expires_at': expires_at, 'user': {'id': user_id}}})
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.get('/auth/me')
def me():
    session = _session()
    if not session:
        return _error('登录已过期，请重新登录', 401, 'SESSION_EXPIRED')
    response = jsonify({'success': True, 'data': {'user': {'id': session['user_id']}, 'expires_at': session['expires_at']}})
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.post('/auth/logout')
def logout():
    session = _session()
    if session:
        db = get_db()
        db.execute('DELETE FROM miniapp_sessions WHERE token_hash=?', (session['token_hash'],))
        db.commit()
    return jsonify({'success': True})
