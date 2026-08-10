# -*- coding: utf-8 -*-
"""pytest 公共夹具与辅助。

- app：create_app 指向 tmp_path 下的临时 SQLite 库，TESTING 模式。
- client / make_client：Flask 测试客户端（各自独立 Cookie 会话）。
- seed：最小预置数据（1 active 学期、2 个班级、system_admin、每班 admin、
  active/pending/disabled 学生、含多时段的课程与作业），直接写 SQLite，
  不依赖 scripts/seed_dev.py 的演示数据。
- CSRF：登录/激活响应 JSON 中的 csrf_token 放入后续写请求的 X-CSRF-Token 头。
"""
import os
import sqlite3
import sys

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from db import get_db  # noqa: E402
from utils import hash_invite_code  # noqa: E402

# ---------------------------------------------------------------------------
# 种子数据常量（明文只存在于测试代码与响应断言中，数据库只存哈希/摘要）
# ---------------------------------------------------------------------------

SEMESTER_NAME = '2025 秋季学期'
SEMESTER_START = '2025-09-01'  # 周一
SEMESTER_WEEKS = 16

CLASS1_CODE, CLASS1_NAME = 'C001', '软件 1 班'
CLASS2_CODE, CLASS2_NAME = 'C002', '软件 2 班'
INVITE1 = 'CLASS1-INVITE'
INVITE2 = 'CLASS2-INVITE'

SYSADMIN_ID, SYSADMIN_PW = 'sysadmin', 'SysAdmin123'
ADMIN1_ID, ADMIN1_PW = 'admin1', 'Admin1234'
ADMIN2_ID, ADMIN2_PW = 'admin2', 'Admin1234'
STU1_NO, STU1_NAME, STU1_PW = '2021001', '张三', 'Student123'   # 1 班 active
STU2_NO, STU2_NAME = '2021002', '李四'                          # 1 班 pending
STU3_NO, STU3_NAME, STU3_PW = '2021003', '王五', 'Student123'   # 2 班 active
STU4_NO, STU4_NAME, STU4_PW = '2021004', '赵六', 'Student123'   # 1 班 disabled


def _fast_hash(password):
    """低迭代 pbkdf2，仅为测试提速；check_password_hash 按哈希串自描述解析。"""
    return generate_password_hash(password, method='pbkdf2:sha256:1000')


def seed_database(db_path):
    """向空库写入最小种子数据，返回各实体 id 字典。"""
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys = ON')
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO semesters (name, start_date, week_count, status) VALUES (?, ?, ?, 'active')",
        (SEMESTER_NAME, SEMESTER_START, SEMESTER_WEEKS),
    )
    sem_id = cur.lastrowid

    class_ids = []
    for code, name, invite in ((CLASS1_CODE, CLASS1_NAME, INVITE1),
                               (CLASS2_CODE, CLASS2_NAME, INVITE2)):
        cur.execute(
            "INSERT INTO classes (class_code, class_name, semester_id, invite_code_hash,"
            " invite_code_updated_at, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (code, name, sem_id, hash_invite_code(invite), '2025-08-01T00:00:00+00:00'),
        )
        class_ids.append(cur.lastrowid)
    c1, c2 = class_ids

    def add_user(login_id, student_no, name, password, role, status, class_id):
        cur.execute(
            'INSERT INTO users (login_id, student_no, name, password_hash, role, status, class_id)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?)',
            (login_id, student_no, name,
             _fast_hash(password) if password else None, role, status, class_id),
        )
        return cur.lastrowid

    u_sys = add_user(SYSADMIN_ID, None, '系统管理员', SYSADMIN_PW, 'system_admin', 'active', None)
    u_admin1 = add_user(ADMIN1_ID, None, '管理员一', ADMIN1_PW, 'admin', 'active', c1)
    u_admin2 = add_user(ADMIN2_ID, None, '管理员二', ADMIN2_PW, 'admin', 'active', c2)
    u_stu1 = add_user(STU1_NO, STU1_NO, STU1_NAME, STU1_PW, 'student', 'active', c1)
    u_stu2 = add_user(STU2_NO, STU2_NO, STU2_NAME, None, 'student', 'pending', c1)
    u_stu3 = add_user(STU3_NO, STU3_NO, STU3_NAME, STU3_PW, 'student', 'active', c2)
    u_stu4 = add_user(STU4_NO, STU4_NO, STU4_NAME, STU4_PW, 'student', 'disabled', c1)

    def add_teacher(name):
        cur.execute('INSERT INTO teachers (name) VALUES (?)', (name,))
        return cur.lastrowid

    t_wang, t_li, t_chen = add_teacher('王老师'), add_teacher('李老师'), add_teacher('陈老师')

    def add_course(class_id, code, name, teacher_id):
        cur.execute(
            'INSERT INTO courses (class_id, semester_id, course_code, course_name, teacher_id)'
            ' VALUES (?, ?, ?, ?, ?)',
            (class_id, sem_id, code, name, teacher_id),
        )
        return cur.lastrowid

    def add_session(course_id, classroom, day, ss, es, ws, we):
        cur.execute(
            'INSERT INTO course_sessions'
            ' (course_id, classroom, day_of_week, start_section, end_section, week_start, week_end)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?)',
            (course_id, classroom, day, ss, es, ws, we),
        )
        return cur.lastrowid

    math = add_course(c1, 'MATH', '数学分析', t_wang)
    math_s1 = add_session(math, 'A101', 1, 1, 2, 1, 16)   # 周一 1-2 节
    math_s2 = add_session(math, 'A102', 3, 3, 4, 1, 16)   # 周三 3-4 节（一课多时段）
    eng = add_course(c1, 'ENGL', '大学英语', t_li)
    add_session(eng, 'B101', 5, 5, 6, 1, 8)
    phys = add_course(c2, 'PHYS', '大学物理', t_chen)
    add_session(phys, 'C101', 2, 1, 2, 1, 16)

    cur.execute(
        'INSERT INTO homework (course_id, content, deadline, created_by, updated_by)'
        ' VALUES (?, ?, ?, ?, ?)',
        (math, '第一章习题', '2025-10-01', u_admin1, u_admin1),
    )
    hw_math = cur.lastrowid

    conn.commit()
    conn.close()
    return {
        'semester': sem_id,
        'class1': c1, 'class2': c2,
        'sysadmin': u_sys, 'admin1': u_admin1, 'admin2': u_admin2,
        'stu_active': u_stu1, 'stu_pending': u_stu2,
        'stu_active_c2': u_stu3, 'stu_disabled': u_stu4,
        'course_math': math, 'course_eng': eng, 'course_phys': phys,
        'session_math_1': math_s1, 'session_math_2': math_s2,
        'homework_math': hw_math,
    }


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    db_path = str(tmp_path / 'test.db')
    application = create_app({'DATABASE': db_path, 'TESTING': True, 'SECRET_KEY': 'test'})
    application.config['SEED_IDS'] = seed_database(db_path)
    return application


@pytest.fixture
def seed(app):
    return app.config['SEED_IDS']


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_client(app):
    """需要多个并发热会话（如管理员交接）时使用。"""
    def _make():
        return app.test_client()
    return _make


@pytest.fixture
def db(app):
    """应用上下文内的 sqlite3 连接（row_factory=Row，外键已开启）。"""
    with app.app_context():
        yield get_db()


@pytest.fixture(autouse=True)
def _clear_login_rate_limit():
    """登录限流是 routes_auth 模块级内存状态，每个用例前后清空互不影响。"""
    import routes_auth
    routes_auth._LOGIN_FAILS.clear()
    yield
    routes_auth._LOGIN_FAILS.clear()


# ---------------------------------------------------------------------------
# 登录 / CSRF 辅助
# ---------------------------------------------------------------------------

def login(client, login_id, password):
    """POST /api/auth/login，返回原始响应（不断言状态码）。"""
    return client.post('/api/auth/login', json={'login_id': login_id, 'password': password})


def login_token(client, login_id, password):
    """登录并断言成功，返回 csrf_token。"""
    resp = login(client, login_id, password)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['csrf_token']


def csrf(token):
    return {'X-CSRF-Token': token}
