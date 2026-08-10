"""班级课表助手 · Flask 应用工厂。

职责：配置装载、数据库初始化、CSRF 中间件、请求 ID、安全响应头、
统一错误体、静态页面托管、蓝图注册。

运行方式：
    flask --app app run          # Flask CLI 自动识别 create_app 工厂
    python app.py                # 直接运行
"""
import os
import secrets
import uuid

from flask import Flask, g, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

from config import Config, DEV_SECRET_KEY
from db import close_db, init_db
from utils import json_err

CSRF_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
# 登录与学生激活时尚未建立会话，豁免 CSRF 校验
CSRF_EXEMPT_PATHS = {'/api/auth/login', '/api/auth/student-activate'}

_API_ERROR_MAP = {
    400: ('BAD_REQUEST', '请求参数错误'),
    401: ('AUTH_REQUIRED', '请先登录'),
    403: ('FORBIDDEN', '无权限执行此操作'),
    404: ('NOT_FOUND', '资源不存在'),
    405: ('METHOD_NOT_ALLOWED', '请求方法不允许'),
    409: ('CONFLICT', '业务冲突'),
    413: ('PAYLOAD_TOO_LARGE', '请求体过大'),
    429: ('TOO_MANY_REQUESTS', '请求过于频繁，请稍后重试'),
}


def create_app(config_override=None):
    """应用工厂；config_override 可覆盖任意配置项（如测试用 DATABASE 路径）。"""
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config.from_object(Config)
    if config_override:
        app.config.update(config_override)

    if os.environ.get('APP_ENV') == 'production' and app.config['SECRET_KEY'] == DEV_SECRET_KEY:
        raise RuntimeError('生产环境必须通过环境变量 SECRET_KEY 设置独立密钥（见 .env.example）')

    app.json.ensure_ascii = False

    db_dir = os.path.dirname(os.path.abspath(app.config['DATABASE']))
    os.makedirs(db_dir, exist_ok=True)

    app.teardown_appcontext(close_db)
    init_db(app)

    _register_hooks(app)
    _register_pages(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    return app


def _register_hooks(app):
    @app.before_request
    def assign_request_id():
        g.request_id = request.headers.get('X-Request-ID') or uuid.uuid4().hex

    @app.before_request
    def csrf_protect():
        """/api/ 下的写请求统一校验 X-CSRF-Token（登录、学生激活豁免）。"""
        if request.method not in CSRF_METHODS or not request.path.startswith('/api/'):
            return None
        if (request.path.rstrip('/') or '/') in CSRF_EXEMPT_PATHS:
            return None
        expected = session.get('csrf_token')
        provided = request.headers.get('X-CSRF-Token', '')
        if not expected or not provided or not secrets.compare_digest(expected, provided):
            return json_err('CSRF_INVALID', 'CSRF 校验失败，请刷新页面后重试', 403)
        return None

    @app.after_request
    def add_common_headers(resp):
        resp.headers['X-Request-ID'] = g.get('request_id') or ''
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'DENY'
        resp.headers['Referrer-Policy'] = 'same-origin'
        return resp


def _register_pages(app):
    @app.route('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/admin')
    def admin_page():
        return send_from_directory(app.static_folder, 'admin.html')


def _register_blueprints(app):
    # 兄弟模块若尚未就绪，让 ImportError 原样抛出，问题清晰可见
    from routes_auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from routes_system import bp as system_bp
    app.register_blueprint(system_bp)

    import routes_business
    # routes_business 以模块级集合导出全部业务蓝图
    # （/api/schedule、/api/courses、/api/course-sessions、/api/homework、
    #   /api/teachers、/api/classes/me、/api/import）；兼容 BLUEPRINTS / blueprints 两种命名
    blueprints = getattr(routes_business, 'BLUEPRINTS', None) or getattr(routes_business, 'blueprints', None)
    if not blueprints:
        raise RuntimeError('routes_business 必须导出 BLUEPRINTS（或 blueprints）蓝图集合')
    for bp in blueprints:
        app.register_blueprint(bp)


def _register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def handle_http_error(e):
        if request.path.startswith('/api/'):
            code, message = _API_ERROR_MAP.get(e.code, ('HTTP_ERROR', e.name))
            return json_err(code, message, e.code or 500)
        return e

    @app.errorhandler(Exception)
    def handle_unexpected_error(e):
        app.logger.exception('未处理异常 [request_id=%s]', g.get('request_id'))
        return json_err('INTERNAL_ERROR', '服务器内部错误，请稍后重试', 500)


if __name__ == '__main__':
    create_app().run(debug=os.environ.get('FLASK_DEBUG') == '1')
