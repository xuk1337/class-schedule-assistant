-- 班级课表助手 · 数据库 schema（SQLite）
-- 依据 PRD §6.9：9 张表、外键、检查约束、唯一/部分唯一索引。
-- 幂等：全部使用 IF NOT EXISTS / INSERT OR IGNORE，可重复执行。
-- 日期一律 'YYYY-MM-DD'，时间戳为带时区的 ISO 8601（UTC）。

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 学期：start_date 必须为周一，week_count 1-30；同一时刻仅一个 active 学期
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS semesters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    start_date  TEXT NOT NULL
                CHECK (COALESCE(date(start_date) = start_date AND strftime('%w', start_date) = '1', 0)),
    week_count  INTEGER NOT NULL CHECK (week_count BETWEEN 1 AND 30),
    status      TEXT NOT NULL DEFAULT 'inactive' CHECK (status IN ('active', 'inactive')),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

-- 部分唯一索引：同一时刻仅一个 status='active' 的学期
CREATE UNIQUE INDEX IF NOT EXISTS uq_semesters_single_active
    ON semesters (status) WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- 班级：class_code 全局唯一；只保存邀请码摘要，不存明文
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS classes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    class_code             TEXT NOT NULL UNIQUE,
    class_name             TEXT NOT NULL,
    semester_id            INTEGER REFERENCES semesters (id),
    invite_code_hash       TEXT,
    invite_code_updated_at TEXT,
    status                 TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

-- ---------------------------------------------------------------------------
-- 用户：login_id / student_no 全局唯一；student、admin 必须绑定班级，
-- system_admin.class_id 必须为空；待激活学生密码为空
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login_id      TEXT NOT NULL UNIQUE,
    student_no    TEXT UNIQUE,
    name          TEXT NOT NULL,
    password_hash TEXT,
    role          TEXT NOT NULL CHECK (role IN ('student', 'admin', 'system_admin')),
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'disabled')),
    class_id      INTEGER REFERENCES classes (id),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    CHECK (
        (role = 'system_admin' AND class_id IS NULL)
        OR (role IN ('student', 'admin') AND class_id IS NOT NULL)
    ),
    CHECK (role <> 'student' OR student_no IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_users_class ON users (class_id);

-- 部分唯一索引：每班仅一个 status='active' 的 role='admin'
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_one_active_admin_per_class
    ON users (class_id) WHERE role = 'admin' AND status = 'active';

-- ---------------------------------------------------------------------------
-- 教师：姓名唯一，用于教师输入联想；删除前须确认无课程引用
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teachers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

-- ---------------------------------------------------------------------------
-- 节次时间：预置第 1-12 节显示时间（上午 4 节 / 下午 4 节 / 晚上 4 节）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS time_slots (
    section_no INTEGER PRIMARY KEY CHECK (section_no BETWEEN 1 AND 12),
    start_time TEXT NOT NULL
               CHECK (COALESCE(length(start_time) = 5 AND time(start_time) IS NOT NULL, 0)),
    end_time   TEXT NOT NULL
               CHECK (COALESCE(length(end_time) = 5 AND time(end_time) IS NOT NULL, 0))
);

INSERT OR IGNORE INTO time_slots (section_no, start_time, end_time) VALUES
    (1,  '08:00', '08:45'),
    (2,  '08:55', '09:40'),
    (3,  '10:00', '10:45'),
    (4,  '10:55', '11:40'),
    (5,  '14:00', '14:45'),
    (6,  '14:55', '15:40'),
    (7,  '16:00', '16:45'),
    (8,  '16:55', '17:40'),
    (9,  '19:00', '19:45'),
    (10, '19:55', '20:40'),
    (11, '20:50', '21:35'),
    (12, '21:45', '22:30');

-- ---------------------------------------------------------------------------
-- 课程：class_id + semester_id + course_code 唯一；删除时级联删除安排与作业
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id    INTEGER NOT NULL REFERENCES classes (id),
    semester_id INTEGER NOT NULL REFERENCES semesters (id),
    course_code TEXT NOT NULL,
    course_name TEXT NOT NULL,
    teacher_id  INTEGER REFERENCES teachers (id),
    exam_date   TEXT CHECK (exam_date IS NULL OR COALESCE(date(exam_date) = exam_date, 0)),
    remark      TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    UNIQUE (class_id, semester_id, course_code)
);

CREATE INDEX IF NOT EXISTS idx_courses_class_semester ON courses (class_id, semester_id);

-- ---------------------------------------------------------------------------
-- 上课安排：一门课程可有多条；星期 1-7、节次 1-12、周次 1-30 且起止有序
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS course_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     INTEGER NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    classroom     TEXT NOT NULL,
    day_of_week   INTEGER NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    start_section INTEGER NOT NULL CHECK (start_section BETWEEN 1 AND 12),
    end_section   INTEGER NOT NULL CHECK (end_section BETWEEN 1 AND 12),
    week_start    INTEGER NOT NULL CHECK (week_start BETWEEN 1 AND 30),
    week_end      INTEGER NOT NULL CHECK (week_end BETWEEN 1 AND 30),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    CHECK (start_section <= end_section),
    CHECK (week_start <= week_end)
);

CREATE INDEX IF NOT EXISTS idx_course_sessions_course ON course_sessions (course_id);
CREATE INDEX IF NOT EXISTS idx_course_sessions_day ON course_sessions (day_of_week);

-- ---------------------------------------------------------------------------
-- 作业：属于课程并随课程级联删除；本期不保存学生个人完成状态
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS homework (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    content    TEXT NOT NULL,
    deadline   TEXT NOT NULL CHECK (COALESCE(date(deadline) = deadline, 0)),
    created_by INTEGER REFERENCES users (id),
    updated_by INTEGER REFERENCES users (id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_homework_course ON homework (course_id);
CREATE INDEX IF NOT EXISTS idx_homework_deadline ON homework (deadline);

-- ---------------------------------------------------------------------------
-- 审计日志：只追加不修改；不记录密码、密码哈希或邀请码明文
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users (id),
    action        TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    target_id     INTEGER,
    class_id      INTEGER,
    result        TEXT NOT NULL DEFAULT 'success',
    detail_json   TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_class ON audit_logs (class_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs (actor_user_id);
