"""系统管理 API：学期、班级、学生名单、账号生命周期与审计日志。

蓝图前缀 /api/system，全部接口仅 system_admin 可访问，每个写操作写审计日志。
"""
import csv
import io
import json
import re
import secrets
import sqlite3
from datetime import date

from flask import Blueprint, g, request
from werkzeug.security import generate_password_hash

from db import get_db
from utils import (
    audit,
    generate_invite_code,
    hash_invite_code,
    json_err,
    json_ok,
    require_roles,
)

bp = Blueprint("system", __name__, url_prefix="/api/system")

STUDENT_NO_RE = re.compile(r"^\d{6,20}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_CSV_BYTES = 2 * 1024 * 1024
MIN_PASSWORD_LEN = 8

# 运行环境（LibreSSL）无 hashlib.scrypt，Werkzeug 默认 scrypt 会报错，统一显式指定 pbkdf2
HASH_METHOD = "pbkdf2:sha256"

# 与 schema 默认时间戳格式一致：带时区 ISO 8601（UTC）
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"


def _page_args():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except (TypeError, ValueError):
        page, page_size = 1, 20
    return page, page_size


def _not_found(message="资源不存在"):
    return json_err("NOT_FOUND", message, 404)


def _get_class_or_404(db, class_id):
    return db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()


# ---------------- 学期管理 ----------------


@bp.post("/semesters")
@require_roles("system_admin")
def create_semester():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    start_date = (data.get("start_date") or "").strip()
    week_count = data.get("week_count")
    status = data.get("status") or "inactive"

    if not name:
        return json_err("SEMESTER_INVALID", "学期名称不能为空", 400, {"field": "name"})
    if not DATE_RE.fullmatch(start_date):
        return json_err(
            "SEMESTER_INVALID", "第一周周一日期格式应为 YYYY-MM-DD", 400, {"field": "start_date"}
        )
    try:
        first_day = date.fromisoformat(start_date)
    except ValueError:
        return json_err("SEMESTER_INVALID", "第一周周一日期无效", 400, {"field": "start_date"})
    if first_day.weekday() != 0:
        return json_err("SEMESTER_INVALID", "第一周周一必须是星期一", 400, {"field": "start_date"})
    if isinstance(week_count, str) and week_count.isdigit():
        week_count = int(week_count)
    if not isinstance(week_count, int) or isinstance(week_count, bool) or not 1 <= week_count <= 30:
        return json_err("SEMESTER_INVALID", "周数必须为 1-30 的整数", 400, {"field": "week_count"})
    if status not in ("active", "inactive"):
        return json_err(
            "SEMESTER_INVALID", "学期状态仅支持 active 或 inactive", 400, {"field": "status"}
        )

    db = get_db()
    try:
        if status == "active":
            # 同一事务把其他学期置 inactive，部分唯一索引兜底
            db.execute(
                "UPDATE semesters SET status = 'inactive', "
                f"updated_at = {NOW_SQL} WHERE status = 'active'"
            )
        cur = db.execute(
            "INSERT INTO semesters (name, start_date, week_count, status) VALUES (?, ?, ?, ?)",
            (name, start_date, week_count, status),
        )
        semester_id = cur.lastrowid
        audit(
            "semester_create",
            "semester",
            semester_id,
            detail={"name": name, "start_date": start_date, "week_count": week_count, "status": status},
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_err("CONFLICT", "已存在启用中的学期，请确认唯一 active 学期", 409)
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "创建学期失败，请稍后重试", 500)

    row = db.execute("SELECT * FROM semesters WHERE id = ?", (semester_id,)).fetchone()
    return json_ok({"semester": dict(row)}, 201)


@bp.patch("/semesters/<int:semester_id>")
@require_roles("system_admin")
def update_semester(semester_id):
    """启用/停用学期；启用时同事务把其他学期置 inactive（部分唯一索引兜底）。"""
    db = get_db()
    sem = db.execute("SELECT * FROM semesters WHERE id = ?", (semester_id,)).fetchone()
    if sem is None:
        return _not_found("学期不存在")

    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in ("active", "inactive"):
        return json_err("VALIDATION_ERROR", "学期状态仅支持 active 或 inactive", 400, {"field": "status"})

    if new_status != sem["status"]:
        try:
            if new_status == "active":
                db.execute(
                    "UPDATE semesters SET status = 'inactive', "
                    f"updated_at = {NOW_SQL} WHERE status = 'active' AND id != ?",
                    (semester_id,),
                )
            db.execute(
                f"UPDATE semesters SET status = ?, updated_at = {NOW_SQL} WHERE id = ?",
                (new_status, semester_id),
            )
            audit("semester_update", "semester", semester_id, detail={"status": new_status})
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return json_err("CONFLICT", "已存在启用中的学期，请确认唯一 active 学期", 409)
        except Exception:
            db.rollback()
            return json_err("INTERNAL_ERROR", "更新学期失败，请稍后重试", 500)

    row = db.execute("SELECT * FROM semesters WHERE id = ?", (semester_id,)).fetchone()
    return json_ok({"semester": dict(row)})


@bp.get("/semesters")
@require_roles("system_admin")
def list_semesters():
    db = get_db()
    page, page_size = _page_args()
    total = db.execute("SELECT COUNT(*) FROM semesters").fetchone()[0]
    rows = db.execute(
        "SELECT * FROM semesters ORDER BY id DESC LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    return json_ok(
        {"items": [dict(r) for r in rows], "page": page, "page_size": page_size, "total": total}
    )


# ---------------- 班级管理 ----------------


@bp.post("/classes")
@require_roles("system_admin")
def create_class():
    data = request.get_json(silent=True) or {}
    class_code = (data.get("class_code") or "").strip()
    class_name = (data.get("class_name") or "").strip()
    status = data.get("status") or "active"
    semester_id = data.get("semester_id")

    if not class_code:
        return json_err("VALIDATION_ERROR", "班级代码不能为空", 400, {"field": "class_code"})
    if not class_name:
        return json_err("VALIDATION_ERROR", "班级名称不能为空", 400, {"field": "class_name"})
    if status not in ("active", "disabled"):
        return json_err("VALIDATION_ERROR", "班级状态仅支持 active 或 disabled", 400, {"field": "status"})

    invite_code = generate_invite_code()
    code_hash = hash_invite_code(invite_code)

    db = get_db()
    if semester_id is not None:
        sem = db.execute("SELECT id FROM semesters WHERE id = ?", (semester_id,)).fetchone()
        if sem is None:
            return json_err("VALIDATION_ERROR", "所属学期不存在", 400, {"field": "semester_id"})
    try:
        cur = db.execute(
            "INSERT INTO classes (class_code, class_name, semester_id, invite_code_hash, "
            f"invite_code_updated_at, status) VALUES (?, ?, ?, ?, {NOW_SQL}, ?)",
            (class_code, class_name, semester_id, code_hash, status),
        )
        class_id = cur.lastrowid
        audit("class_create", "class", class_id, class_id=class_id,
              detail={"class_code": class_code, "class_name": class_name, "status": status})
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_err("CONFLICT", "班级代码已存在", 409)
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "创建班级失败，请稍后重试", 500)

    row = db.execute(
        "SELECT id, class_code, class_name, semester_id, status, invite_code_updated_at, created_at "
        "FROM classes WHERE id = ?",
        (class_id,),
    ).fetchone()
    # 邀请码明文仅本次响应返回一次，数据库只保存摘要
    return json_ok({"class": dict(row), "invite_code": invite_code}, 201)


@bp.get("/classes")
@require_roles("system_admin")
def list_classes():
    db = get_db()
    page, page_size = _page_args()
    total = db.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
    rows = db.execute(
        "SELECT c.id, c.class_code, c.class_name, c.semester_id, c.status, c.invite_code_updated_at, "
        "c.created_at, u.id AS admin_id, u.name AS admin_name, u.login_id AS admin_login_id, "
        "(SELECT COUNT(*) FROM users s WHERE s.class_id = c.id AND s.role = 'student') "
        "AS student_count "
        "FROM classes c LEFT JOIN users u "
        "ON u.class_id = c.id AND u.role = 'admin' AND u.status = 'active' "
        "ORDER BY c.id DESC LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "class_code": r["class_code"],
                "class_name": r["class_name"],
                "semester_id": r["semester_id"],
                "status": r["status"],
                "invite_code_updated_at": r["invite_code_updated_at"],
                "created_at": r["created_at"],
                "student_count": r["student_count"],
                "admin": (
                    {"id": r["admin_id"], "name": r["admin_name"], "login_id": r["admin_login_id"]}
                    if r["admin_id"] is not None
                    else None
                ),
            }
        )
    return json_ok({"items": items, "page": page, "page_size": page_size, "total": total})


@bp.patch("/classes/<int:class_id>")
@require_roles("system_admin")
def update_class(class_id):
    """停用/启用班级（停用后该班禁止新激活和业务写入）。"""
    db = get_db()
    cls = _get_class_or_404(db, class_id)
    if cls is None:
        return _not_found("班级不存在")

    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in ("active", "disabled"):
        return json_err("VALIDATION_ERROR", "班级状态仅支持 active 或 disabled", 400, {"field": "status"})

    if new_status != cls["status"]:
        try:
            db.execute(
                f"UPDATE classes SET status = ?, updated_at = {NOW_SQL} WHERE id = ?",
                (new_status, class_id),
            )
            audit("class_update", "class", class_id, class_id=class_id, detail={"status": new_status})
            db.commit()
        except Exception:
            db.rollback()
            return json_err("INTERNAL_ERROR", "更新班级失败，请稍后重试", 500)

    row = db.execute(
        "SELECT id, class_code, class_name, semester_id, status, invite_code_updated_at, created_at "
        "FROM classes WHERE id = ?",
        (class_id,),
    ).fetchone()
    return json_ok({"class": dict(row)})


@bp.get("/classes/<int:class_id>/students")
@require_roles("system_admin")
def list_class_students(class_id):
    db = get_db()
    if _get_class_or_404(db, class_id) is None:
        return _not_found("班级不存在")
    status = request.args.get("status")
    if status is not None and status not in ("pending", "active", "disabled"):
        return json_err("VALIDATION_ERROR", "status 仅支持 pending/active/disabled", 400)

    where = "WHERE class_id = ? AND role = 'student'"
    params = [class_id]
    if status:
        where += " AND status = ?"
        params.append(status)

    page, page_size = _page_args()
    total = db.execute(f"SELECT COUNT(*) FROM users {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT id, student_no, name, status, created_at, updated_at FROM users {where} "
        "ORDER BY student_no LIMIT ? OFFSET ?",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    return json_ok(
        {"items": [dict(r) for r in rows], "page": page, "page_size": page_size, "total": total}
    )


# ---------------- 学生名单导入 ----------------


def _parse_csv_rows():
    """解析 multipart CSV，返回 [(row_no, name, student_no)] 或错误响应。"""
    upload = request.files.get("file")
    if upload is None:
        return None, json_err("VALIDATION_ERROR", "请上传 CSV 文件（字段名 file）", 400)
    raw = upload.read()
    if len(raw) > MAX_CSV_BYTES:
        return None, json_err("VALIDATION_ERROR", "CSV 文件不能超过 2MB", 400)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, json_err("VALIDATION_ERROR", "CSV 文件须为 UTF-8 编码", 400)

    reader = csv.reader(io.StringIO(text))
    lines = [row for row in reader if any(cell.strip() for cell in row)]
    if not lines:
        return None, json_err("VALIDATION_ERROR", "CSV 文件为空", 400)

    header = [cell.strip() for cell in lines[0]]
    try:
        name_idx = header.index("姓名") if "姓名" in header else header.index("name")
        no_idx = header.index("学号") if "学号" in header else header.index("student_no")
    except ValueError:
        return None, json_err(
            "VALIDATION_ERROR", "CSV 表头必须包含「姓名」和「学号」两列", 400
        )

    rows = []
    for i, line in enumerate(lines[1:], start=1):
        name = line[name_idx].strip() if name_idx < len(line) else ""
        student_no = line[no_idx].strip() if no_idx < len(line) else ""
        rows.append((i, name, student_no))
    return rows, None


def _parse_json_rows():
    data = request.get_json(silent=True) or {}
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        return None, json_err("VALIDATION_ERROR", "请求体须包含非空 rows 数组", 400)
    rows = []
    for i, item in enumerate(raw_rows, start=1):
        if not isinstance(item, dict):
            rows.append((i, "", ""))
            continue
        name = (item.get("name") or item.get("姓名") or "").strip()
        student_no = (item.get("student_no") or item.get("学号") or "").strip()
        rows.append((i, name, student_no))
    return rows, None


@bp.post("/classes/<int:class_id>/students/import")
@require_roles("system_admin")
def import_students(class_id):
    db = get_db()
    cls = _get_class_or_404(db, class_id)
    if cls is None:
        return _not_found("班级不存在")
    if cls["status"] != "active":
        return json_err("CONFLICT", "班级已停用，禁止导入名单", 409)

    if request.files:
        rows, err = _parse_csv_rows()
    else:
        rows, err = _parse_json_rows()
    if err is not None:
        return err

    existing = {
        r[0] for r in db.execute("SELECT student_no FROM users WHERE student_no IS NOT NULL")
    }
    errors = []
    seen = set()
    for row_no, name, student_no in rows:
        if not name:
            errors.append({"row": row_no, "field": "name", "message": "姓名不能为空"})
        if not STUDENT_NO_RE.fullmatch(student_no):
            errors.append({"row": row_no, "field": "student_no", "message": "学号应为 6-20 位数字"})
        elif student_no in seen:
            errors.append({"row": row_no, "field": "student_no", "message": "名单内学号重复"})
        elif student_no in existing:
            errors.append({"row": row_no, "field": "student_no", "message": "学号已存在于系统中"})
        seen.add(student_no)
    if errors:
        return json_err("ROSTER_INVALID", "名单校验失败，整批未导入", 400, {"errors": errors})

    try:
        db.executemany(
            "INSERT INTO users (login_id, student_no, name, role, status, class_id) "
            "VALUES (?, ?, ?, 'student', 'pending', ?)",
            [(no, no, name, class_id) for _, name, no in rows],
        )
        audit(
            "students_import",
            "class",
            class_id,
            class_id=class_id,
            detail={"imported": len(rows)},
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_err("CONFLICT", "学号与现有数据冲突，整批未导入", 409)
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "导入失败，整批未写入", 500)
    return json_ok({"imported": len(rows)}, 201)


# ---------------- 管理员任命与交接 ----------------


@bp.put("/classes/<int:class_id>/admin")
@require_roles("system_admin")
def assign_class_admin(class_id):
    db = get_db()
    cls = _get_class_or_404(db, class_id)
    if cls is None:
        return _not_found("班级不存在")
    if cls["status"] != "active":
        return json_err("CONFLICT", "班级已停用，无法任命管理员", 409)

    data = request.get_json(silent=True) or {}
    login_id = (data.get("login_id") or "").strip()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""

    if not login_id or len(login_id) > 64 or any(ch.isspace() for ch in login_id):
        return json_err("VALIDATION_ERROR", "登录账号不能为空、不超过 64 位且不含空白字符", 400)
    if password and len(password) < MIN_PASSWORD_LEN:
        return json_err("VALIDATION_ERROR", "初始密码至少 8 位", 400)

    existing = db.execute("SELECT * FROM users WHERE login_id = ?", (login_id,)).fetchone()
    if existing is not None and existing["role"] != "admin":
        return json_err("CONFLICT", "该登录账号已被非管理员账号占用", 409)
    if (
        existing is not None
        and existing["status"] == "active"
        and existing["class_id"] not in (None, class_id)
    ):
        return json_err("CONFLICT", "该管理员账号已在其他班级启用", 409)
    if existing is None and not name:
        return json_err("VALIDATION_ERROR", "新建管理员账号需提供姓名", 400, {"field": "name"})

    generated_password = None
    try:
        old = db.execute(
            "SELECT id FROM users WHERE class_id = ? AND role = 'admin' AND status = 'active'",
            (class_id,),
        ).fetchone()
        if old is not None:
            db.execute(
                f"UPDATE users SET status = 'disabled', updated_at = {NOW_SQL} WHERE id = ?",
                (old["id"],),
            )

        if existing is not None:
            updates = f"role = 'admin', class_id = ?, status = 'active', updated_at = {NOW_SQL}"
            params = [class_id]
            if name:
                updates += ", name = ?"
                params.append(name)
            if password:
                updates += ", password_hash = ?"
                params.append(generate_password_hash(password, method=HASH_METHOD))
            params.append(existing["id"])
            db.execute(f"UPDATE users SET {updates} WHERE id = ?", params)
            admin_id = existing["id"]
        else:
            plain = password or secrets.token_urlsafe(8)
            if not password:
                generated_password = plain
            cur = db.execute(
                "INSERT INTO users (login_id, name, password_hash, role, status, class_id) "
                "VALUES (?, ?, ?, 'admin', 'active', ?)",
                (login_id, name, generate_password_hash(plain, method=HASH_METHOD), class_id),
            )
            admin_id = cur.lastrowid

        audit(
            "admin_assign",
            "user",
            admin_id,
            class_id=class_id,
            detail={"login_id": login_id, "previous_admin_id": old["id"] if old else None},
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_err("CONFLICT", "同一班级只能有一个有效管理员", 409)
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "任命失败，请稍后重试", 500)

    row = db.execute(
        "SELECT id, login_id, name, role, status, class_id FROM users WHERE id = ?",
        (admin_id,),
    ).fetchone()
    result = {"admin": dict(row)}
    if generated_password is not None:
        # 服务端生成的初始密码仅本次响应返回一次
        result["new_password"] = generated_password
    return json_ok(result)


# ---------------- 账号生命周期 ----------------


@bp.get("/users")
@require_roles("system_admin")
def list_users():
    """用户列表：支持 role / class_id 筛选与分页，附带班级名称。"""
    db = get_db()
    where = "WHERE 1 = 1"
    params = []

    role = request.args.get("role")
    if role:
        if role not in ("student", "admin", "system_admin"):
            return json_err("VALIDATION_ERROR", "role 仅支持 student/admin/system_admin", 400)
        where += " AND u.role = ?"
        params.append(role)
    class_id = request.args.get("class_id")
    if class_id:
        try:
            class_id = int(class_id)
        except (TypeError, ValueError):
            return json_err("VALIDATION_ERROR", "class_id 必须为整数", 400)
        where += " AND u.class_id = ?"
        params.append(class_id)

    page, page_size = _page_args()
    total = db.execute(f"SELECT COUNT(*) FROM users u {where}", params).fetchone()[0]
    rows = db.execute(
        "SELECT u.id, u.login_id, u.student_no, u.name, u.role, u.status, u.class_id, "
        "c.class_name, c.class_code, u.created_at, u.updated_at "
        "FROM users u LEFT JOIN classes c ON c.id = u.class_id "
        f"{where} ORDER BY u.id LIMIT ? OFFSET ?",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    return json_ok(
        {"items": [dict(r) for r in rows], "page": page, "page_size": page_size, "total": total}
    )


@bp.patch("/users/<int:user_id>")
@require_roles("system_admin")
def update_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return _not_found("用户不存在")

    data = request.get_json(silent=True) or {}
    if "role" in data:
        return json_err("VALIDATION_ERROR", "不支持修改用户角色", 400)

    updates = []
    params = []
    changes = {}

    if "status" in data:
        new_status = data["status"]
        if new_status not in ("active", "disabled"):
            return json_err("VALIDATION_ERROR", "status 仅支持 active 或 disabled", 400)
        if user["role"] == "system_admin":
            return json_err("FORBIDDEN", "不能停用系统管理员账号", 403)
        if new_status != user["status"]:
            updates.append("status = ?")
            params.append(new_status)
            changes["status"] = new_status

    if "class_id" in data:
        if user["role"] != "student":
            return json_err("VALIDATION_ERROR", "仅学生支持转班，管理员请使用任命接口", 400)
        new_class_id = data["class_id"]
        target = db.execute("SELECT * FROM classes WHERE id = ?", (new_class_id,)).fetchone()
        if target is None or target["status"] != "active":
            return json_err("TARGET_CLASS_INVALID", "目标班级不存在或已停用", 400)
        conflict = db.execute(
            "SELECT id FROM users WHERE class_id = ? AND student_no = ? AND id != ?",
            (new_class_id, user["student_no"], user_id),
        ).fetchone()
        if conflict is not None:
            return json_err("CONFLICT", "目标班级已存在相同学号的学生", 409)
        if new_class_id != user["class_id"]:
            updates.append("class_id = ?")
            params.append(new_class_id)
            changes["class_id"] = new_class_id

    if not updates:
        return json_err("VALIDATION_ERROR", "没有需要更新的字段", 400)

    updates.append(f"updated_at = {NOW_SQL}")
    params.append(user_id)
    try:
        db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        audit("user_update", "user", user_id,
              class_id=changes.get("class_id", user["class_id"]), detail=changes)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_err("CONFLICT", "数据冲突（如唯一管理员约束），更新被拒绝", 409)
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "更新失败，请稍后重试", 500)

    row = db.execute(
        "SELECT id, login_id, student_no, name, role, status, class_id FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return json_ok({"user": dict(row)})


@bp.post("/users/<int:user_id>/reset-password")
@require_roles("system_admin")
def reset_password(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return _not_found("用户不存在")

    new_password = secrets.token_urlsafe(8)
    try:
        db.execute(
            f"UPDATE users SET password_hash = ?, updated_at = {NOW_SQL} WHERE id = ?",
            (generate_password_hash(new_password, method=HASH_METHOD), user_id),
        )
        audit("password_reset", "user", user_id, class_id=user["class_id"])
        db.commit()
    except Exception:
        db.rollback()
        return json_err("INTERNAL_ERROR", "重置密码失败，请稍后重试", 500)
    # 新密码明文仅本次响应返回一次，审计日志不记录
    return json_ok({"new_password": new_password})


# ---------------- 审计日志查询 ----------------


@bp.get("/audit-logs")
@require_roles("system_admin")
def list_audit_logs():
    db = get_db()
    where = "WHERE 1 = 1"
    params = []

    actor_user_id = request.args.get("actor_user_id")
    if actor_user_id:
        where += " AND a.actor_user_id = ?"
        params.append(actor_user_id)
    class_id = request.args.get("class_id")
    if class_id:
        where += " AND a.class_id = ?"
        params.append(class_id)
    action = request.args.get("action")
    if action:
        where += " AND a.action = ?"
        params.append(action)
    date_from = request.args.get("date_from")
    if date_from:
        where += " AND substr(a.created_at, 1, 10) >= ?"
        params.append(date_from)
    date_to = request.args.get("date_to")
    if date_to:
        where += " AND substr(a.created_at, 1, 10) <= ?"
        params.append(date_to)

    page, page_size = _page_args()
    total = db.execute(f"SELECT COUNT(*) FROM audit_logs a {where}", params).fetchone()[0]
    rows = db.execute(
        "SELECT a.id, a.actor_user_id, u.name AS actor_name, u.login_id AS actor_login_id, "
        "a.action, a.target_type, a.target_id, a.class_id, c.class_name, "
        "a.result, a.detail_json, a.created_at "
        "FROM audit_logs a "
        "LEFT JOIN users u ON u.id = a.actor_user_id "
        "LEFT JOIN classes c ON c.id = a.class_id "
        f"{where} ORDER BY a.id DESC LIMIT ? OFFSET ?",
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()

    items = []
    for r in rows:
        item = dict(r)
        raw_detail = item.pop("detail_json", None)
        try:
            item["detail"] = json.loads(raw_detail) if raw_detail else None
        except (TypeError, ValueError):
            item["detail"] = raw_detail
        items.append(item)
    return json_ok({"items": items, "page": page, "page_size": page_size, "total": total})
