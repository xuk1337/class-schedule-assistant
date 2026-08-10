# -*- coding: utf-8 -*-
"""业务测试：课程多时段、时间冲突、作业 CRUD、邀请码重置、双 CSV 导入。"""
import hashlib
import io

from conftest import (
    ADMIN1_ID, ADMIN1_PW, INVITE1, STU1_NO, STU1_PW, STU2_NAME, STU2_NO,
    csrf, login_token,
)


def _admin(client):
    return login_token(client, ADMIN1_ID, ADMIN1_PW)


def _payload(code, name='新课程', teacher='新教师', sessions=None):
    return {
        'course_code': code,
        'course_name': name,
        'teacher': teacher,
        'sessions': sessions if sessions is not None else [
            {'classroom': 'D101', 'day_of_week': 2, 'start_section': 1,
             'end_section': 2, 'week_start': 1, 'week_end': 16},
        ],
    }


class TestCourseMultiSession:
    def test_create_course_with_multiple_sessions(self, client, seed):
        token = _admin(client)
        resp = client.post('/api/courses', json=_payload('HIST', '中国通史', '赵老师', [
            {'classroom': 'D201', 'day_of_week': 2, 'start_section': 1,
             'end_section': 2, 'week_start': 1, 'week_end': 16},
            {'classroom': 'D202', 'day_of_week': 4, 'start_section': 7,
             'end_section': 8, 'week_start': 1, 'week_end': 8},
        ]), headers=csrf(token))
        assert resp.status_code == 201
        body = resp.get_json()
        assert len(body['sessions']) == 2
        assert body['teacher_name'] == '赵老师'

        # 课程详情可查出两个时段
        detail = client.get(f"/api/courses/{body['id']}").get_json()
        assert len(detail['sessions']) == 2
        days = sorted(s['day_of_week'] for s in detail['sessions'])
        assert days == [2, 4]

    def test_schedule_query_returns_multi_sessions(self, client, seed):
        token = _admin(client)
        resp = client.get('/api/schedule', query_string={'week': 1})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['week'] == 1
        assert len(body['time_slots']) == 12
        math = next(c for c in body['courses'] if c['course_code'] == 'MATH')
        assert len(math['sessions']) == 2
        # 英语课只有 1-8 周，第 9 周不应出现
        resp = client.get('/api/schedule', query_string={'week': 9})
        codes = {c['course_code'] for c in resp.get_json()['courses']}
        assert 'ENGL' not in codes and 'MATH' in codes
        # 学生同样可查
        login_token(client, STU1_NO, STU1_PW)
        assert client.get('/api/schedule', query_string={'week': 1}).status_code == 200


class TestTimeConflict:
    def test_conflict_409_with_message(self, client, seed):
        token = _admin(client)
        # 与 MATH 周一 1-2 节（week 1-16）重叠：周一 2-3 节
        resp = client.post('/api/courses', json=_payload('CONF', '冲突课', '某老师', [
            {'classroom': 'D301', 'day_of_week': 1, 'start_section': 2,
             'end_section': 3, 'week_start': 1, 'week_end': 16},
        ]), headers=csrf(token))
        assert resp.status_code == 409
        body = resp.get_json()
        assert body['code'] == 'COURSE_TIME_CONFLICT'
        assert '时间冲突' in body['message']
        assert body['details']['conflict_course']['course_code'] == 'MATH'

    def test_adjacent_sections_no_conflict(self, client, seed):
        token = _admin(client)
        # MATH 周一 1-2 节结束后再排 3-4 节：首尾相接不算冲突
        resp = client.post('/api/courses', json=_payload('ADJ', '相邻课', '某老师', [
            {'classroom': 'D302', 'day_of_week': 1, 'start_section': 3,
             'end_section': 4, 'week_start': 1, 'week_end': 16},
        ]), headers=csrf(token))
        assert resp.status_code == 201

    def test_week_range_not_overlapping_no_conflict(self, client, seed):
        token = _admin(client)
        # 与 MATH 同时段但周次不重叠不可能（MATH 1-16 全覆盖），改与 ENGL 比较：
        # ENGL 周五 5-6 节 week 1-8，排周五 5-6 节 week 9-16 不冲突
        resp = client.post('/api/courses', json=_payload('WEEKOK', '后半学期课', '某老师', [
            {'classroom': 'B101', 'day_of_week': 5, 'start_section': 5,
             'end_section': 6, 'week_start': 9, 'week_end': 16},
        ]), headers=csrf(token))
        assert resp.status_code == 201

    def test_update_excludes_self(self, client, seed):
        token = _admin(client)
        # 原样整体保存 MATH（时段未变），不与自身冲突
        resp = client.put(f"/api/courses/{seed['course_math']}", json=_payload(
            'MATH', '数学分析', '王老师', [
                {'classroom': 'A101', 'day_of_week': 1, 'start_section': 1,
                 'end_section': 2, 'week_start': 1, 'week_end': 16},
                {'classroom': 'A102', 'day_of_week': 3, 'start_section': 3,
                 'end_section': 4, 'week_start': 1, 'week_end': 16},
            ]), headers=csrf(token))
        assert resp.status_code == 200
        assert len(resp.get_json()['sessions']) == 2

    def test_update_to_conflict_still_409(self, client, seed):
        token = _admin(client)
        # 把 ENGL 改到与 MATH 冲突的时段
        resp = client.put(f"/api/courses/{seed['course_eng']}", json=_payload(
            'ENGL', '大学英语', '李老师', [
                {'classroom': 'B101', 'day_of_week': 1, 'start_section': 1,
                 'end_section': 2, 'week_start': 1, 'week_end': 16},
            ]), headers=csrf(token))
        assert resp.status_code == 409
        assert '时间冲突' in resp.get_json()['message']

    def test_session_update_excludes_self(self, client, seed):
        token = _admin(client)
        sid = seed['session_math_1']
        resp = client.put(f'/api/course-sessions/{sid}', json={
            'classroom': 'A101', 'day_of_week': 1, 'start_section': 1,
            'end_section': 2, 'week_start': 1, 'week_end': 16,
        }, headers=csrf(token))
        assert resp.status_code == 200

    def test_session_create_and_delete(self, client, seed):
        token = _admin(client)
        resp = client.post(f"/api/courses/{seed['course_math']}/sessions", json={
            'classroom': 'A103', 'day_of_week': 5, 'start_section': 9,
            'end_section': 10, 'week_start': 9, 'week_end': 16,
        }, headers=csrf(token))
        assert resp.status_code == 201
        sid = resp.get_json()['id']
        detail = client.get(f"/api/courses/{seed['course_math']}").get_json()
        assert len(detail['sessions']) == 3
        resp = client.delete(f'/api/course-sessions/{sid}', headers=csrf(token))
        assert resp.status_code == 200
        detail = client.get(f"/api/courses/{seed['course_math']}").get_json()
        assert len(detail['sessions']) == 2

    def test_session_validation_errors_400(self, client, seed):
        token = _admin(client)
        # 开始节次 > 结束节次
        resp = client.post(f"/api/courses/{seed['course_math']}/sessions", json={
            'classroom': 'A103', 'day_of_week': 5, 'start_section': 10,
            'end_section': 9, 'week_start': 9, 'week_end': 16,
        }, headers=csrf(token))
        assert resp.status_code == 400
        # 结束周超过学期周数
        resp = client.post(f"/api/courses/{seed['course_math']}/sessions", json={
            'classroom': 'A103', 'day_of_week': 5, 'start_section': 9,
            'end_section': 10, 'week_start': 1, 'week_end': 17,
        }, headers=csrf(token))
        assert resp.status_code == 400

    def test_delete_course_cascade_counts(self, client, seed):
        token = _admin(client)
        resp = client.delete(f"/api/courses/{seed['course_math']}", headers=csrf(token))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['deleted'] is True
        assert body['deleted_sessions'] == 2
        assert body['deleted_homework'] == 1
        assert client.get(f"/api/courses/{seed['course_math']}").status_code == 404


class TestHomework:
    def test_homework_crud(self, client, seed):
        token = _admin(client)
        cid = seed['course_eng']
        # 创建
        resp = client.post(f'/api/courses/{cid}/homework', json={
            'content': '背诵第二单元课文', 'deadline': '2025-10-15',
        }, headers=csrf(token))
        assert resp.status_code == 201
        hw = resp.get_json()
        assert hw['created_by'] == seed['admin1']
        # 详情中可查（deadline 升序）
        detail = client.get(f'/api/courses/{cid}').get_json()
        assert any(h['id'] == hw['id'] for h in detail['homework'])
        # 更新
        resp = client.put(f"/api/homework/{hw['id']}", json={
            'content': '改背第三单元', 'deadline': '2025-10-20',
        }, headers=csrf(token))
        assert resp.status_code == 200
        assert resp.get_json()['content'] == '改背第三单元'
        assert resp.get_json()['deadline'] == '2025-10-20'
        # 删除
        resp = client.delete(f"/api/homework/{hw['id']}", headers=csrf(token))
        assert resp.status_code == 200
        detail = client.get(f'/api/courses/{cid}').get_json()
        assert all(h['id'] != hw['id'] for h in detail['homework'])

    def test_homework_validation_400(self, client, seed):
        token = _admin(client)
        resp = client.post(f"/api/courses/{seed['course_math']}/homework", json={
            'content': '', 'deadline': '2025-10-15',
        }, headers=csrf(token))
        assert resp.status_code == 400
        resp = client.post(f"/api/courses/{seed['course_math']}/homework", json={
            'content': 'x', 'deadline': '10月15日',
        }, headers=csrf(token))
        assert resp.status_code == 400


class TestInviteCodeRegenerate:
    def test_regenerate_invalidates_old_code(self, client, seed, db):
        token = _admin(client)
        resp = client.post('/api/classes/me/invite-code/regenerate', headers=csrf(token))
        assert resp.status_code == 200
        new_invite = resp.get_json()['invite_code']
        assert new_invite and new_invite != INVITE1

        # 数据库只存新码摘要，不含明文
        row = db.execute('SELECT invite_code_hash FROM classes WHERE id = ?',
                         (seed['class1'],)).fetchone()
        assert row['invite_code_hash'] == hashlib.sha256(new_invite.encode()).hexdigest()
        assert new_invite not in row['invite_code_hash']

        # 旧码失效
        resp = client.post('/api/auth/student-activate', json={
            'name': STU2_NAME, 'invite_code': INVITE1,
            'student_no': STU2_NO, 'password': 'NewPass123',
        })
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'INVITE_CODE_INVALID'

        # 新码可激活
        resp = client.post('/api/auth/student-activate', json={
            'name': STU2_NAME, 'invite_code': new_invite,
            'student_no': STU2_NO, 'password': 'NewPass123',
        })
        assert resp.status_code == 201

        # 审计日志不含明文
        audit_row = db.execute(
            "SELECT detail_json FROM audit_logs WHERE action = 'invite_code.regenerate'"
        ).fetchone()
        assert audit_row is not None
        assert new_invite not in (audit_row['detail_json'] or '')

    def test_regenerate_requires_admin(self, client):
        token = login_token(client, STU1_NO, STU1_PW)
        resp = client.post('/api/classes/me/invite-code/regenerate', headers=csrf(token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 双 CSV 导入
# ---------------------------------------------------------------------------

COURSE_CSV = (
    '课程编号,课程名,教师,教室,星期,开始节次,结束节次,起始周,结束周,考试日期,备注\n'
    'MATH2,高等数学,王老师,A201,1,1,2,1,16,2026-01-10,\n'
    # 同编号多行：课程级字段（课程名/教师/考试日期/备注）必须逐行一致（PRD §6.6）
    'MATH2,高等数学,王老师,A202,3,3,4,1,16,2026-01-10,\n'
    'ENG2,大学英语,李老师,B101,5,5,6,1,8,,\n'
).encode('utf-8')

HOMEWORK_CSV = (
    '课程编号,作业内容,截止日期\n'
    'MATH2,第一章习题,2025-10-08\n'
    'ENG2,背诵课文,2025-10-09\n'
).encode('utf-8')


def _import(client, token, course_bytes=COURSE_CSV, homework_bytes=HOMEWORK_CSV,
            course_name='courses.csv', homework_name='homework.csv'):
    return client.post(
        '/api/import/course-homework',
        data={
            'course_file': (io.BytesIO(course_bytes), course_name),
            'homework_file': (io.BytesIO(homework_bytes), homework_name),
        },
        content_type='multipart/form-data',
        headers=csrf(token),
    )


class TestCourseHomeworkImport:
    def test_import_success_counts_and_replace(self, client, seed):
        token = _admin(client)
        resp = _import(client, token)
        assert resp.status_code == 200
        assert resp.get_json() == {'courses': 2, 'sessions': 3, 'homework': 2}

        # 覆盖式导入：原 MATH/ENGL 已被替换删除
        assert client.get(f"/api/courses/{seed['course_math']}").status_code == 404
        assert client.get(f"/api/courses/{seed['course_eng']}").status_code == 404
        # 新数据可查，多时段聚合正确
        schedule = client.get('/api/schedule', query_string={'week': 1}).get_json()
        codes = {c['course_code'] for c in schedule['courses']}
        assert codes == {'MATH2', 'ENG2'}
        math2 = next(c for c in schedule['courses'] if c['course_code'] == 'MATH2')
        assert len(math2['sessions']) == 2
        assert math2['exam_date'] == '2026-01-10'
        hw_contents = {h['content'] for h in schedule['homework']}
        assert hw_contents == {'第一章习题', '背诵课文'}

    def test_import_csv_with_bom(self, client, seed):
        token = _admin(client)
        resp = _import(client, token,
                       course_bytes=b'\xef\xbb\xbf' + COURSE_CSV,
                       homework_bytes=b'\xef\xbb\xbf' + HOMEWORK_CSV)
        assert resp.status_code == 200
        assert resp.get_json() == {'courses': 2, 'sessions': 3, 'homework': 2}

    def test_import_error_rows_rollback_keeps_original(self, client, seed):
        token = _admin(client)
        bad_course = (
            '课程编号,课程名,教师,教室,星期,开始节次,结束节次,起始周,结束周,考试日期,备注\n'
            'OK1,正常课,老师甲,E101,2,1,2,1,16,,\n'
            'BAD1,坏星期,老师乙,E102,9,1,2,1,16,,\n'   # 星期越界
            'BAD2,坏节次,老师丙,E103,3,5,3,1,16,,\n'   # 开始节次 > 结束节次
        ).encode('utf-8')
        ok_hw = (
            '课程编号,作业内容,截止日期\n'
            'OK1,正常作业,2025-10-08\n'
        ).encode('utf-8')
        resp = _import(client, token, course_bytes=bad_course, homework_bytes=ok_hw)
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['code'] == 'IMPORT_VALIDATION_FAILED'
        assert len(body['details']['errors']) == 2
        # 整体回滚：种子课程与作业原样保留，OK1 也没有写入
        assert client.get(f"/api/courses/{seed['course_math']}").status_code == 200
        schedule = client.get('/api/schedule', query_string={'week': 1}).get_json()
        codes = {c['course_code'] for c in schedule['courses']}
        assert codes == {'MATH', 'ENGL'}

    def test_import_homework_unknown_course_code_rollback(self, client, seed):
        token = _admin(client)
        bad_hw = (
            '课程编号,作业内容,截止日期\n'
            'NOPE,孤作业,2025-10-08\n'
        ).encode('utf-8')
        resp = _import(client, token, homework_bytes=bad_hw)
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'IMPORT_VALIDATION_FAILED'
        assert client.get(f"/api/courses/{seed['course_math']}").status_code == 200

    def test_import_internal_time_conflict_rollback(self, client, seed):
        token = _admin(client)
        conflict_course = (
            '课程编号,课程名,教师,教室,星期,开始节次,结束节次,起始周,结束周,考试日期,备注\n'
            'CC1,课程一,老师甲,E101,2,1,2,1,16,,\n'
            'CC2,课程二,老师乙,E102,2,2,3,1,16,,\n'  # 与 CC1 同时段重叠
        ).encode('utf-8')
        resp = _import(client, token, course_bytes=conflict_course,
                       homework_bytes='课程编号,作业内容,截止日期\n'.encode('utf-8'))
        assert resp.status_code == 400
        assert client.get(f"/api/courses/{seed['course_math']}").status_code == 200

    def test_import_missing_files_400(self, client, seed):
        token = _admin(client)
        resp = client.post('/api/import/course-homework', data={},
                           content_type='multipart/form-data', headers=csrf(token))
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'IMPORT_FILES_MISSING'

    def test_import_requires_admin(self, client):
        token = login_token(client, STU1_NO, STU1_PW)
        resp = _import(client, token)
        assert resp.status_code == 403
