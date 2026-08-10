"""认证 API：学生激活、登录、退出、会话信息、修改密码。

蓝图前缀 /api/auth。CSRF 由 app.py 统一校验，登录与学生激活两个接口豁免。
"""
import re
import secrets
import time

from flask import Blueprint, g, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db
from utils import (
    current_semester,
    current_user,
    hash_invite_code,
    json_err,
    json_ok,
    require_roles,
)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

STUDENT_NO_RE = re.compile(r"^\d{6,20}$")
MIN_PASSWORD_LEN = 8

# 运行环境（LibreSSL）无 hashlib.scrypt，Werkzeug 默认 scrypt 会报错，统一显式指定 pbkdf2
HASH_METHOD = "pbkdf2:sha256"

# 与 schema 默认时间戳格式一致：带时区 ISO 8601（UTC）
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"

# 简单内存限流：同一 IP + 账号连续失败 5 次锁定 5 分钟
_LOGIN_FAILS = {}
_MAX_FAILS = 5
_LOCK_SECONDS = 300


def _lock_key(login_id):
    return (request.remote_addr or "", login_id)


def _is_locked(key):
    entry = _LOGIN_FAILS.get(key)
    if not entry:
        return False
    _, lock_until = entry
    if lock_until is None:
        return False
    if lock_until > time.time():
        return True
    _LOGIN_FAILS.pop(key, None)
    return False


def _record_fail(key):
    count, _ = _LOGIN_FAILS.get(key, (0, None))
    count += 1
    if count >= _MAX_FAILS:
        _LOGIN_FAILS[key] = (0, time.time() + _LOCK_SECONDS)
    else:
        _LOGIN_FAILS[key] = (count, None)


def _public_user(row):
    return {
        "id": row["id"],
        "login_id": row["login_id"],
        "student_no": row["student_no"],
        "name": row["name"],
        "role": row["role"],
        "status": row["status"],
        "class_id": row["class_id"],
    }


def _class_info(db, class_id):
    if class_id is None:
        return None
    row = db.execute(
        "SELECT id, class_code, class_name, status FROM classes WHERE id = ?",
        (class_id,),
    ).fetchone()
    return dict(row) if row else None


def _semester_info(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "start_date": row["start_date"],
        "week_count": row["week_count"],
        "status": row["status"],
    }


def _start_session(user_row):
    session.clear()
    session["user_id"] = user_row["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)


@bp.post("/student-activate")
def student_activate():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    invite_code = (data.get("invite_code") or "").strip()
    student_no = (data.get("student_no") or "").strip()
    password = data.get("password") or ""

    if not all([name, invite_code, student_no, password]):
        return json_err("VALIDATION_ERROR", "请完整填写注册信息", 400)
    if not STUDENT_NO_RE.fullmatch(student_no):
        return json_err("VALIDATION_ERROR", "学号应为 6-20 位数字", 400)
    if len(password) < MIN_PASSWORD_LEN:
        return json_err("VALIDATION_ERROR", "密码至少 8 位", 400)

    db = get_db()
    code_hash = hash_invite_code(invite_code)
    cls = db.execute(
        "SELECT * FROM classes WHERE invite_code_hash = ? AND status = 'active'",
        (code_hash,),
    ).fetchone()
    if cls is None:
        return json_err("INVITE_CODE_INVALID", "班级邀请码无效，请向班级管理员确认", 400)

    user = db.execute(
        "SELECT * FROM users WHERE class_id = ? AND student_no = ?",
        (cls["id"], student_no),
    ).fetchone()
    if user is not None and user["status"] == "active":
        return json_err("ALREADY_ACTIVE", "该学号已激活，请直接登录", 409)
    if user is not None and user["status"] == "disabled":
        return json_err("ACCOUNT_DISABLED", "账号已停用，请联系系统管理员", 403)
    if user is None or user["status"] != "pending" or user["name"] != name:
        # 学号不在名单、姓名不符或状态异常时统一提示，不泄露细节
        return json_err("ROSTER_MISMATCH", "注册信息与班级名单不匹配", 400)

    try:
        db.execute(
            "UPDATE users SET password_hash = ?, status = 'active', "
            f"updated_at = {NOW_SQL} WHERE id = ? AND status = 'pending'",
            (generate_password_hash(password, method=HASH_METHOD), user["id"]),
        )
        db.commit()
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "激活失败，请稍后重试", 500)

    fresh = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    _start_session(fresh)
    return json_ok(
        {
            "user": _public_user(fresh),
            "class": _class_info(db, cls["id"]),
            "csrf_token": session["csrf_token"],
        },
        201,
    )


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    login_id = (data.get("login_id") or "").strip()
    password = data.get("password") or ""
    if not login_id or not password:
        return json_err("VALIDATION_ERROR", "请输入账号和密码", 400)

    key = _lock_key(login_id)
    if _is_locked(key):
        return json_err("LOGIN_LOCKED", "失败次数过多，请 5 分钟后再试", 429)

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE login_id = ?", (login_id,)
    ).fetchone()
    if (
        user is None
        or not user["password_hash"]
        or not check_password_hash(user["password_hash"], password)
    ):
        _record_fail(key)
        return json_err("INVALID_CREDENTIALS", "账号或密码不正确", 401)
    _LOGIN_FAILS.pop(key, None)

    if user["status"] == "disabled":
        return json_err("ACCOUNT_DISABLED", "账号已停用，请联系系统管理员", 403)
    if user["status"] == "pending":
        return json_err("ACCOUNT_PENDING", "账号尚未激活，请先完成学生激活", 403)
    if user["role"] in ("student", "admin") and user["class_id"] is None:
        return json_err("ACCOUNT_UNBOUND", "账号未绑定班级，请联系系统管理员", 403)

    _start_session(user)
    return json_ok(
        {
            "user": _public_user(user),
            "class": _class_info(db, user["class_id"]),
            "csrf_token": session["csrf_token"],
        }
    )


@bp.post("/logout")
def logout():
    session.clear()
    return json_ok()


@bp.get("/me")
def me():
    user = current_user()
    if user is None:
        return json_err("AUTH_REQUIRED", "请先登录", 401)
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    db = get_db()
    return json_ok(
        {
            "user": _public_user(user),
            "class": _class_info(db, user["class_id"]),
            "semester": _semester_info(current_semester()),
            "csrf_token": session["csrf_token"],
        }
    )


@bp.post("/change-password")
@require_roles("student", "admin", "system_admin")
def change_password():
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password") or ""
    new_password = data.get("new_password") or ""
    if not old_password or not new_password:
        return json_err("VALIDATION_ERROR", "请输入原密码和新密码", 400)
    if len(new_password) < MIN_PASSWORD_LEN:
        return json_err("VALIDATION_ERROR", "新密码至少 8 位", 400)

    user = g.user
    if not check_password_hash(user["password_hash"], old_password):
        return json_err("INVALID_CREDENTIALS", "原密码不正确", 400)

    db = get_db()
    try:
        db.execute(
            f"UPDATE users SET password_hash = ?, updated_at = {NOW_SQL} WHERE id = ?",
            (generate_password_hash(new_password, method=HASH_METHOD), user["id"]),
        )
        db.commit()
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "修改密码失败，请稍后重试", 500)
    return json_ok()
