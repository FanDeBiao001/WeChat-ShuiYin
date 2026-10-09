from flask import Blueprint, redirect, render_template, session, url_for
from src.db import get_db, setting

bp = Blueprint('web', __name__)


@bp.route('/')
def index():
    """前台展示页面（Landing Page）"""
    if setting('homepage_enabled', '1') != '1':
        user_id = session.get('user_id')
        if user_id:
            user = get_db().execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
            if user:
                if user['role'] == 'admin':
                    return redirect(url_for('admin.overview'))
                return redirect(url_for('portal.overview'))
        return redirect(url_for('auth.login'))

    api_enabled = setting('global_api_enabled', '1') == '1'
    demo_enabled = setting('demo_enabled', '1') == '1'
    return render_template(
        'landing.html',
        api_enabled=api_enabled,
        demo_enabled=demo_enabled,
        parser_enabled=api_enabled and demo_enabled,
    )
