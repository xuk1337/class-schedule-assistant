#!/usr/bin/env python3
"""开发环境种子数据：清空业务数据并重新灌入演示数据，保证首页开箱即有内容。

幂等：先清空全部业务表（保留 time_slots 预置数据）再重灌，可反复运行。
严禁在生产环境运行（APP_ENV=production 时直接拒绝执行）。

用法：
    python scripts/seed_dev.py
运行后打印全部演示账号、密码和两个班级的演示邀请码（仅供开发环境）。
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from app import create_app
from db import get_db
from utils import generate_invite_code, hash_invite_code

# 演示账号密码（仅供开发环境，README 同步记录；生产禁止出现）
SYSADMIN_PASSWORD = 'sysadmin123'
ADMIN_PASSWORD = 'admin1234'
STUDENT_PASSWORD = 'student123'

# 本机运行环境（LibreSSL）的 hashlib 无 scrypt，Werkzeug 默认算法会报错，统一显式指定 pbkdf2
HASH_METHOD = 'pbkdf2:sha256'

# 清库顺序：子表在前，保留 time_slots 预置数据
_CLEAR_TABLES = [
    'audit_logs', 'homework', 'course_sessions', 'courses',
    'users', 'teachers', 'classes', 'semesters',
]


def main():
    if os.environ.get('APP_ENV') == 'production':
        print('错误：种子脚本仅限开发环境，禁止在生产环境运行', file=sys.stderr)
        return 1

    app = create_app()
    today = date.today()
    # 学期第一周周一：今天往前约 3 周的某个周一，保证任意当前周（约第 4 周）都有课
    semester_start = today - timedelta(days=today.weekday() + 21)
    current_week = (today - semester_start).days // 7 + 1

    with app.app_context():
        db = get_db()

        # ---- 先清库重灌，保证幂等 ----
        for table in _CLEAR_TABLES:
            db.execute(f'DELETE FROM {table}')
        db.execute(
            f"DELETE FROM sqlite_sequence WHERE name IN ({','.join('?' * len(_CLEAR_TABLES))})",
            _CLEAR_TABLES,
        )

        # ---- 学期 ----
        if semester_start.month >= 7:
            sem_name = f'{semester_start.year}-{semester_start.year + 1} 学年第一学期'
        else:
            sem_name = f'{semester_start.year - 1}-{semester_start.year} 学年第二学期'
        semester_id = db.execute(
            "INSERT INTO semesters (name, start_date, week_count, status) VALUES (?, ?, 20, 'active')",
            (sem_name, semester_start.isoformat()),
        ).lastrowid

        # ---- 班级（2 个班，验证数据隔离）----
        invite = {}  # class_id -> 明文邀请码（仅启动时打印一次）
        class_ids = {}
        for code, name in [('SE2301', '软件技术2301班'), ('SE2302', '软件技术2302班')]:
            plain = generate_invite_code()
            cid = db.execute(
                "INSERT INTO classes (class_code, class_name, semester_id, invite_code_hash,"
                " invite_code_updated_at, status) VALUES (?, ?, ?, ?, ?, 'active')",
                (code, name, semester_id, hash_invite_code(plain),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')),
            ).lastrowid
            class_ids[code] = cid
            invite[cid] = (name, plain)
        c1, c2 = class_ids['SE2301'], class_ids['SE2302']

        # ---- 用户：1 系统管理员 + 每班 1 班级管理员 + 每班 8 学生 ----
        sysadmin_id = db.execute(
            "INSERT INTO users (login_id, name, password_hash, role, status, class_id)"
            " VALUES ('sysadmin', '系统管理员', ?, 'system_admin', 'active', NULL)",
            (generate_password_hash(SYSADMIN_PASSWORD, method=HASH_METHOD),),
        ).lastrowid

        admin_ids = {}
        for login_id, name, cid in [('admin2301', '王梦琪', c1), ('admin2302', '李嘉航', c2)]:
            admin_ids[cid] = db.execute(
                "INSERT INTO users (login_id, name, password_hash, role, status, class_id)"
                " VALUES (?, ?, ?, 'admin', 'active', ?)",
                (login_id, name, generate_password_hash(ADMIN_PASSWORD, method=HASH_METHOD), cid),
            ).lastrowid

        students = {
            c1: ['陈思远', '刘雨桐', '张子涵', '王浩然', '李欣怡', '赵宇轩', '孙佳怡', '周文博'],
            c2: ['吴雅静', '郑皓月', '冯子墨', '褚晓彤', '卫明轩', '蒋梦琪', '沈浩然', '韩雨欣'],
        }
        for cid, names in students.items():
            prefix = '202330100' if cid == c1 else '202330200'
            for i, name in enumerate(names, start=1):
                active = i <= 5  # 前 5 名已激活，后 3 名待激活
                db.execute(
                    "INSERT INTO users (login_id, student_no, name, password_hash, role, status, class_id)"
                    " VALUES (?, ?, ?, ?, 'student', ?, ?)",
                    (
                        f'{prefix}{i}', f'{prefix}{i}', name,
                        generate_password_hash(STUDENT_PASSWORD, method=HASH_METHOD) if active else None,
                        'active' if active else 'pending',
                        cid,
                    ),
                )

        # ---- 教师 ----
        teacher_ids = {}
        for tname in ['张思源', '李慧敏', '王建国', '陈雅静', '刘志强', '赵雪琳', '孙立新', '周文斌']:
            teacher_ids[tname] = db.execute(
                'INSERT INTO teachers (name) VALUES (?)', (tname,)
            ).lastrowid

        # ---- 课程与上课安排 ----
        # (course_code, 课程名, 教师, exam_date 偏移天数或 None, 备注,
        #  [(教室, 星期, 开始节次, 结束节次, 起始周, 结束周), ...])
        courses_spec = {
            c1: [
                ('MATH-01', '高等数学', '张思源', 9, '考试时携带计算器',
                 [('教学楼301', 1, 1, 2, 1, 20), ('教学楼305', 3, 3, 4, 1, 20)]),
                ('ENG-01', '大学英语', '李慧敏', None, '',
                 [('外语楼102', 1, 3, 4, 1, 16)]),
                ('PE-01', '大学体育', '孙立新', None, '',
                 [('操场', 1, 5, 6, 1, 16)]),
                ('DS-01', '数据结构', '王建国', 13, '',
                 [('实验楼204', 2, 1, 2, 1, 20)]),
                ('PY-01', 'Python程序设计', '陈雅静', None, '机房上课，自带笔记本',
                 [('实训楼408', 2, 3, 4, 1, 16)]),
                ('MARX-01', '马克思主义基本原理', '周文斌', None, '',
                 [('文科楼201', 4, 7, 8, 5, 12)]),
                ('DB-01', '数据库原理', '刘志强', None, '',
                 [('实验楼301', 3, 5, 6, 1, 16)]),
                ('PHY-01', '大学物理', '赵雪琳', None, '第 3 周开始上课',
                 [('理科楼102', 5, 1, 2, 3, 18)]),
            ],
            c2: [
                ('MATH-01', '高等数学', '张思源', None, '',
                 [('教学楼302', 2, 1, 2, 1, 20), ('教学楼306', 4, 3, 4, 1, 20)]),
                ('ENG-01', '大学英语', '李慧敏', None, '',
                 [('外语楼103', 3, 1, 2, 1, 16)]),
                ('OS-01', '操作系统', '王建国', 8, '',
                 [('实验楼202', 1, 3, 4, 1, 16)]),
                ('NET-01', '计算机网络', '刘志强', None, '',
                 [('实验楼305', 5, 3, 4, 1, 16)]),
                ('LA-01', '线性代数', '赵雪琳', None, '',
                 [('教学楼401', 3, 5, 6, 1, 20)]),
                ('SE-01', '软件工程导论', '陈雅静', None, '含课程设计',
                 [('教学楼402', 4, 7, 8, 4, 16)]),
            ],
        }
        course_ids = {}  # (class_id, course_code) -> course_id
        for cid, specs in courses_spec.items():
            for code, name, teacher, exam_offset, remark, sessions in specs:
                course_id = db.execute(
                    'INSERT INTO courses (class_id, semester_id, course_code, course_name,'
                    ' teacher_id, exam_date, remark) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (
                        cid, semester_id, code, name, teacher_ids[teacher],
                        (today + timedelta(days=exam_offset)).isoformat() if exam_offset else None,
                        remark,
                    ),
                ).lastrowid
                course_ids[(cid, code)] = course_id
                for classroom, day, s1, s2, w1, w2 in sessions:
                    db.execute(
                        'INSERT INTO course_sessions (course_id, classroom, day_of_week,'
                        ' start_section, end_section, week_start, week_end)'
                        ' VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (course_id, classroom, day, s1, s2, w1, w2),
                    )

        # ---- 作业：含已逾期、2 天内到期、更远截止各若干 ----
        homework_spec = {
            c1: [  # (course_code, 内容, 截止日期偏移天数)
                ('MATH-01', '习题集第 5 章第 1-20 题', -2),
                ('MATH-01', '期中复习报告', 14),
                ('PY-01', '实验报告 4：文件操作', -1),
                ('PY-01', '期末项目选题提交', 10),
                ('ENG-01', 'Unit 3 英语作文', 1),
                ('DS-01', '链表实验：单链表逆置', 2),
                ('DS-01', '第 7 章课后习题', 6),
            ],
            c2: [
                ('OS-01', '进程同步实验报告', 1),
                ('NET-01', 'Wireshark 抓包分析报告', -3),
                ('LA-01', '第 3 章习题 1-10', 5),
                ('SE-01', '需求分析文档初稿', 9),
            ],
        }
        for cid, items in homework_spec.items():
            for code, content, offset in items:
                db.execute(
                    'INSERT INTO homework (course_id, content, deadline, created_by, updated_by)'
                    ' VALUES (?, ?, ?, ?, ?)',
                    (course_ids[(cid, code)], content,
                     (today + timedelta(days=offset)).isoformat(),
                     admin_ids[cid], admin_ids[cid]),
                )

        # ---- 审计样例 ----
        import json
        audit_rows = [
            (sysadmin_id, 'semester.create', 'semester', semester_id, None,
             {'name': sem_name, 'start_date': semester_start.isoformat(), 'week_count': 20}),
            (sysadmin_id, 'class.create', 'class', c1, c1, {'class_code': 'SE2301'}),
            (sysadmin_id, 'class.create', 'class', c2, c2, {'class_code': 'SE2302'}),
            (admin_ids[c1], 'invite_code.regenerate', 'class', c1, c1, None),
        ]
        for actor, action, ttype, tid, class_id, detail in audit_rows:
            db.execute(
                "INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, class_id,"
                " result, detail_json) VALUES (?, ?, ?, ?, ?, 'success', ?)",
                (actor, action, ttype, tid, class_id,
                 json.dumps(detail, ensure_ascii=False) if detail else None),
            )

        db.commit()

    # ---- 打印演示账号与邀请码（明文仅此处展示一次）----
    print('=' * 64)
    print('开发种子数据已灌入（原业务数据已清空，可重复运行）')
    print(f'学期：{sem_name}｜第一周周一 {semester_start}｜共 20 周｜今天约为第 {current_week} 周')
    print()
    print('演示账号（以下账号与密码仅供开发环境，禁止用于生产）：')
    print(f'  系统管理员  sysadmin                / {SYSADMIN_PASSWORD}')
    print(f'  班级管理员  admin2301               / {ADMIN_PASSWORD}  （软件技术2301班）')
    print(f'  班级管理员  admin2302               / {ADMIN_PASSWORD}  （软件技术2302班）')
    print(f'  学生(已激活) 2023301001-2023301005   / {STUDENT_PASSWORD}（软件技术2301班）')
    print(f'              2023302001-2023302005   / {STUDENT_PASSWORD}（软件技术2302班）')
    print('  学生(待激活) 2023301006-2023301008、2023302006-2023302008')
    print('              （需凭下方邀请码 + 姓名 + 学号自行激活并设置密码）')
    print()
    print('班级演示邀请码（数据库只存摘要，明文仅此一次展示）：')
    for _, (cname, plain) in invite.items():
        print(f'  {cname}：{plain}')
    print('=' * 64)
    return 0


if __name__ == '__main__':
    sys.exit(main())
