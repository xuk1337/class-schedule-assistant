# -*- coding: utf-8 -*-
"""系统管理测试：学期、名单导入、管理员任命/交接、账号生命周期、审计日志。"""
import io
import json

from conftest import (
    ADMIN1_ID, ADMIN1_PW, INVITE1, STU1_NO, STU1_PW, SYSADMIN_ID, SYSADMIN_PW,
    csrf, login, login_token,
)


def _sys(client):
    return login_token(client, SYSADMIN_ID, SYSADMIN_PW)


class TestSemester:
    def test_create_non_monday_400(self, client):
        token = _sys(client)
        resp = client.post('/api/system/semesters', json={
            'name': '坏学期', 'start_date': '2025-09-02', 'week_count': 16,  # 周二
        }, headers=csrf(token))
        assert resp.status_code == 400
        assert resp.get_json()['details']['field'] == 'start_date'

    def test_create_week_count_out_of_range_400(self, client):
        token = _sys(client)
        resp = client.post('/api/system/semesters', json={
            'name': '坏学期', 'start_date': '2026-03-02', 'week_count': 31,
        }, headers=csrf(token))
        assert resp.status_code == 400
        assert resp.get_json()['details']['field'] == 'week_count'

    def test_active_semester_mutually_exclusive(self, client, seed):
        token = _sys(client)
        # 新建 active 学期：原有 active 学期应自动转为 inactive
        resp = client.post('/api/system/semesters', json={
            'name': '2026 春季学期', 'start_date': '2026-03-02',
            'week_count': 18, 'status': 'active',
        }, headers=csrf(token))
        assert resp.status_code == 201
        new_id = resp.get_json()['semester']['id']

        items = client.get('/api/system/semesters').get_json()['items']
        actives = [s for s in items if s['status'] == 'active']
        assert len(actives) == 1
        assert actives[0]['id'] == new_id

        # 再启用旧学期：新学期的 active 被互斥替换
        resp = client.patch(f"/api/system/semesters/{seed['semester']}",
                            json={'status': 'active'}, headers=csrf(token))
        assert resp.status_code == 200
        items = client.get('/api/system/semesters').get_json()['items']
        actives = [s for s in items if s['status'] == 'active']
        assert len(actives) == 1
        assert actives[0]['id'] == seed['semester']


class TestRosterImport:
    def test_import_json_success(self, client, seed, db):
        token = _sys(client)
        resp = client.post(f"/api/system/classes/{seed['class1']}/students/import", json={
            'rows': [
                {'name': '钱七', 'student_no': '2021010'},
                {'name': '孙八', 'student_no': '2021011'},
            ],
        }, headers=csrf(token))
        assert resp.status_code == 201
        assert resp.get_json()['imported'] == 2
        count = db.execute(
            "SELECT COUNT(*) FROM users WHERE class_id = ? AND role = 'student'",
            (seed['class1'],)).fetchone()[0]
        assert count == 5  # 种子 3 人 + 新导入 2 人
        row = db.execute("SELECT * FROM users WHERE student_no = '2021010'").fetchone()
        assert row['status'] == 'pending'
        assert row['password_hash'] is None

    def test_import_with_error_rows_rolls_back_entire_batch(self, client, seed, db):
        token = _sys(client)
        before = db.execute(
            "SELECT COUNT(*) FROM users WHERE class_id = ? AND role = 'student'",
            (seed['class1'],)).fetchone()[0]
        resp = client.post(f"/api/system/classes/{seed['class1']}/students/import", json={
            'rows': [
                {'name': '合法学生', 'student_no': '2021020'},
                {'name': '', 'student_no': '2021021'},          # 姓名为空
                {'name': '坏学号', 'student_no': 'abc'},          # 学号格式错
                {'name': '重复学号', 'student_no': '2021020'},    # 批内重复
                {'name': '撞库学号', 'student_no': STU1_NO},      # 与系统已有学号冲突
            ],
        }, headers=csrf(token))
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['code'] == 'ROSTER_INVALID'
        assert len(body['details']['errors']) == 4
        # 整批回滚：合法行也没有写入
        after = db.execute(
            "SELECT COUNT(*) FROM users WHERE class_id = ? AND role = 'student'",
            (seed['class1'],)).fetchone()[0]
        assert after == before

    def test_import_csv_multipart_with_bom(self, client, seed):
        token = _sys(client)
        csv_text = '姓名,学号\n周九,2021030\n'
        resp = client.post(
            f"/api/system/classes/{seed['class1']}/students/import",
            data={'file': (io.BytesIO(csv_text.encode('utf-8-sig')), 'roster.csv')},
            content_type='multipart/form-data',
            headers=csrf(token),
        )
        assert resp.status_code == 201
        assert resp.get_json()['imported'] == 1


class TestAdminAssignment:
    def test_assign_admin_with_generated_password(self, client, seed):
        token = _sys(client)
        # 不给密码时服务端生成一次性明文，仅在响应中出现
        resp = client.put(f"/api/system/classes/{seed['class2']}/admin", json={
            'login_id': 'admin2new', 'name': '新二班管理员',
        }, headers=csrf(token))
        assert resp.status_code == 200
        body = resp.get_json()
        generated = body['new_password']
        assert len(generated) >= 8
        assert body['admin']['role'] == 'admin'
        assert body['admin']['status'] == 'active'
        assert login(client, 'admin2new', generated).status_code == 200

    def test_handover_disables_old_admin(self, client, make_client, seed, db):
        # 旧管理员先建立会话
        old_client = make_client()
        old_token = login_token(old_client, ADMIN1_ID, ADMIN1_PW)

        token = _sys(client)
        resp = client.put(f"/api/system/classes/{seed['class1']}/admin", json={
            'login_id': 'admin1b', 'name': '一班新管理员', 'password': 'AdminB123',
        }, headers=csrf(token))
        assert resp.status_code == 200

        # 数据库：一班恰好只有一个 active admin（admin1b），旧账号 disabled
        rows = db.execute(
            "SELECT login_id, status FROM users WHERE class_id = ? AND role = 'admin'",
            (seed['class1'],)).fetchall()
        actives = [r['login_id'] for r in rows if r['status'] == 'active']
        assert actives == ['admin1b']
        assert dict((r['login_id'], r['status']) for r in rows)[ADMIN1_ID] == 'disabled'

        # 旧会话立即失权（带合法 CSRF 头仍 403）
        resp = old_client.post('/api/courses', json={
            'course_code': 'OLDA', 'course_name': '旧管理员课', 'teacher': 'x',
            'sessions': [{'classroom': 'D1', 'day_of_week': 6, 'start_section': 9,
                          'end_section': 10, 'week_start': 1, 'week_end': 16}],
        }, headers=csrf(old_token))
        assert resp.status_code == 403

        # 旧账号无法再登录，新账号可登录且有管理权限
        assert login(make_client(), ADMIN1_ID, ADMIN1_PW).status_code == 403
        new_client = make_client()
        new_token = login_token(new_client, 'admin1b', 'AdminB123')
        assert new_client.get('/api/schedule').status_code == 200
        assert new_token

    def test_assign_admin_already_active_in_other_class_409(self, client, seed):
        token = _sys(client)
        resp = client.put(f"/api/system/classes/{seed['class1']}/admin", json={
            'login_id': 'admin2',  # admin2 是 2 班 active 管理员
        }, headers=csrf(token))
        assert resp.status_code == 409

    def test_assign_admin_to_disabled_class_409(self, client, seed):
        token = _sys(client)
        resp = client.patch(f"/api/system/classes/{seed['class2']}",
                            json={'status': 'disabled'}, headers=csrf(token))
        assert resp.status_code == 200
        resp = client.put(f"/api/system/classes/{seed['class2']}/admin", json={
            'login_id': 'adminx', 'name': '某人', 'password': 'SomePass123',
        }, headers=csrf(token))
        assert resp.status_code == 409


class TestUserLifecycle:
    def test_disable_then_enable_student(self, client, make_client, seed):
        token = _sys(client)
        resp = client.patch(f"/api/system/users/{seed['stu_active']}",
                            json={'status': 'disabled'}, headers=csrf(token))
        assert resp.status_code == 200
        assert resp.get_json()['user']['status'] == 'disabled'
        # 停用后无法登录
        resp = login(make_client(), STU1_NO, STU1_PW)
        assert resp.status_code == 403
        assert resp.get_json()['code'] == 'ACCOUNT_DISABLED'
        # 启用后恢复
        resp = client.patch(f"/api/system/users/{seed['stu_active']}",
                            json={'status': 'active'}, headers=csrf(token))
        assert resp.status_code == 200
        assert login(make_client(), STU1_NO, STU1_PW).status_code == 200

    def test_cannot_disable_system_admin(self, client, seed):
        token = _sys(client)
        resp = client.patch(f"/api/system/users/{seed['sysadmin']}",
                            json={'status': 'disabled'}, headers=csrf(token))
        assert resp.status_code == 403

    def test_transfer_student_to_another_class(self, client, seed, db):
        token = _sys(client)
        resp = client.patch(f"/api/system/users/{seed['stu_pending']}",
                            json={'class_id': seed['class2']}, headers=csrf(token))
        assert resp.status_code == 200
        assert resp.get_json()['user']['class_id'] == seed['class2']
        row = db.execute('SELECT class_id FROM users WHERE id = ?',
                         (seed['stu_pending'],)).fetchone()
        assert row['class_id'] == seed['class2']

    def test_transfer_to_missing_class_400(self, client, seed):
        token = _sys(client)
        resp = client.patch(f"/api/system/users/{seed['stu_active']}",
                            json={'class_id': 999999}, headers=csrf(token))
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'TARGET_CLASS_INVALID'

    def test_transfer_admin_rejected_400(self, client, seed):
        token = _sys(client)
        resp = client.patch(f"/api/system/users/{seed['admin1']}",
                            json={'class_id': seed['class2']}, headers=csrf(token))
        assert resp.status_code == 400

    def test_role_change_rejected_400(self, client, seed):
        token = _sys(client)
        resp = client.patch(f"/api/system/users/{seed['stu_active']}",
                            json={'role': 'admin'}, headers=csrf(token))
        assert resp.status_code == 400

    def test_reset_password(self, client, make_client, seed):
        token = _sys(client)
        resp = client.post(f"/api/system/users/{seed['stu_active']}/reset-password",
                           headers=csrf(token))
        assert resp.status_code == 200
        new_pw = resp.get_json()['new_password']
        assert len(new_pw) >= 8
        # 旧密码失效，新密码可用
        assert login(make_client(), STU1_NO, STU1_PW).status_code == 401
        assert login(make_client(), STU1_NO, new_pw).status_code == 200


class TestAuditLog:
    def test_audit_records_written_and_free_of_secrets(self, client, make_client, seed):
        token = _sys(client)
        # 制造一组管理动作
        client.post('/api/system/semesters', json={
            'name': '审计学期', 'start_date': '2026-03-02', 'week_count': 10,
        }, headers=csrf(token))
        client.post(f"/api/system/classes/{seed['class1']}/students/import", json={
            'rows': [{'name': '审计生', 'student_no': '2021099'}],
        }, headers=csrf(token))
        client.put(f"/api/system/classes/{seed['class1']}/admin", json={
            'login_id': 'auditadmin', 'name': '审计管理员', 'password': 'AuditPw123',
        }, headers=csrf(token))
        reset_resp = client.post(
            f"/api/system/users/{seed['stu_active']}/reset-password", headers=csrf(token))
        new_pw = reset_resp.get_json()['new_password']
        client.patch(f"/api/system/users/{seed['stu_active']}",
                     json={'status': 'disabled'}, headers=csrf(token))

        resp = client.get('/api/system/audit-logs', query_string={'page_size': 100})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['total'] >= 5
        actions = {item['action'] for item in body['items']}
        assert {'semester_create', 'students_import', 'admin_assign',
                'password_reset', 'user_update'} <= actions

        # 审计日志整体不得包含任何密码或邀请码明文
        blob = json.dumps(body, ensure_ascii=False)
        for secret in (SYSADMIN_PW, ADMIN1_PW, STU1_PW, 'AuditPw123', new_pw, INVITE1):
            assert secret not in blob

    def test_audit_logs_filter_by_action(self, client, seed):
        token = _sys(client)
        client.patch(f"/api/system/users/{seed['stu_active']}",
                     json={'status': 'disabled'}, headers=csrf(token))
        resp = client.get('/api/system/audit-logs',
                          query_string={'action': 'user_update'})
        body = resp.get_json()
        assert body['total'] >= 1
        assert all(item['action'] == 'user_update' for item in body['items'])

    def test_audit_logs_require_system_admin(self, client):
        login_token(client, ADMIN1_ID, ADMIN1_PW)
        assert client.get('/api/system/audit-logs').status_code == 403
