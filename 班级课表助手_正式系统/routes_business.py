"""业务 API：课表查询、课程/上课安排/作业 CRUD、教师联想、邀请码重置、双 CSV 导入。

蓝图（由 app.py 统一注册，也可直接遍历模块级 ``blueprints`` 元组）：
- schedule_bp        /api/schedule
- courses_bp         /api/courses
- course_sessions_bp /api/course-sessions
- homework_bp        /api/homework
- teachers_bp        /api/teachers
- classes_me_bp      /api/classes/me
- import_bp          /api/import

依赖共享契约：db.get_db()、utils.json_ok/json_err/require_roles/current_semester/audit。
所有业务写接口的 class_id / semester_id 均从 Session 用户与 active 学期推导，
不接受前端传入；CSRF 由 app.py 的 before_request 统一校验，本模块不重复处理。
"""

import csv
import io
import os
import re
import sqlite3
from datetime import date, datetime, timezone

from flask import Blueprint, current_app, g, request

from db import get_db
from utils import (audit, current_semester, generate_invite_code, hash_invite_code,
                   json_err, json_ok, require_roles)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

MAX_CSV_BYTES = 2 * 1024 * 1024          # 每个 CSV 文件不超过 2MB
MAX_COURSE_ROWS = 100                    # 课程 CSV 数据行上限
MAX_HOMEWORK_ROWS = 300                  # 作业 CSV 数据行上限
MAX_IMPORT_ERRORS = 100                  # 导入逐行错误上限，防止超大错误响应

_COURSE_CODE_RE = re.compile(r'^[A-Za-z0-9_-]{1,50}$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
_INT_RE = re.compile(r'^-?\d+$')

# 课程/作业 CSV 表头：同时接受 PRD §6.6 中文表头与 snake_case 英文表头
COURSE_HEADER_VARIANTS = [
    ['课程编号', '课程名', '教师', '教室', '星期', '开始节次', '结束节次', '起始周', '结束周', '考试日期', '备注'],
    ['course_code', 'course_name', 'teacher', 'classroom', 'day_of_week',
     'start_section', 'end_section', 'week_start', 'week_end', 'exam_date', 'remark'],
]
COURSE_FIELDS = ['course_code', 'course_name', 'teacher', 'classroom', 'day_of_week',
                 'start_section', 'end_section', 'week_start', 'week_end', 'exam_date', 'remark']
HOMEWORK_HEADER_VARIANTS = [
    ['课程编号', '作业内容', '截止日期'],
    ['course_code', 'content', 'deadline'],
]
HOMEWORK_FIELDS = ['course_code', 'content', 'deadline']


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _ts(value):
    """SQLite CURRENT_TIMESTAMP（UTC，无时区）规范为带时区的 ISO 8601 字符串。"""
    if isinstance(value, str) and _TS_RE.match(value):
        return value.replace(' ', 'T') + '+00:00'
    return value


def _to_int(value):
    """宽松整数解析，失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if _INT_RE.match(s):
            return int(s)
    return None


def _to_date(value):
    """严格校验 YYYY-MM-DD，合法返回原字符串，否则 None。"""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not _DATE_RE.match(s):
        return None
    try:
        datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        return None
    return s


def _to_text(value, max_len=200):
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:max_len]


def _biz_context():
    """读业务公共上下文。返回 (user, semester, err_response)。"""
    user = g.user
    if not user['class_id']:
        return None, None, json_err('FORBIDDEN', '当前账号未绑定班级', 403)
    semester = current_semester()
    if semester is None:
        return None, None, json_err('NO_ACTIVE_SEMESTER', '当前没有进行中的学期', 400)
    return user, semester, None


def _write_context():
    """写业务公共上下文：本班 + active 学期 + 班级未停用。返回 (user, semester, cls, err)。"""
    user, semester, err = _biz_context()
    if err:
        return None, None, None, err
    cls = get_db().execute('SELECT * FROM classes WHERE id = ?', (user['class_id'],)).fetchone()
    if cls is None or cls['status'] != 'active':
        return None, None, None, json_err('CLASS_DISABLED', '班级已停用，禁止业务写入', 403)
    return user, semester, cls, None


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def _session_dict(row):
    return {
        'id': row['id'],
        'course_id': row['course_id'],
        'classroom': row['classroom'],
        'day_of_week': row['day_of_week'],
        'start_section': row['start_section'],
        'end_section': row['end_section'],
        'week_start': row['week_start'],
        'week_end': row['week_end'],
        'created_at': _ts(row['created_at']),
        'updated_at': _ts(row['updated_at']),
    }


def _homework_dict(row):
    return {
        'id': row['id'],
        'course_id': row['course_id'],
        'content': row['content'],
        'deadline': row['deadline'],
        'created_by': row['created_by'],
        'updated_by': row['updated_by'],
        'created_at': _ts(row['created_at']),
        'updated_at': _ts(row['updated_at']),
    }


def _course_dict(row, teacher_name=None, sessions=None, homework=None):
    data = {
        'id': row['id'],
        'class_id': row['class_id'],
        'semester_id': row['semester_id'],
        'course_code': row['course_code'],
        'course_name': row['course_name'],
        'teacher_id': row['teacher_id'],
        'teacher_name': teacher_name,
        'exam_date': row['exam_date'],
        'remark': row['remark'],
        'created_at': _ts(row['created_at']),
        'updated_at': _ts(row['updated_at']),
    }
    if sessions is not None:
        data['sessions'] = sessions
    if homework is not None:
        data['homework'] = homework
    return data


def _load_course_detail(db, course_id, class_id):
    """按班级范围加载课程详情（含全部 sessions 与按 deadline 升序的 homework）。"""
    row = db.execute(
        """SELECT c.*, t.name AS teacher_name
           FROM courses c LEFT JOIN teachers t ON t.id = c.teacher_id
           WHERE c.id = ? AND c.class_id = ?""",
        (course_id, class_id),
    ).fetchone()
    if row is None:
        return None
    sessions = [
        _session_dict(s) for s in db.execute(
            'SELECT * FROM course_sessions WHERE course_id = ? '
            'ORDER BY day_of_week, start_section, id', (course_id,))
    ]
    homework = [
        _homework_dict(h) for h in db.execute(
            'SELECT * FROM homework WHERE course_id = ? ORDER BY deadline, id', (course_id,))
    ]
    return _course_dict(row, teacher_name=row['teacher_name'], sessions=sessions, homework=homework)


def _get_owned_course(db, course_id, class_id, semester_id):
    """写操作用：课程必须属于本班且属于 active 学期，否则返回 None。"""
    return db.execute(
        'SELECT * FROM courses WHERE id = ? AND class_id = ? AND semester_id = ?',
        (course_id, class_id, semester_id),
    ).fetchone()


def _get_or_create_teacher(db, name):
    row = db.execute('SELECT id FROM teachers WHERE name = ?', (name,)).fetchone()
    if row:
        return row['id']
    try:
        return db.execute('INSERT INTO teachers (name) VALUES (?)', (name,)).lastrowid
    except sqlite3.IntegrityError:  # name 唯一约束下的并发兜底
        row = db.execute('SELECT id FROM teachers WHERE name = ?', (name,)).fetchone()
        if row:
            return row['id']
        raise


def _insert_session(db, course_id, s):
    return db.execute(
        """INSERT INTO course_sessions
           (course_id, classroom, day_of_week, start_section, end_section, week_start, week_end)
           VALUES (?,?,?,?,?,?,?)""",
        (course_id, s['classroom'], s['day_of_week'], s['start_section'],
         s['end_section'], s['week_start'], s['week_end']),
    ).lastrowid


# ---------------------------------------------------------------------------
# 校验与冲突检测
# ---------------------------------------------------------------------------

def _validate_session_payload(raw, week_count):
    """校验单条上课安排，返回 (clean, field_errors)。"""
    if not isinstance(raw, dict):
        return None, {'session': '上课安排格式错误'}
    errors = {}

    classroom = _to_text(raw.get('classroom'), 100)
    if not classroom:
        errors['classroom'] = '教室不能为空'

    day = _to_int(raw.get('day_of_week'))
    if day is None:
        errors['day_of_week'] = '星期参数格式错误'
    elif not 1 <= day <= 7:
        errors['day_of_week'] = '星期必须在 1-7 之间'

    start_section = _to_int(raw.get('start_section'))
    end_section = _to_int(raw.get('end_section'))
    if start_section is None or end_section is None:
        errors['start_section'] = '节次/周次参数格式错误'
    elif not (1 <= start_section <= 12 and 1 <= end_section <= 12):
        errors['start_section'] = '节次必须在 1-12 之间'
    elif start_section > end_section:
        errors['start_section'] = '开始节次不能大于结束节次'

    week_start = _to_int(raw.get('week_start'))
    week_end = _to_int(raw.get('week_end'))
    if week_start is None or week_end is None:
        errors['week_start'] = '节次/周次参数格式错误'
    elif week_start < 1:
        errors['week_start'] = '起始周必须不小于 1'
    elif week_start > week_end:
        errors['week_start'] = '起始周不能大于结束周'
    elif week_end > week_count:
        errors['week_end'] = f'结束周不能超过学期周数（{week_count}）'

    if errors:
        return None, errors
    return {
        'classroom': classroom,
        'day_of_week': day,
        'start_section': start_section,
        'end_section': end_section,
        'week_start': week_start,
        'week_end': week_end,
    }, None


def _validate_course_payload(raw, week_count):
    """校验课程整体保存载荷（基本信息 + sessions 数组），返回 (clean, err_response)。"""
    if not isinstance(raw, dict):
        return None, json_err('VALIDATION_ERROR', '请求体必须为 JSON 对象', 400)
    errors = {}

    code = _to_text(raw.get('course_code'), 50)
    if not code:
        errors['course_code'] = '课程编号不能为空'
    elif not _COURSE_CODE_RE.match(code):
        errors['course_code'] = '课程编号仅允许字母、数字、_、-'

    name = _to_text(raw.get('course_name'), 100)
    if not name:
        errors['course_name'] = '课程名不能为空'

    teacher = _to_text(raw.get('teacher') or raw.get('teacher_name'), 50)
    if not teacher:
        errors['teacher'] = '教师不能为空'

    exam_date = raw.get('exam_date')
    if exam_date in (None, ''):
        exam_date = None
    else:
        exam_date = _to_date(exam_date)
        if exam_date is None:
            errors['exam_date'] = '考试日期必须为 YYYY-MM-DD'

    remark = _to_text(raw.get('remark'), 500)

    sessions = []
    session_errors = []
    raw_sessions = raw.get('sessions')
    if not isinstance(raw_sessions, list) or not raw_sessions:
        errors['sessions'] = '至少需要一条上课安排'
    else:
        seen = set()
        for i, item in enumerate(raw_sessions):
            clean, ferr = _validate_session_payload(item, week_count)
            if ferr:
                session_errors.append({'index': i, 'fields': ferr})
                continue
            key = (clean['classroom'], clean['day_of_week'], clean['start_section'],
                   clean['end_section'], clean['week_start'], clean['week_end'])
            if key in seen:
                continue  # 完全相同的安排去重
            seen.add(key)
            sessions.append(clean)
        if session_errors:
            errors['sessions'] = '上课安排存在字段错误'
        elif not sessions:
            errors['sessions'] = '至少需要一条上课安排'

    if errors:
        details = {'fields': errors}
        if session_errors:
            details['session_errors'] = session_errors
        return None, json_err('VALIDATION_ERROR', '课程字段校验失败', 400, details=details)
    return {
        'course_code': code,
        'course_name': name,
        'teacher': teacher,
        'exam_date': exam_date,
        'remark': remark,
        'sessions': sessions,
    }, None


def find_time_conflict(db, class_id, semester_id, session, exclude_course_id=None):
    """冲突检测（PRD §6.1）：同班同学期、同星期、节次区间重叠且周次区间重叠。

    session 需含 day_of_week/start_section/end_section/week_start/week_end。
    exclude_course_id 用于编辑时排除自身课程。命中返回冲突行，否则 None。
    """
    sql = """
        SELECT cs.id, cs.course_id, cs.classroom, cs.day_of_week,
               cs.start_section, cs.end_section, cs.week_start, cs.week_end,
               c.course_code, c.course_name
        FROM course_sessions cs
        JOIN courses c ON c.id = cs.course_id
        WHERE c.class_id = ? AND c.semester_id = ?
          AND cs.day_of_week = ?
          AND cs.start_section <= ? AND cs.end_section >= ?
          AND cs.week_start <= ? AND cs.week_end >= ?
    """
    params = [class_id, semester_id, session['day_of_week'],
              session['end_section'], session['start_section'],
              session['week_end'], session['week_start']]
    if exclude_course_id is not None:
        sql += ' AND c.id != ?'
        params.append(exclude_course_id)
    sql += ' ORDER BY cs.id LIMIT 1'
    return db.execute(sql, params).fetchone()


def _conflict_response(hit):
    return json_err(
        'COURSE_TIME_CONFLICT',
        f"与《{hit['course_name']}》时间冲突！",
        409,
        details={
            'conflict_course': {
                'id': hit['course_id'],
                'course_code': hit['course_code'],
                'course_name': hit['course_name'],
            },
            'conflict_session': {
                'id': hit['id'],
                'classroom': hit['classroom'],
                'day_of_week': hit['day_of_week'],
                'start_section': hit['start_section'],
                'end_section': hit['end_section'],
                'week_start': hit['week_start'],
                'week_end': hit['week_end'],
            },
        },
    )


def _validate_homework_payload(raw):
    if not isinstance(raw, dict):
        return None, json_err('VALIDATION_ERROR', '请求体必须为 JSON 对象', 400)
    errors = {}
    content = _to_text(raw.get('content'), 500)
    if not content:
        errors['content'] = '作业内容不能为空'
    deadline = _to_date(raw.get('deadline'))
    if deadline is None:
        errors['deadline'] = '截止日期必填，且必须为 YYYY-MM-DD'
    if errors:
        return None, json_err('VALIDATION_ERROR', '作业字段校验失败', 400,
                              details={'fields': errors})
    return {'content': content, 'deadline': deadline}, None


# ---------------------------------------------------------------------------
# 课表查询
# ---------------------------------------------------------------------------

schedule_bp = Blueprint('schedule', __name__, url_prefix='/api/schedule')


@schedule_bp.get('')
@require_roles('student', 'admin')
def get_schedule():
    """GET /api/schedule?week=N：本班 + active 学期指定周的课表。"""
    user, semester, err = _biz_context()
    if err:
        return err
    db = get_db()
    class_id = user['class_id']
    week_count = semester['week_count']

    start_date = datetime.strptime(semester['start_date'], '%Y-%m-%d').date()
    today = date.today()
    current_week = min(max((today - start_date).days // 7 + 1, 1), week_count)

    week_arg = request.args.get('week')
    if week_arg in (None, ''):
        week = current_week
    else:
        week = _to_int(week_arg)
        if week is None:
            return json_err('INVALID_WEEK', '周次参数格式错误', 400)
        week = min(max(week, 1), week_count)  # 越界钳制在 1..week_count

    course_rows = db.execute(
        """SELECT DISTINCT c.*, t.name AS teacher_name
           FROM courses c
           LEFT JOIN teachers t ON t.id = c.teacher_id
           JOIN course_sessions cs ON cs.course_id = c.id
           WHERE c.class_id = ? AND c.semester_id = ?
             AND cs.week_start <= ? AND cs.week_end >= ?
           ORDER BY c.course_name, c.id""",
        (class_id, semester['id'], week, week),
    ).fetchall()

    sessions_by_course = {}
    if course_rows:
        ids = [r['id'] for r in course_rows]
        placeholders = ','.join('?' * len(ids))
        for s in db.execute(
                f'SELECT * FROM course_sessions WHERE course_id IN ({placeholders}) '
                'ORDER BY day_of_week, start_section, id', ids):
            sessions_by_course.setdefault(s['course_id'], []).append(_session_dict(s))

    courses = [
        _course_dict(r, teacher_name=r['teacher_name'],
                     sessions=sessions_by_course.get(r['id'], []))
        for r in course_rows
    ]

    time_slots = [
        {'section_no': r['section_no'], 'start_time': r['start_time'], 'end_time': r['end_time']}
        for r in db.execute('SELECT * FROM time_slots ORDER BY section_no')
    ]

    cls = db.execute('SELECT id, class_code, class_name, status FROM classes WHERE id = ?',
                     (class_id,)).fetchone()

    # 提醒数据（考试倒计时、作业提醒）覆盖全学期，不随周过滤
    exams = [
        {'course_id': r['id'], 'course_name': r['course_name'], 'exam_date': r['exam_date']}
        for r in db.execute(
            "SELECT id, course_name, exam_date FROM courses "
            "WHERE class_id = ? AND semester_id = ? "
            "AND exam_date IS NOT NULL AND exam_date != '' "
            "ORDER BY exam_date, id", (class_id, semester['id']))
    ]
    homework = [
        {'id': r['id'], 'course_id': r['course_id'], 'course_name': r['course_name'],
         'content': r['content'], 'deadline': r['deadline']}
        for r in db.execute(
            """SELECT h.id, h.course_id, h.content, h.deadline, c.course_name
               FROM homework h JOIN courses c ON c.id = h.course_id
               WHERE c.class_id = ? AND c.semester_id = ?
               ORDER BY h.deadline, h.id""", (class_id, semester['id']))
    ]

    return json_ok({
        'week': week,
        'week_label': f'第 {week} 周',
        'current_week': current_week,
        'today': today.isoformat(),
        'semester': {
            'id': semester['id'],
            'name': semester['name'],
            'start_date': semester['start_date'],
            'week_count': semester['week_count'],
        },
        'class': {
            'id': cls['id'],
            'class_code': cls['class_code'],
            'class_name': cls['class_name'],
        } if cls else None,
        'time_slots': time_slots,
        'courses': courses,
        'exams': exams,
        'homework': homework,
    })


# ---------------------------------------------------------------------------
# 课程 CRUD（含整体替换式 sessions 保存）
# ---------------------------------------------------------------------------

courses_bp = Blueprint('courses', __name__, url_prefix='/api/courses')


@courses_bp.get('/<int:course_id>')
@require_roles('student', 'admin')
def get_course(course_id):
    """GET /api/courses/{id}：课程信息 + sessions + homework（deadline 升序）。"""
    user, semester, err = _biz_context()
    if err:
        return err
    detail = _load_course_detail(get_db(), course_id, user['class_id'])
    if detail is None:
        return json_err('COURSE_NOT_FOUND', '课程不存在', 404)
    return json_ok(detail)


@courses_bp.post('')
@require_roles('admin')
def create_course():
    """POST /api/courses：新增课程，同事务整体保存其 sessions。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    payload, err = _validate_course_payload(request.get_json(silent=True), semester['week_count'])
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']

    dup = db.execute(
        'SELECT id FROM courses WHERE class_id = ? AND semester_id = ? AND course_code = ?',
        (class_id, sem_id, payload['course_code'])).fetchone()
    if dup:
        return json_err('COURSE_CODE_EXISTS',
                        f"课程编号 {payload['course_code']} 在本学期已存在", 409)

    for s in payload['sessions']:
        hit = find_time_conflict(db, class_id, sem_id, s)
        if hit:
            return _conflict_response(hit)

    try:
        teacher_id = _get_or_create_teacher(db, payload['teacher'])
        course_id = db.execute(
            """INSERT INTO courses
               (class_id, semester_id, course_code, course_name, teacher_id, exam_date, remark)
               VALUES (?,?,?,?,?,?,?)""",
            (class_id, sem_id, payload['course_code'], payload['course_name'],
             teacher_id, payload['exam_date'], payload['remark'])).lastrowid
        for s in payload['sessions']:
            _insert_session(db, course_id, s)
        audit('course.create', 'course', course_id, class_id,
              detail={'course_code': payload['course_code'],
                      'course_name': payload['course_name']})
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_err('COURSE_CODE_EXISTS',
                        f"课程编号 {payload['course_code']} 在本学期已存在", 409)
    except Exception:
        db.rollback()
        current_app.logger.exception('create_course failed')
        return json_err('INTERNAL_ERROR', '保存课程失败', 500)

    return json_ok(_load_course_detail(db, course_id, class_id), status=201)


@courses_bp.put('/<int:course_id>')
@require_roles('admin')
def update_course(course_id):
    """PUT /api/courses/{id}：整体替换式更新课程基本信息与全部 sessions。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    if _get_owned_course(db, course_id, class_id, sem_id) is None:
        return json_err('COURSE_NOT_FOUND', '课程不存在或不属于当前班级', 404)

    payload, err = _validate_course_payload(request.get_json(silent=True), semester['week_count'])
    if err:
        return err

    dup = db.execute(
        'SELECT id FROM courses WHERE class_id = ? AND semester_id = ? AND course_code = ? AND id != ?',
        (class_id, sem_id, payload['course_code'], course_id)).fetchone()
    if dup:
        return json_err('COURSE_CODE_EXISTS',
                        f"课程编号 {payload['course_code']} 在本学期已存在", 409)

    for s in payload['sessions']:
        hit = find_time_conflict(db, class_id, sem_id, s, exclude_course_id=course_id)
        if hit:
            return _conflict_response(hit)

    try:
        teacher_id = _get_or_create_teacher(db, payload['teacher'])
        db.execute(
            """UPDATE courses
               SET course_code = ?, course_name = ?, teacher_id = ?, exam_date = ?, remark = ?
               WHERE id = ? AND class_id = ? AND semester_id = ?""",
            (payload['course_code'], payload['course_name'], teacher_id,
             payload['exam_date'], payload['remark'], course_id, class_id, sem_id))
        db.execute('DELETE FROM course_sessions WHERE course_id = ?', (course_id,))
        for s in payload['sessions']:
            _insert_session(db, course_id, s)
        audit('course.update', 'course', course_id, class_id,
              detail={'course_code': payload['course_code'],
                      'course_name': payload['course_name']})
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return json_err('COURSE_CODE_EXISTS',
                        f"课程编号 {payload['course_code']} 在本学期已存在", 409)
    except Exception:
        db.rollback()
        current_app.logger.exception('update_course failed')
        return json_err('INTERNAL_ERROR', '保存课程失败', 500)

    return json_ok(_load_course_detail(db, course_id, class_id))


@courses_bp.delete('/<int:course_id>')
@require_roles('admin')
def delete_course(course_id):
    """DELETE /api/courses/{id}：级联删除课程及其 sessions/homework，返回关联数量。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    course = _get_owned_course(db, course_id, class_id, sem_id)
    if course is None:
        return json_err('COURSE_NOT_FOUND', '课程不存在或不属于当前班级', 404)

    session_count = db.execute(
        'SELECT COUNT(*) AS n FROM course_sessions WHERE course_id = ?', (course_id,)).fetchone()['n']
    homework_count = db.execute(
        'SELECT COUNT(*) AS n FROM homework WHERE course_id = ?', (course_id,)).fetchone()['n']

    try:
        db.execute('DELETE FROM homework WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM course_sessions WHERE course_id = ?', (course_id,))
        db.execute('DELETE FROM courses WHERE id = ? AND class_id = ? AND semester_id = ?',
                   (course_id, class_id, sem_id))
        audit('course.delete', 'course', course_id, class_id,
              detail={'course_code': course['course_code'],
                      'course_name': course['course_name'],
                      'deleted_sessions': session_count,
                      'deleted_homework': homework_count})
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('delete_course failed')
        return json_err('INTERNAL_ERROR', '删除课程失败', 500)

    return json_ok({'deleted': True,
                    'deleted_sessions': session_count,
                    'deleted_homework': homework_count})


# ---------------------------------------------------------------------------
# 上课安排独立 CRUD
# ---------------------------------------------------------------------------

course_sessions_bp = Blueprint('course_sessions', __name__,
                               url_prefix='/api/course-sessions')


@courses_bp.post('/<int:course_id>/sessions')
@require_roles('admin')
def create_session(course_id):
    """POST /api/courses/{id}/sessions：为课程新增一条上课安排。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    if _get_owned_course(db, course_id, class_id, sem_id) is None:
        return json_err('COURSE_NOT_FOUND', '课程不存在或不属于当前班级', 404)

    clean, ferr = _validate_session_payload(request.get_json(silent=True), semester['week_count'])
    if ferr:
        return json_err('VALIDATION_ERROR', '上课安排字段校验失败', 400,
                        details={'fields': ferr})
    hit = find_time_conflict(db, class_id, sem_id, clean, exclude_course_id=course_id)
    if hit:
        return _conflict_response(hit)

    try:
        session_id = _insert_session(db, course_id, clean)
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('create_session failed')
        return json_err('INTERNAL_ERROR', '保存上课安排失败', 500)

    row = db.execute('SELECT * FROM course_sessions WHERE id = ?', (session_id,)).fetchone()
    return json_ok(_session_dict(row), status=201)


@course_sessions_bp.put('/<int:session_id>')
@require_roles('admin')
def update_session(session_id):
    """PUT /api/course-sessions/{id}：编辑一条上课安排（冲突检测排除自身课程）。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    row = db.execute(
        """SELECT cs.*, c.class_id AS course_class_id, c.semester_id AS course_semester_id
           FROM course_sessions cs JOIN courses c ON c.id = cs.course_id
           WHERE cs.id = ?""", (session_id,)).fetchone()
    if row is None or row['course_class_id'] != class_id or row['course_semester_id'] != sem_id:
        return json_err('SESSION_NOT_FOUND', '上课安排不存在或不属于当前班级', 404)

    clean, ferr = _validate_session_payload(request.get_json(silent=True), semester['week_count'])
    if ferr:
        return json_err('VALIDATION_ERROR', '上课安排字段校验失败', 400,
                        details={'fields': ferr})
    hit = find_time_conflict(db, class_id, sem_id, clean, exclude_course_id=row['course_id'])
    if hit:
        return _conflict_response(hit)

    try:
        db.execute(
            """UPDATE course_sessions
               SET classroom = ?, day_of_week = ?, start_section = ?, end_section = ?,
                   week_start = ?, week_end = ?
               WHERE id = ?""",
            (clean['classroom'], clean['day_of_week'], clean['start_section'],
             clean['end_section'], clean['week_start'], clean['week_end'], session_id))
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('update_session failed')
        return json_err('INTERNAL_ERROR', '保存上课安排失败', 500)

    row = db.execute('SELECT * FROM course_sessions WHERE id = ?', (session_id,)).fetchone()
    return json_ok(_session_dict(row))


@course_sessions_bp.delete('/<int:session_id>')
@require_roles('admin')
def delete_session(session_id):
    """DELETE /api/course-sessions/{id}：删除一条上课安排。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    row = db.execute(
        """SELECT cs.id, c.class_id AS course_class_id, c.semester_id AS course_semester_id
           FROM course_sessions cs JOIN courses c ON c.id = cs.course_id
           WHERE cs.id = ?""", (session_id,)).fetchone()
    if row is None or row['course_class_id'] != class_id or row['course_semester_id'] != sem_id:
        return json_err('SESSION_NOT_FOUND', '上课安排不存在或不属于当前班级', 404)

    try:
        db.execute('DELETE FROM course_sessions WHERE id = ?', (session_id,))
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('delete_session failed')
        return json_err('INTERNAL_ERROR', '删除上课安排失败', 500)
    return json_ok({'deleted': True})


# ---------------------------------------------------------------------------
# 作业 CRUD
# ---------------------------------------------------------------------------

homework_bp = Blueprint('homework', __name__, url_prefix='/api/homework')


@courses_bp.post('/<int:course_id>/homework')
@require_roles('admin')
def create_homework(course_id):
    """POST /api/courses/{id}/homework：新增班级共享作业（学生写由角色校验返回 403）。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    if _get_owned_course(db, course_id, class_id, sem_id) is None:
        return json_err('COURSE_NOT_FOUND', '课程不存在或不属于当前班级', 404)

    payload, err = _validate_homework_payload(request.get_json(silent=True))
    if err:
        return err

    try:
        homework_id = db.execute(
            """INSERT INTO homework (course_id, content, deadline, created_by, updated_by)
               VALUES (?,?,?,?,?)""",
            (course_id, payload['content'], payload['deadline'], user['id'], user['id']),
        ).lastrowid
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('create_homework failed')
        return json_err('INTERNAL_ERROR', '保存作业失败', 500)

    row = db.execute('SELECT * FROM homework WHERE id = ?', (homework_id,)).fetchone()
    return json_ok(_homework_dict(row), status=201)


def _get_owned_homework(db, homework_id, class_id, semester_id):
    return db.execute(
        """SELECT h.*, c.class_id AS course_class_id, c.semester_id AS course_semester_id
           FROM homework h JOIN courses c ON c.id = h.course_id
           WHERE h.id = ?""", (homework_id,)).fetchone()


@homework_bp.put('/<int:homework_id>')
@require_roles('admin')
def update_homework(homework_id):
    """PUT /api/homework/{id}：编辑作业内容与截止日期。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    row = _get_owned_homework(db, homework_id, class_id, sem_id)
    if row is None or row['course_class_id'] != class_id or row['course_semester_id'] != sem_id:
        return json_err('HOMEWORK_NOT_FOUND', '作业不存在或不属于当前班级', 404)

    payload, err = _validate_homework_payload(request.get_json(silent=True))
    if err:
        return err

    try:
        db.execute(
            'UPDATE homework SET content = ?, deadline = ?, updated_by = ? WHERE id = ?',
            (payload['content'], payload['deadline'], user['id'], homework_id))
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('update_homework failed')
        return json_err('INTERNAL_ERROR', '保存作业失败', 500)

    row = db.execute('SELECT * FROM homework WHERE id = ?', (homework_id,)).fetchone()
    return json_ok(_homework_dict(row))


@homework_bp.delete('/<int:homework_id>')
@require_roles('admin')
def delete_homework(homework_id):
    """DELETE /api/homework/{id}：删除作业。"""
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    row = _get_owned_homework(db, homework_id, class_id, sem_id)
    if row is None or row['course_class_id'] != class_id or row['course_semester_id'] != sem_id:
        return json_err('HOMEWORK_NOT_FOUND', '作业不存在或不属于当前班级', 404)

    try:
        db.execute('DELETE FROM homework WHERE id = ?', (homework_id,))
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('delete_homework failed')
        return json_err('INTERNAL_ERROR', '删除作业失败', 500)
    return json_ok({'deleted': True})


# ---------------------------------------------------------------------------
# 教师联想
# ---------------------------------------------------------------------------

teachers_bp = Blueprint('teachers', __name__, url_prefix='/api/teachers')


@teachers_bp.get('')
@require_roles('student', 'admin')
def list_teachers():
    """GET /api/teachers?query=&limit=：teachers 表 ∪ 本班课程已用教师名。"""
    user, semester, err = _biz_context()
    if err:
        return err
    query = _to_text(request.args.get('query'), 50)
    limit = _to_int(request.args.get('limit')) or 20
    limit = min(max(limit, 1), 50)
    like = f'%{query}%'
    rows = get_db().execute(
        """SELECT name FROM (
               SELECT name FROM teachers WHERE (? = '' OR name LIKE ?)
               UNION
               SELECT t.name FROM courses c JOIN teachers t ON t.id = c.teacher_id
               WHERE c.class_id = ? AND c.semester_id = ? AND (? = '' OR t.name LIKE ?)
           ) ORDER BY name LIMIT ?""",
        (query, like, user['class_id'], semester['id'], query, like, limit),
    ).fetchall()
    return json_ok({'items': [r['name'] for r in rows]})


# ---------------------------------------------------------------------------
# 邀请码重新生成
# ---------------------------------------------------------------------------

classes_me_bp = Blueprint('classes_me', __name__, url_prefix='/api/classes/me')


@classes_me_bp.post('/invite-code/regenerate')
@require_roles('admin')
def regenerate_invite_code():
    """POST /api/classes/me/invite-code/regenerate：仅当前班级 active admin。

    明文仅在本次响应返回一次；数据库只保存 SHA-256 摘要；旧码立即失效；
    不影响已激活账号与班级业务数据。
    """
    user = g.user
    if not user['class_id']:
        return json_err('FORBIDDEN', '当前账号未绑定班级', 403)
    db = get_db()
    cls = db.execute('SELECT * FROM classes WHERE id = ?', (user['class_id'],)).fetchone()
    if cls is None or cls['status'] != 'active':
        return json_err('CLASS_DISABLED', '班级已停用，禁止业务写入', 403)

    plain = generate_invite_code()
    digest = hash_invite_code(plain)
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(
            'UPDATE classes SET invite_code_hash = ?, invite_code_updated_at = ?, updated_at = ? '
            'WHERE id = ?',
            (digest, now, now, cls['id']))
        audit('invite_code.regenerate', 'class', cls['id'], cls['id'],
              detail={'class_code': cls['class_code']})
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('regenerate_invite_code failed')
        return json_err('INTERNAL_ERROR', '重新生成邀请码失败', 500)

    return json_ok({'invite_code': plain, 'invite_code_updated_at': now})


# ---------------------------------------------------------------------------
# 双 CSV 导入
# ---------------------------------------------------------------------------

import_bp = Blueprint('course_import', __name__, url_prefix='/api/import')


def _pick_file(*names):
    for n in names:
        f = request.files.get(n)
        if f is not None and (f.filename or ''):
            return f
    return None


def _read_csv_rows(storage, header_variants):
    """读取并解析 CSV，返回 (data_rows, err_response)。

    data_rows 为 [(行号, [单元格...])]，行号为文件内实际行号（表头为第 1 行）。
    支持 UTF-8 BOM、双引号与字段内逗号（标准 csv 模块）。
    """
    fname = os.path.basename(storage.filename or '') or 'CSV 文件'
    data = storage.read()
    if len(data) > MAX_CSV_BYTES:
        return None, json_err('IMPORT_FILE_TOO_LARGE', f'{fname} 超过 2MB 限制', 400)
    if not data:
        return None, json_err('IMPORT_FILE_EMPTY', f'{fname} 内容为空', 400)
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return None, json_err('IMPORT_FILE_ENCODING',
                              f'{fname} 编码错误，请使用 UTF-8 编码保存', 400)

    rows = [(n, [c.strip() for c in row])
            for n, row in enumerate(csv.reader(io.StringIO(text)), start=1)
            if any(c.strip() for c in row)]
    if not rows:
        return None, json_err('IMPORT_FILE_EMPTY', f'{fname} 内容为空', 400)

    header = rows[0][1]
    if header not in header_variants:
        expected = '，'.join(header_variants[0])
        return None, json_err('IMPORT_BAD_HEADER',
                              f'{fname} 表头不正确，应为：{expected}', 400,
                              details={'file': fname, 'expected_header': header_variants[0]})
    return rows[1:], None


def _sessions_overlap(a, b):
    return (a['day_of_week'] == b['day_of_week']
            and a['start_section'] <= b['end_section']
            and b['start_section'] <= a['end_section']
            and a['week_start'] <= b['week_end']
            and b['week_start'] <= a['week_end'])


@import_bp.post('/course-homework')
@require_roles('admin')
def import_course_homework():
    """POST /api/import/course-homework：双 CSV 统一校验 + 事务覆盖本班本学期。

    multipart/form-data 字段：course_file（课程 CSV）、homework_file（作业 CSV）。
    任一校验失败整体不保存并返回逐行错误（含文件名与行号）；全部通过后在一个
    事务内删除本班本学期全部 courses（级联 sessions/homework）并写入新数据。
    """
    user, semester, cls, err = _write_context()
    if err:
        return err
    db = get_db()
    class_id, sem_id = user['class_id'], semester['id']
    week_count = semester['week_count']

    course_file = _pick_file('course_file', 'courses_file', 'course_csv', 'course')
    homework_file = _pick_file('homework_file', 'homework_csv', 'homework')
    if course_file is None or homework_file is None:
        return json_err('IMPORT_FILES_MISSING',
                        '请同时上传课程 CSV（course_file）和作业 CSV（homework_file）', 400)
    for f in (course_file, homework_file):
        if not (os.path.basename(f.filename or '').lower().endswith('.csv')):
            return json_err('IMPORT_FILE_TYPE', '仅支持 .csv 文件', 400)

    course_fname = os.path.basename(course_file.filename) or '课程.csv'
    homework_fname = os.path.basename(homework_file.filename) or '作业.csv'

    course_rows, err = _read_csv_rows(course_file, COURSE_HEADER_VARIANTS)
    if err:
        return err
    homework_rows, err = _read_csv_rows(homework_file, HOMEWORK_HEADER_VARIANTS)
    if err:
        return err
    if len(course_rows) > MAX_COURSE_ROWS:
        return json_err('IMPORT_TOO_MANY_ROWS',
                        f'{course_fname} 数据行超过 {MAX_COURSE_ROWS} 行上限', 400)
    if len(homework_rows) > MAX_HOMEWORK_ROWS:
        return json_err('IMPORT_TOO_MANY_ROWS',
                        f'{homework_fname} 数据行超过 {MAX_HOMEWORK_ROWS} 行上限', 400)

    errors = []

    def add_error(file, line, message):
        if len(errors) < MAX_IMPORT_ERRORS:
            errors.append({'file': file, 'line': line, 'message': message})

    # ---- 课程文件逐行校验并按课程编号聚合 ----
    courses = {}
    for lineno, row in course_rows:
        if len(row) > len(COURSE_FIELDS):
            add_error(course_fname, lineno, '列数多于表头')
            continue
        rec = dict(zip(COURSE_FIELDS, row + [''] * (len(COURSE_FIELDS) - len(row))))
        row_ok = True

        code = rec['course_code']
        if not code:
            add_error(course_fname, lineno, '课程编号不能为空')
            row_ok = False
        elif not _COURSE_CODE_RE.match(code):
            add_error(course_fname, lineno, '课程编号仅允许字母、数字、_、-')
            row_ok = False
        if not rec['course_name']:
            add_error(course_fname, lineno, '课程名不能为空')
            row_ok = False
        if not rec['teacher']:
            add_error(course_fname, lineno, '教师不能为空')
            row_ok = False
        if not rec['classroom']:
            add_error(course_fname, lineno, '教室不能为空')
            row_ok = False

        day = _to_int(rec['day_of_week'])
        if day is None or not 1 <= day <= 7:
            add_error(course_fname, lineno, '星期必须为 1-7 的整数')
            row_ok = False

        ss, es = _to_int(rec['start_section']), _to_int(rec['end_section'])
        if ss is None or es is None:
            add_error(course_fname, lineno, '节次必须为整数')
            row_ok = False
        elif not (1 <= ss <= 12 and 1 <= es <= 12):
            add_error(course_fname, lineno, '节次必须在 1-12 之间')
            row_ok = False
        elif ss > es:
            add_error(course_fname, lineno, '开始节次不能大于结束节次')
            row_ok = False

        ws, we = _to_int(rec['week_start']), _to_int(rec['week_end'])
        if ws is None or we is None:
            add_error(course_fname, lineno, '周次必须为整数')
            row_ok = False
        elif ws < 1:
            add_error(course_fname, lineno, '起始周必须不小于 1')
            row_ok = False
        elif ws > we:
            add_error(course_fname, lineno, '起始周不能大于结束周')
            row_ok = False
        elif we > week_count:
            add_error(course_fname, lineno, f'结束周不能超过学期周数（{week_count}）')
            row_ok = False

        exam = None
        if rec['exam_date']:
            exam = _to_date(rec['exam_date'])
            if exam is None:
                add_error(course_fname, lineno, '考试日期必须为 YYYY-MM-DD')
                row_ok = False

        if not row_ok:
            continue

        entry = courses.get(code)
        if entry is None:
            entry = {'course_code': code, 'course_name': rec['course_name'],
                     'teacher': rec['teacher'], 'exam_date': exam,
                     'remark': rec['remark'], 'first_line': lineno,
                     'sessions': [], '_keys': set()}
            courses[code] = entry
        else:
            # 同一课程编号多行时课程级字段必须一致，否则指出冲突行
            for field, label in (('course_name', '课程名'), ('teacher', '教师'),
                                 ('exam_date', '考试日期'), ('remark', '备注')):
                new_val = exam if field == 'exam_date' else rec[field]
                if entry[field] != new_val:
                    add_error(course_fname, lineno,
                              f"{label}与第 {entry['first_line']} 行的同编号课程不一致")

        session = {'classroom': rec['classroom'], 'day_of_week': day,
                   'start_section': ss, 'end_section': es,
                   'week_start': ws, 'week_end': we, '_line': lineno}
        key = (session['classroom'], day, ss, es, ws, we)
        if key not in entry['_keys']:  # 完全相同的安排去重
            entry['_keys'].add(key)
            entry['sessions'].append(session)

    # ---- 作业文件逐行校验 ----
    homework_data = []
    seen_homework = set()
    for lineno, row in homework_rows:
        if len(row) > len(HOMEWORK_FIELDS):
            add_error(homework_fname, lineno, '列数多于表头')
            continue
        rec = dict(zip(HOMEWORK_FIELDS, row + [''] * (len(HOMEWORK_FIELDS) - len(row))))
        row_ok = True

        code = rec['course_code']
        if not code:
            add_error(homework_fname, lineno, '课程编号不能为空')
            row_ok = False
        elif code not in courses:
            add_error(homework_fname, lineno, f'课程编号 {code} 在课程文件中不存在')
            row_ok = False
        if not rec['content']:
            add_error(homework_fname, lineno, '作业内容不能为空')
            row_ok = False

        deadline = _to_date(rec['deadline'])
        if deadline is None:
            add_error(homework_fname, lineno, '截止日期必须为 YYYY-MM-DD')
            row_ok = False

        if not row_ok:
            continue
        dup_key = (code, rec['content'], deadline)
        if dup_key in seen_homework:
            add_error(homework_fname, lineno, '作业重复（同课程、同内容、同截止日期）')
            continue
        seen_homework.add(dup_key)
        homework_data.append({'course_code': code, 'content': rec['content'],
                              'deadline': deadline})

    # ---- 文件内跨课程时间冲突 ----
    flat = [(code, entry['course_name'], s)
            for code, entry in courses.items() for s in entry['sessions']]
    for i in range(len(flat)):
        for j in range(i + 1, len(flat)):
            code_a, name_a, sa = flat[i]
            code_b, _, sb = flat[j]
            if code_a != code_b and _sessions_overlap(sa, sb):
                add_error(course_fname, sb['_line'],
                          f"与课程《{name_a}》（第 {sa['_line']} 行）时间冲突")

    if errors:
        return json_err('IMPORT_VALIDATION_FAILED',
                        f'CSV 校验未通过（{len(errors)} 处错误），未导入任何数据', 400,
                        details={'errors': errors})

    # ---- 一个事务内先删后写，删除条件同时含 class_id + semester_id ----
    counts = {
        'courses': len(courses),
        'sessions': sum(len(e['sessions']) for e in courses.values()),
        'homework': len(homework_data),
    }
    try:
        old_ids = [r['id'] for r in db.execute(
            'SELECT id FROM courses WHERE class_id = ? AND semester_id = ?',
            (class_id, sem_id))]
        if old_ids:
            ph = ','.join('?' * len(old_ids))
            db.execute(f'DELETE FROM homework WHERE course_id IN ({ph})', old_ids)
            db.execute(f'DELETE FROM course_sessions WHERE course_id IN ({ph})', old_ids)
            db.execute('DELETE FROM courses WHERE class_id = ? AND semester_id = ?',
                       (class_id, sem_id))

        id_by_code = {}
        for code, entry in courses.items():
            teacher_id = _get_or_create_teacher(db, entry['teacher'])
            id_by_code[code] = db.execute(
                """INSERT INTO courses
                   (class_id, semester_id, course_code, course_name, teacher_id, exam_date, remark)
                   VALUES (?,?,?,?,?,?,?)""",
                (class_id, sem_id, code, entry['course_name'], teacher_id,
                 entry['exam_date'], entry['remark'])).lastrowid
            for s in entry['sessions']:
                _insert_session(db, id_by_code[code], s)
        for hw in homework_data:
            db.execute(
                """INSERT INTO homework (course_id, content, deadline, created_by, updated_by)
                   VALUES (?,?,?,?,?)""",
                (id_by_code[hw['course_code']], hw['content'], hw['deadline'],
                 user['id'], user['id']))

        audit('import.course_homework', 'class', class_id, class_id, detail=counts)
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('import_course_homework failed')
        return json_err('IMPORT_FAILED', '导入失败，已整体回滚，本班原有数据未变', 500)

    return json_ok(counts)


# ---------------------------------------------------------------------------
# 蓝图汇总（供 app.py 注册）
# ---------------------------------------------------------------------------

blueprints = (
    schedule_bp,
    courses_bp,
    course_sessions_bp,
    homework_bp,
    teachers_bp,
    classes_me_bp,
    import_bp,
)


def register_blueprints(app):
    for bp in blueprints:
        app.register_blueprint(bp)
