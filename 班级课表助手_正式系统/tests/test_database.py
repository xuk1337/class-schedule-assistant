# -*- coding: utf-8 -*-
"""数据库层测试：幂等初始化、9 表与索引、外键级联、唯一约束与部分唯一索引。"""
import sqlite3

import pytest

from db import get_db, init_db

EXPECTED_TABLES = {
    'semesters', 'classes', 'users', 'teachers', 'time_slots',
    'courses', 'course_sessions', 'homework', 'audit_logs',
}

EXPECTED_INDEXES = {
    'uq_semesters_single_active',
    'uq_users_one_active_admin_per_class',
    'idx_users_class',
    'idx_courses_class_semester',
    'idx_course_sessions_course',
    'idx_course_sessions_day',
    'idx_homework_course',
    'idx_homework_deadline',
    'idx_audit_logs_created_at',
    'idx_audit_logs_class',
    'idx_audit_logs_actor',
}


def test_empty_db_init_idempotent(app):
    """空库建表后再次执行 schema 不报错、数据不重复（time_slots 仍 12 行）。"""
    init_db(app)  # create_app 已执行过一次，这里第二次
    with app.app_context():
        d = get_db()
        assert d.execute('SELECT COUNT(*) FROM time_slots').fetchone()[0] == 12


def test_nine_tables_exist(db):
    tables = {
        r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    }
    assert tables == EXPECTED_TABLES


def test_indexes_exist(db):
    indexes = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert EXPECTED_INDEXES <= indexes
    # 唯一约束对应的自动索引也应存在（class_code / student_no / 课程联合唯一 / 教师名）
    auto = {name for name in indexes if name.startswith('sqlite_autoindex_')}
    assert any('classes' in n for n in auto)
    assert any('users' in n for n in auto)
    assert any('courses' in n for n in auto)


def test_foreign_keys_enforced(db):
    """外键开启：引用不存在课程的上课安排应被拒绝。"""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            'INSERT INTO course_sessions'
            ' (course_id, classroom, day_of_week, start_section, end_section, week_start, week_end)'
            " VALUES (999999, 'X', 1, 1, 2, 1, 16)")
    db.rollback()


def test_course_delete_cascades_sessions_and_homework(db, seed):
    """删除课程应级联删除其上课安排与作业。"""
    cid = seed['course_math']
    assert db.execute('SELECT COUNT(*) FROM course_sessions WHERE course_id = ?', (cid,)).fetchone()[0] == 2
    assert db.execute('SELECT COUNT(*) FROM homework WHERE course_id = ?', (cid,)).fetchone()[0] == 1
    db.execute('DELETE FROM courses WHERE id = ?', (cid,))
    db.commit()
    assert db.execute('SELECT COUNT(*) FROM course_sessions WHERE course_id = ?', (cid,)).fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM homework WHERE course_id = ?', (cid,)).fetchone()[0] == 0


def test_unique_class_code(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO classes (class_code, class_name, status) VALUES ('C001', '重复班', 'active')")
    db.rollback()


def test_unique_student_no_globally(db, seed):
    """student_no 全局唯一：即使插到另一个班也冲突。"""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO users (login_id, student_no, name, role, status, class_id)"
            " VALUES ('x2021001', '2021001', '重复学号', 'student', 'pending', ?)",
            (seed['class2'],))
    db.rollback()


def test_unique_course_code_per_class_semester(db, seed):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            'INSERT INTO courses (class_id, semester_id, course_code, course_name)'
            " VALUES (?, ?, 'MATH', '重复编号')",
            (seed['class1'], seed['semester']))
    db.rollback()


def test_partial_unique_single_active_semester(db):
    """部分唯一索引：已有 active 学期时再插入 active 学期必须失败。"""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO semesters (name, start_date, week_count, status)"
            " VALUES ('第二个学期', '2026-03-02', 16, 'active')")
    db.rollback()
    # 但 inactive 学期可以并存
    db.execute(
        "INSERT INTO semesters (name, start_date, week_count, status)"
        " VALUES ('备用学期', '2026-03-02', 16, 'inactive')")
    db.commit()


def test_partial_unique_one_active_admin_per_class(db, seed):
    """部分唯一索引：同一班级不能有两个 status='active' 的 admin。"""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO users (login_id, name, password_hash, role, status, class_id)"
            " VALUES ('admin1x', '第二管理员', 'h', 'admin', 'active', ?)",
            (seed['class1'],))
    db.rollback()
    # 同班 disabled admin 不触发该索引
    db.execute(
        "INSERT INTO users (login_id, name, password_hash, role, status, class_id)"
        " VALUES ('admin1old', '旧管理员', 'h', 'admin', 'disabled', ?)",
        (seed['class1'],))
    db.commit()


def test_semester_monday_check_constraint(db):
    """CHECK 约束：start_date 必须是周一（2025-09-02 是周二）。"""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO semesters (name, start_date, week_count, status)"
            " VALUES ('坏学期', '2025-09-02', 16, 'inactive')")
    db.rollback()
