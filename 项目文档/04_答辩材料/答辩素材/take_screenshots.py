# -*- coding: utf-8 -*-
"""答辩 PPT 演示截图脚本（Playwright + Chromium，真实界面操作）。"""
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
SYSTEM_DIR = os.path.join(PROJECT_ROOT, '班级课表助手_正式系统')
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',
                      os.path.join(SYSTEM_DIR, '.ms-playwright'))

from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5000'
OUT = os.path.join(SCRIPT_DIR, 'screenshots')
os.makedirs(OUT, exist_ok=True)

STUDENT = ('2023301001', 'student123')
ADMIN = ('admin2301', 'admin1234')
SYSADMIN = ('sysadmin', 'sysadmin123')
INVITE = 'p9M-7atHWpk'  # 软件技术2301班邀请码（当前演示数据库）

done = []
failed = []


def shot(page, name, full_page=False, el=None):
    path = os.path.join(OUT, name)
    time.sleep(0.4)
    if el is not None:
        el.screenshot(path=path)
    else:
        page.screenshot(path=path, full_page=full_page)
    done.append(name)
    print('[OK]', name, flush=True)


def login_main(page, login_id, pwd, role='student'):
    """课表端登录（role: student/admin tab）。"""
    page.goto(BASE + '/', wait_until='networkidle')
    page.wait_for_selector('#login-btn')
    if role == 'admin':
        page.click('#tab-admin')
    page.fill('#login-id', login_id)
    page.fill('#login-pwd', pwd)
    page.click('#login-btn')
    page.wait_for_selector('#view-main:not(.hidden)', timeout=10000)
    page.wait_for_selector('.course-block', timeout=10000)


def ensure_week_has_courses(page):
    """当前周没课就往后/往前找有课的周。"""
    if page.locator('.course-block').count() > 0:
        return
    for _ in range(20):
        nxt = page.locator('#btn-week-next')
        if nxt.is_enabled():
            nxt.click()
            page.wait_for_selector('.course-block', timeout=3000)
            return
        prev = page.locator('#btn-week-prev')
        if prev.is_enabled():
            prev.click()
            page.wait_for_selector('.course-block', timeout=3000)
            return
        break


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1440, 'height': 900},
                                  device_scale_factor=2, locale='zh-CN')
        page = ctx.new_page()
        page.on('dialog', lambda d: d.accept())  # window.confirm 一律确认

        # ---------- 01 登录页 ----------
        page.goto(BASE + '/', wait_until='networkidle')
        page.wait_for_selector('#panel-login:not([hidden])')
        shot(page, '01_login.png')

        # ---------- 02 学生激活表单 ----------
        page.click('#btn-show-activate')
        page.wait_for_selector('#panel-activate:not([hidden])')
        page.fill('#activate-name', '王小明')
        page.fill('#activate-invite', INVITE)
        page.fill('#activate-no', '2023301006')
        page.fill('#activate-pwd', 'student123')
        page.fill('#activate-pwd2', 'student123')
        shot(page, '02_activate.png')

        # ---------- 03 学生周视图 ----------
        login_main(page, *STUDENT)
        ensure_week_has_courses(page)
        page.wait_for_selector('#banner-area .attention-body', timeout=5000)
        time.sleep(0.5)
        shot(page, '03_week_view.png', full_page=True)

        # ---------- 04 日视图 ----------
        page.click('#tab-day-view')
        page.wait_for_selector('#view-day:not(.hidden-view)')
        picked = False
        for d in range(1, 8):
            page.click('#day-selector .day-btn[data-day="%d"]' % d)
            time.sleep(0.2)
            if page.locator('#day-timeline-content .course-detail-card').count() > 0:
                picked = True
                break
        if not picked:
            raise RuntimeError('日视图没有找到有课的一天')
        shot(page, '04_day_view.png')

        # ---------- 05 学生课程详情弹窗（只读） ----------
        page.click('#tab-week-view')
        page.wait_for_selector('#view-week:not(.hidden-view)')
        page.locator('.course-block').first.click()
        page.wait_for_selector('#modal-detail.show .modal-dialog')
        page.wait_for_selector('#detail-sessions .session-item')
        # 学生弹窗天然只读（应用已修复：学生身份不渲染管理按钮），无需注入 CSS
        shot(page, '05_course_detail_student.png')
        page.click('#modal-detail .modal-close')

        # ---------- 15 PDF 打印样式（趁学生会话还在） ----------
        page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        page.emulate_media(media='print')
        time.sleep(0.3)
        shot(page, '15_print_view.png', full_page=True)
        page.emulate_media(media='screen')
        page.evaluate("window.dispatchEvent(new Event('afterprint'))")

        # ---------- 学生登出，换班级管理员 ----------
        page.click('#btn-logout')
        page.wait_for_selector('#view-login:not(.hidden)', timeout=10000)

        # ---------- 06 管理员周视图 ----------
        login_main(page, *ADMIN, role='admin')
        ensure_week_has_courses(page)
        time.sleep(0.5)
        shot(page, '06_admin_week_view.png', full_page=True)

        # ---------- 07 课程编辑弹窗（高等数学双时段） ----------
        math_block = page.locator('.course-block', has_text='高等数学').first
        math_block.click()
        page.wait_for_selector('#modal-detail.show .modal-dialog')
        page.click('#btn-detail-edit')
        page.wait_for_selector('#modal-course-form.show .modal-dialog')
        page.wait_for_selector('#session-rows .session-row')
        shot(page, '07_course_edit.png')
        page.click('#modal-course-form .modal-close')

        # ---------- 08 双 CSV 导入弹窗 ----------
        csv_course = '/tmp/demo_course.csv'
        csv_hw = '/tmp/demo_homework.csv'
        with open(csv_course, 'w', encoding='utf-8') as f:
            f.write('课程编号,课程名,教师,教室,星期,开始节次,结束节次,起始周,结束周,考试日期,备注\n')
        with open(csv_hw, 'w', encoding='utf-8') as f:
            f.write('课程编号,作业内容,截止日期\n')
        page.click('#btn-more')  # 新 UI：导入/班级设置收进「更多」菜单
        page.wait_for_selector('#more-menu:not([hidden])')
        page.click('#btn-import')
        page.wait_for_selector('#modal-import.show .modal-dialog')
        page.set_input_files('#file-course', csv_course)
        page.set_input_files('#file-homework', csv_hw)
        page.wait_for_selector('#file-course-name:not(:empty)')
        shot(page, '08_import_csv.png')
        page.click('#modal-import .modal-close')

        # ---------- 09 邀请码重新生成一次性展示 ----------
        page.click('#btn-more')  # 「更多」菜单
        page.wait_for_selector('#more-menu:not([hidden])')
        page.click('#btn-class-settings')
        page.wait_for_selector('#modal-class-settings.show .modal-dialog')
        page.click('#btn-regen-invite')  # confirm 由 dialog handler 自动确认
        page.wait_for_selector('#modal-invite.show .modal-dialog')
        page.wait_for_selector('#invite-code-text:not(:empty)')
        shot(page, '09_invite_code.png')
        page.click('#modal-invite .modal-close')

        # ---------- 10 课程详情-作业管理（管理员视角） ----------
        page.locator('.course-block').first.click()
        page.wait_for_selector('#modal-detail.show .modal-dialog')
        page.wait_for_selector('#modal-detail .homework-op-btn', timeout=8000)
        body = page.locator('#modal-detail .modal-body')
        body.evaluate('(el) => { el.scrollTop = el.scrollHeight; }')
        shot(page, '10_homework.png')
        page.click('#modal-detail .modal-close')

        ctx.close()

        # ---------- 系统管理后台（独立会话） ----------
        ctx2 = browser.new_context(viewport={'width': 1440, 'height': 900},
                                   device_scale_factor=2, locale='zh-CN')
        ap = ctx2.new_page()
        ap.on('dialog', lambda d: d.accept())
        ap.goto(BASE + '/admin', wait_until='networkidle')
        ap.wait_for_selector('#admin-login-btn')
        ap.fill('#admin-login-id', SYSADMIN[0])
        ap.fill('#admin-login-pwd', SYSADMIN[1])
        ap.click('#admin-login-btn')
        ap.wait_for_selector('#admin-app:not(.hidden)', timeout=10000)
        ap.wait_for_selector('#admin-content .data-table tbody tr', timeout=10000)

        # 11 学期管理（默认板块）
        shot(ap, '11_admin_semesters.png')

        # 12 班级管理
        ap.click('.admin-nav-btn[data-section="classes"]')
        ap.wait_for_selector('#admin-content .data-table tbody tr', timeout=10000)
        time.sleep(0.3)
        shot(ap, '12_admin_classes.png')

        # 13 用户管理（学生名单：账号状态/停用/转班/重置密码）
        ap.click('.admin-nav-btn[data-section="students"]')
        ap.wait_for_selector('#admin-content .data-table tbody tr', timeout=10000)
        time.sleep(0.3)
        shot(ap, '13_admin_users.png', full_page=True)

        # 14 审计日志
        ap.click('.admin-nav-btn[data-section="audit"]')
        ap.wait_for_selector('#admin-content .data-table tbody tr', timeout=10000)
        time.sleep(0.3)
        shot(ap, '14_admin_audit.png', full_page=True)

        ctx2.close()

        # ---------- 16 移动端响应式 ----------
        ctx3 = browser.new_context(viewport={'width': 390, 'height': 844},
                                   device_scale_factor=2, locale='zh-CN',
                                   is_mobile=True)
        mp = ctx3.new_page()
        mp.on('dialog', lambda d: d.accept())
        # 新 UI 移动端默认日视图，先登录再手动切到周视图
        mp.goto(BASE + '/', wait_until='networkidle')
        mp.wait_for_selector('#login-btn')
        mp.fill('#login-id', STUDENT[0])
        mp.fill('#login-pwd', STUDENT[1])
        mp.click('#login-btn')
        mp.wait_for_selector('#view-main:not(.hidden)', timeout=10000)
        mp.click('#tab-week-view')
        mp.wait_for_selector('#view-week:not(.hidden-view)')
        mp.wait_for_selector('.course-block', timeout=10000)
        ensure_week_has_courses(mp)
        tw = mp.locator('#view-week .table-wrap')
        tw.evaluate('(el) => { el.scrollLeft = Math.min(300, el.scrollWidth); }')
        time.sleep(0.3)
        shot(mp, '16_mobile.png')
        ctx3.close()

        browser.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('[FAIL]', repr(e), flush=True)
        failed.append(repr(e))
    print('=== done: %d, failed: %s ===' % (len(done), failed), flush=True)
    sys.exit(1 if failed else 0)
