# -*- coding: utf-8 -*-
"""认证与授权测试：学生激活、登录限流、CSRF、角色与班级隔离、未登录。"""
from conftest import (
    ADMIN1_ID, ADMIN1_PW, ADMIN2_ID, ADMIN2_PW,
    INVITE1, STU1_NO, STU1_PW, STU2_NAME, STU2_NO,
    STU4_NO, STU4_PW, SYSADMIN_ID, SYSADMIN_PW,
    csrf, login, login_token,
)

NEW_PW = 'NewPass123'


def _activate(client, name=STU2_NAME, invite=INVITE1, student_no=STU2_NO, password=NEW_PW):
    return client.post('/api/auth/student-activate', json={
        'name': name, 'invite_code': invite, 'student_no': student_no, 'password': password,
    })


class TestStudentActivate:
    def test_activate_success(self, client):
        resp = _activate(client)
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['user']['status'] == 'active'
        assert data['user']['student_no'] == STU2_NO
        assert data['class']['class_code'] == 'C001'
        assert data['csrf_token']
        # 激活即建立会话，可直接访问课表
        assert client.get('/api/schedule').status_code == 200
        # 新密码可登录
        assert login(client, STU2_NO, NEW_PW).status_code == 200

    def test_activate_mismatch_uniform_error(self, client):
        # 姓名不符与学号不在名单，返回统一的 400，不泄露细节
        r1 = _activate(client, name='不叫李四')
        r2 = _activate(client, student_no='2099999')
        for r in (r1, r2):
            assert r.status_code == 400
            assert r.get_json()['code'] == 'ROSTER_MISMATCH'
        assert r1.get_json()['message'] == r2.get_json()['message']

    def test_activate_invalid_invite_code(self, client):
        resp = _activate(client, invite='NO-SUCH-CODE')
        assert resp.status_code == 400
        assert resp.get_json()['code'] == 'INVITE_CODE_INVALID'

    def test_activate_already_active_409(self, client):
        resp = _activate(client, name='张三', student_no=STU1_NO)
        assert resp.status_code == 409
        assert resp.get_json()['code'] == 'ALREADY_ACTIVE'

    def test_activate_disabled_403(self, client):
        resp = _activate(client, name='赵六', student_no=STU4_NO)
        assert resp.status_code == 403
        assert resp.get_json()['code'] == 'ACCOUNT_DISABLED'

    def test_activate_validation_errors(self, client):
        # 学号格式错误
        resp = _activate(client, student_no='abc')
        assert resp.status_code == 400
        assert '学号' in resp.get_json()['message']
        # 密码不足 8 位
        resp = _activate(client, password='short')
        assert resp.status_code == 400
        assert '密码' in resp.get_json()['message']
        # 缺字段
        resp = client.post('/api/auth/student-activate', json={'name': STU2_NAME})
        assert resp.status_code == 400


class TestLogin:
    def test_login_success_and_me(self, client):
        token = login_token(client, STU1_NO, STU1_PW)
        assert token
        me = client.get('/api/auth/me')
        assert me.status_code == 200
        data = me.get_json()
        assert data['user']['role'] == 'student'
        assert data['semester']['status'] == 'active'

    def test_login_wrong_password_401(self, client):
        resp = login(client, STU1_NO, 'WrongPass1')
        assert resp.status_code == 401
        assert resp.get_json()['code'] == 'INVALID_CREDENTIALS'

    def test_login_disabled_account_403(self, client):
        resp = login(client, STU4_NO, STU4_PW)
        assert resp.status_code == 403
        assert resp.get_json()['code'] == 'ACCOUNT_DISABLED'

    def test_login_rate_limit_429(self, client):
        for _ in range(5):
            resp = login(client, SYSADMIN_ID, 'bad-password')
            assert resp.status_code == 401
        # 第 6 次触发锁定
        resp = login(client, SYSADMIN_ID, 'bad-password')
        assert resp.status_code == 429
        assert resp.get_json()['code'] == 'LOGIN_LOCKED'
        # 锁定期间即使密码正确也拒绝
        resp = login(client, SYSADMIN_ID, SYSADMIN_PW)
        assert resp.status_code == 429


class TestCsrf:
    def test_write_without_csrf_token_403(self, client):
        login_token(client, ADMIN1_ID, ADMIN1_PW)
        payload = _course_payload('NOCSRF')
        resp = client.post('/api/courses', json=payload)
        assert resp.status_code == 403
        assert resp.get_json()['code'] == 'CSRF_INVALID'

    def test_write_with_wrong_csrf_token_403(self, client):
        login_token(client, ADMIN1_ID, ADMIN1_PW)
        resp = client.post('/api/courses', json=_course_payload('NOCSRF'),
                           headers=csrf('forged-token'))
        assert resp.status_code == 403
        assert resp.get_json()['code'] == 'CSRF_INVALID'

    def test_login_and_activate_are_csrf_exempt(self, client):
        # 登录与激活未建立会话，豁免 CSRF（不带头也不应 403）
        assert login(client, STU1_NO, STU1_PW).status_code == 200
        assert _activate(client).status_code == 201


class TestAuthorization:
    def test_student_write_forbidden_403(self, client, seed):
        token = login_token(client, STU1_NO, STU1_PW)
        resp = client.post('/api/courses', json=_course_payload('STUX'), headers=csrf(token))
        assert resp.status_code == 403
        resp = client.post(f"/api/courses/{seed['course_math']}/homework",
                           json={'content': '学生提交', 'deadline': '2025-10-02'},
                           headers=csrf(token))
        assert resp.status_code == 403
        # 学生也不能访问系统管理接口
        assert client.get('/api/system/users', headers=csrf(token)).status_code == 403

    def test_admin_cannot_access_system_api_403(self, client):
        login_token(client, ADMIN1_ID, ADMIN1_PW)
        assert client.get('/api/system/semesters').status_code == 403

    def test_cross_class_course_detail_404(self, client, make_client, seed):
        # 2 班管理员访问 1 班课程
        c2_client = make_client()
        login_token(c2_client, ADMIN2_ID, ADMIN2_PW)
        resp = c2_client.get(f"/api/courses/{seed['course_math']}")
        assert resp.status_code == 404
        assert resp.get_json()['code'] == 'COURSE_NOT_FOUND'
        # 1 班学生访问 2 班课程
        login_token(client, STU1_NO, STU1_PW)
        assert client.get(f"/api/courses/{seed['course_phys']}").status_code == 404

    def test_unauthenticated_401(self, client, seed):
        assert client.get('/api/schedule').status_code == 401
        assert client.get(f"/api/courses/{seed['course_math']}").status_code == 401
        assert client.get('/api/system/users').status_code == 401
        for resp in (client.get('/api/schedule'), client.get('/api/system/users')):
            assert resp.get_json()['code'] == 'AUTH_REQUIRED'


def _course_payload(code):
    return {
        'course_code': code,
        'course_name': '权限测试课',
        'teacher': '测试教师',
        'sessions': [
            {'classroom': 'D101', 'day_of_week': 6, 'start_section': 9,
             'end_section': 10, 'week_start': 1, 'week_end': 16},
        ],
    }
