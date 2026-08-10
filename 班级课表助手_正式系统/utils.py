"""共享工具：统一 JSON 响应、鉴权装饰器、当前上下文、审计日志、邀请码摘要。

密码哈希统一使用 werkzeug.security.generate_password_hash / check_password_hash
（由各路由模块自行调用）；邀请码明文只在本模块生成，数据库只存 sha256 摘要。
"""
import hashlib
import json
import secrets
from functools import wraps

from flask import g, jsonify, session

from db import get_db


# ---------------------------------------------------------------------------
# 统一响应
# ---------------------------------------------------------------------------

def json_ok(data=None, status=200):
    """成功响应：JSON body + 状态码（200/201/204 等）。"""
    resp = jsonify(data if data is not None else {})
    resp.status_code = status
    return resp


def json_err(code, message, status=400, details=None):
    """错误响应：固定 {"code", "message", "details"} 结构。"""
    resp = jsonify({
        'code': code,
        'message': message,
        'details': details if details is not None else {},
    })
    resp.status_code = status
    return resp


# ---------------------------------------------------------------------------
# 当前上下文
# ---------------------------------------------------------------------------

def current_user():
    """session 中的用户行（sqlite3.Row）或 None；请求内缓存。"""
    if 'user' in g:
        return g.user
    user_id = session.get('user_id')
    if not user_id:
        return None
    row = get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if row is None:  # session 里的账号已被删除，清理会话
        session.clear()
        return None
    g.user = row
    return row


def current_semester():
    """status='active' 的学期行或 None；请求内缓存。"""
    if 'semester' not in g:
        g.semester = get_db().execute(
            "SELECT * FROM semesters WHERE status = 'active'"
        ).fetchone()
    return g.semester


# ---------------------------------------------------------------------------
# 鉴权装饰器
# ---------------------------------------------------------------------------

def require_roles(*roles):
    """要求已登录且角色匹配。

    未登录返回 401 AUTH_REQUIRED；账号被停用/未激活或角色不符返回 403 FORBIDDEN；
    通过时把 users 表当前行放入 g.user。
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user_id = session.get('user_id')
            if not user_id:
                return json_err('AUTH_REQUIRED', '请先登录', 401)
            row = get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            if row is None:
                session.clear()
                return json_err('AUTH_REQUIRED', '请先登录', 401)
            if row['status'] != 'active':
                return json_err('FORBIDDEN', '账号未激活或已被停用', 403)
            if roles and row['role'] not in roles:
                return json_err('FORBIDDEN', '无权限执行此操作', 403)
            g.user = row
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------

def audit(action, target_type, target_id=None, class_id=None, result='success', detail=None):
    """写 audit_logs。

    不自行 commit，随调用方的业务事务一起提交或回滚。
    禁止在 detail 中放入密码、密码哈希或邀请码明文。
    """
    actor_id = g.user['id'] if g.get('user') is not None else session.get('user_id')
    get_db().execute(
        'INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, class_id, result, detail_json)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            actor_id,
            action,
            target_type,
            target_id,
            class_id,
            result,
            json.dumps(detail, ensure_ascii=False) if detail is not None else None,
        ),
    )


# ---------------------------------------------------------------------------
# 邀请码
# ---------------------------------------------------------------------------

def generate_invite_code():
    """生成邀请码明文（密码学安全随机数）；明文只允许一次性展示，禁止落库。"""
    return secrets.token_urlsafe(8)


def hash_invite_code(plain):
    """邀请码明文 → sha256 摘要（数据库只保存该值）。"""
    return hashlib.sha256(plain.encode('utf-8')).hexdigest()
