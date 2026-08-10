/* ============================================================
 * 班级课表助手 · 课表端（登录/激活、周/日视图、提醒、课程与
 * 作业维护、双 CSV 导入、邀请码重置、PDF 导出）
 * 数据全部来自 Flask API；不使用 localStorage 保存业务数据。
 * 服务端数据一律 textContent / DOM 创建；图标走 ICON() 静态常量。
 * ============================================================ */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var h = API.h, clear = API.clear, toast = API.toast, icon = window.ICON;

  var PALETTE_SIZE = 10; // 课程色板数量，与 base.css 中 .cc-1 ~ .cc-10 一一对应
  var DAY_NAMES = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'];
  var DAY_SHORT = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  var ROLE_NAMES = { student: '学生', admin: '班级管理员', system_admin: '系统管理员' };
  var MAX_FILE_SIZE = 2 * 1024 * 1024;
  var MOBILE_MQ = window.matchMedia('(max-width: 768px)');

  var state = {
    user: null,
    semester: null,
    week: 1,
    viewMode: MOBILE_MQ.matches ? 'day' : 'week', // 移动端默认日视图
    viewModeTouched: false,  // 用户手动切换后不再随断点自动改
    day: todayDow(),         // 日视图选中的星期（1-7）
    search: '',
    schedule: null,          // 最近一次 GET /api/schedule 响应
    teachersLoaded: false,
    editingCourseId: null,   // 课程表单正在编辑的课程 id（null=新增）
    detailCourse: null,      // 详情弹窗当前课程
    printPrepared: false,
    printBackup: null
  };

  /* ================= 日期工具 ================= */
  function todayMid() { var d = new Date(); return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }
  function parseDate(s) { var p = String(s || '').split('-'); return new Date(+p[0], (+p[1]) - 1, +p[2]); }
  function todayDow() { return ((new Date().getDay() + 6) % 7) + 1; } // 周一=1 … 周日=7
  function fmtMD(d) { return (d.getMonth() + 1) + '/' + d.getDate(); }
  function diffDays(a, b) { return Math.round((a - b) / 86400000); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function currentWeekOf(sem) {
    if (!sem || !sem.start_date) return 1;
    var w = Math.floor(diffDays(todayMid(), parseDate(sem.start_date)) / 7) + 1;
    return clamp(w, 1, sem.week_count || 30);
  }
  /* 当前显示周 + 星期 → 实际日期 */
  function dateOfColumn(day) {
    var d = parseDate(state.semester.start_date);
    d.setDate(d.getDate() + (state.week - 1) * 7 + (day - 1));
    return d;
  }

  /* ================= 课程配色（h = h*31 + charCode，取 10 色） ================= */
  function courseColorClass(name) {
    var hash = 0;
    var s = String(name || '');
    for (var i = 0; i < s.length; i += 1) hash = hash * 31 + s.charCodeAt(i);
    return 'cc-' + (Math.abs(hash) % PALETTE_SIZE + 1);
  }

  /* ================= 作业状态着色 ================= */
  function hwStatus(deadline) {
    var left = diffDays(parseDate(deadline), todayMid());
    if (left < 0) return { cls: 'overdue', text: '已逾期 ' + (-left) + ' 天' };
    if (left <= 2) return { cls: 'due-soon', text: '剩 ' + left + ' 天' };
    return { cls: 'normal', text: '剩 ' + left + ' 天' };
  }

  /* ================= 启动与视图切换 ================= */
  API.onUnauthorized(function () {
    state.user = null;
    state.schedule = null;
    showLoginView();
  });

  function showLoginView() {
    $('view-main').classList.add('hidden');
    $('view-login').classList.remove('hidden');
    $('login-id').value = '';
    $('login-pwd').value = '';
    showPanel('login');
  }
  function showMainView() {
    $('view-login').classList.add('hidden');
    $('view-main').classList.remove('hidden');
  }
  function showPanel(which) {
    $('panel-login').hidden = which !== 'login';
    $('panel-activate').hidden = which !== 'activate';
  }

  function afterLogin(user) {
    if (!user) { showLoginView(); return; }
    if (user.role === 'system_admin') { window.location.href = '/admin'; return; }
    state.user = user;
    showMainView();
    applyRoleVisibility();
    renderUserBar();
    loadInitial();
  }

  function isAdmin() { return state.user && state.user.role === 'admin'; }

  function applyRoleVisibility() {
    var show = isAdmin();
    document.querySelectorAll('.admin-only').forEach(function (el) {
      el.classList.toggle('hidden', !show);
    });
  }

  /* ================= 登录 / 激活 ================= */
  var loginRole = 'student';

  function switchLoginTab(role) {
    loginRole = role;
    $('tab-student').classList.toggle('active', role === 'student');
    $('tab-admin').classList.toggle('active', role === 'admin');
    $('tab-student').setAttribute('aria-selected', String(role === 'student'));
    $('tab-admin').setAttribute('aria-selected', String(role === 'admin'));
    $('login-id-label').textContent = role === 'student' ? '学号' : '管理员账号';
    $('login-id').placeholder = role === 'student' ? '请输入学号' : '请输入管理员账号';
    $('login-id').value = '';
    $('login-pwd').value = '';
    hideError($('login-error'));
    $('student-activate-entry').hidden = role !== 'student';
    $('admin-note').hidden = role === 'student';
  }

  function showError(el, msg) { el.textContent = msg; el.classList.add('show'); el.classList.remove('success'); }
  function showSuccess(el, msg) { el.textContent = msg; el.classList.add('show'); el.classList.add('success'); }
  function hideError(el) { el.classList.remove('show'); }

  async function doLogin() {
    var id = $('login-id').value.trim();
    var pwd = $('login-pwd').value;
    hideError($('login-error'));
    if (!id || !pwd) {
      showError($('login-error'), '请输入' + (loginRole === 'student' ? '学号' : '管理员账号') + '和密码');
      return;
    }
    var btn = $('login-btn');
    btn.disabled = true;
    btn.textContent = '登录中…';
    try {
      var data = await API.post('/api/auth/login', { login_id: id, password: pwd });
      API.setSession(data || {});
      var user = API.getUser();
      if (!user) { await API.init().catch(function () { return null; }); user = API.getUser(); }
      afterLogin(user);
    } catch (err) {
      if (err.status === 401) {
        showError($('login-error'), loginRole === 'student' ? '学号或密码不正确' : '管理员账号或密码不正确');
      } else {
        showError($('login-error'), err.message || '登录失败，请稍后重试');
      }
    } finally {
      btn.disabled = false;
      btn.textContent = '登 录';
    }
  }

  async function doActivate() {
    var name = $('activate-name').value.trim();
    var invite = $('activate-invite').value.trim();
    var no = $('activate-no').value.trim();
    var pwd = $('activate-pwd').value;
    var pwd2 = $('activate-pwd2').value;
    var errEl = $('activate-error');
    hideError(errEl);
    if (!name || !invite || !no || !pwd || !pwd2) { showError(errEl, '请完整填写注册信息'); return; }
    if (!/^\d{6,20}$/.test(no)) { showError(errEl, '学号应为 6-20 位数字'); return; }
    if (pwd.length < 8) { showError(errEl, '密码至少 8 位'); return; }
    if (pwd !== pwd2) { showError(errEl, '两次输入的密码不一致'); return; }
    var btn = $('activate-btn');
    btn.disabled = true;
    btn.textContent = '激活中…';
    try {
      var data = await API.post('/api/auth/student-activate', {
        name: name, student_no: no, invite_code: invite, password: pwd
      });
      API.setSession(data || {});
      var user = API.getUser();
      if (!user) { await API.init().catch(function () { return null; }); user = API.getUser(); }
      if (user) {
        toast('激活成功', 'success');
        afterLogin(user);
      } else {
        // 激活成功但未建立会话：回到登录页
        showPanel('login');
        showSuccess($('login-error'), '激活成功，请使用学号和密码登录');
        $('activate-name').value = ''; $('activate-invite').value = '';
        $('activate-no').value = ''; $('activate-pwd').value = ''; $('activate-pwd2').value = '';
      }
    } catch (err) {
      if (!err.notified) showError(errEl, err.message || '激活失败，请稍后重试');
    } finally {
      btn.disabled = false;
      btn.textContent = '激 活';
    }
  }

  async function doLogout() {
    try { await API.post('/api/auth/logout'); } catch (e) { /* 忽略，本地状态照清 */ }
    API.clearSession();
    window.location.reload();
  }

  /* ================= 数据加载 ================= */
  async function loadInitial() {
    await API.withLoading((async function () {
      try {
        var probe = await API.get('/api/schedule?week=1');
        state.schedule = probe;
        state.semester = probe.semester || (API.getMeExtra() && API.getMeExtra().semester) || null;
        if (state.semester) {
          state.week = currentWeekOf(state.semester);
          if (state.week !== 1) {
            state.schedule = await API.get('/api/schedule?week=' + state.week);
          }
        }
      } catch (err) {
        state.schedule = null;
        state.semester = null;
        API.showError(err);
      }
      renderAll();
    })());
  }

  async function loadSchedule() {
    await API.withLoading((async function () {
      try {
        var data = await API.get('/api/schedule?week=' + state.week);
        state.schedule = data;
        if (data && data.semester) state.semester = data.semester;
      } catch (err) {
        API.showError(err);
      }
      renderAll();
    })());
  }

  function courses() {
    return (state.schedule && Array.isArray(state.schedule.courses)) ? state.schedule.courses : [];
  }
  function timeSlots() {
    return (state.schedule && Array.isArray(state.schedule.time_slots)) ? state.schedule.time_slots : [];
  }
  function slotOf(sectionNo) {
    var slots = timeSlots();
    for (var i = 0; i < slots.length; i += 1) {
      if (slots[i].section_no === sectionNo) return slots[i];
    }
    return slots[sectionNo - 1] || null;
  }
  /* 当前周在周的 课程+安排 展开列表 */
  function sessionsThisWeek() {
    var list = [];
    courses().forEach(function (c) {
      (c.sessions || []).forEach(function (s) {
        if (s.week_start <= state.week && state.week <= s.week_end) {
          list.push({ course: c, session: s });
        }
      });
    });
    return list;
  }
  /* 汇总作业：优先使用接口返回的聚合列表，否则从课程内嵌列表展开 */
  function allHomework() {
    var d = state.schedule || {};
    if (Array.isArray(d.homework)) return d.homework.slice();
    var out = [];
    courses().forEach(function (c) {
      (c.homework || []).forEach(function (hw) {
        out.push({
          id: hw.id, course_id: c.id, course_name: c.course_name,
          content: hw.content, deadline: hw.deadline
        });
      });
    });
    return out;
  }
  function findCourse(id) {
    var list = courses();
    for (var i = 0; i < list.length; i += 1) if (list[i].id === id) return list[i];
    return null;
  }
  /* 搜索过滤：课程名 / 教师 / 教室（当前周任一安排的教室） */
  function matchesSearch(course) {
    var q = state.search.toLowerCase();
    if (!q) return true;
    if (String(course.course_name || '').toLowerCase().indexOf(q) >= 0) return true;
    if (String(course.teacher_name || '').toLowerCase().indexOf(q) >= 0) return true;
    var hit = false;
    (course.sessions || []).forEach(function (s) {
      if (s.week_start <= state.week && state.week <= s.week_end &&
        String(s.classroom || '').toLowerCase().indexOf(q) >= 0) hit = true;
    });
    return hit;
  }

  /* ================= 渲染：整体 ================= */
  function renderAll() {
    renderUserBar();
    renderContext();
    renderBanner();
    renderToolbar();
    renderWeekView();
    renderDayView();
    applyViewVisibility();
  }

  function renderUserBar() {
    if (!state.user) return;
    $('user-role-tag').textContent = ROLE_NAMES[state.user.role] || '用户';
    $('user-class-tag').textContent = state.user.class_name || state.user.class_code || '未分班';
    $('user-name-display').textContent =
      (state.user.name || '') + '（' + (state.user.login_id || state.user.student_no || '') + '）';
  }

  function renderContext() {
    $('class-name-display').textContent =
      (state.user && (state.user.class_name || state.user.class_code)) || '—';
    $('semester-display').textContent = state.semester ? state.semester.name : '';
  }

  /* ================= 渲染：近期事项 ================= */
  function renderBanner() {
    // 考试倒计时：取 exam_date ≥ 今天且最近的一场
    var examEl = clear($('exam-reminder'));
    var today = todayMid();
    var upcoming = courses().filter(function (c) {
      return c.exam_date && parseDate(c.exam_date) >= today;
    }).sort(function (a, b) { return parseDate(a.exam_date) - parseDate(b.exam_date); });
    if (!upcoming.length) {
      examEl.textContent = '暂无考试安排';
    } else {
      var c = upcoming[0];
      var left = diffDays(parseDate(c.exam_date), today);
      examEl.appendChild(h('span', null, '距离《' + c.course_name + '》考试还有 '));
      examEl.appendChild(h('span', { class: 'exam-days' }, left + ' 天'));
    }

    // 作业提醒：截止升序，最多 5 条
    var hwEl = clear($('homework-reminder'));
    var list = allHomework().slice().sort(function (a, b) {
      return String(a.deadline).localeCompare(String(b.deadline));
    }).slice(0, 5);
    if (!list.length) {
      hwEl.textContent = '暂无作业';
    } else {
      list.forEach(function (hw) {
        var st = hwStatus(hw.deadline);
        hwEl.appendChild(h('div', { class: 'hw-item ' + st.cls },
          h('span', { class: 'hw-course' }, '[' + (hw.course_name || '课程') + ']'),
          h('span', null, hw.content),
          h('span', null, ' · ' + st.text)
        ));
      });
    }
  }

  /* ================= 渲染：工具栏 ================= */
  function renderToolbar() {
    var wc = (state.semester && state.semester.week_count) || 30;
    $('week-label').textContent = '第 ' + state.week + ' 周';
    $('btn-week-prev').disabled = state.week <= 1;
    $('btn-week-next').disabled = state.week >= wc;
    $('day-selector').hidden = state.viewMode !== 'day';
    $('tab-week-view').classList.toggle('active', state.viewMode === 'week');
    $('tab-day-view').classList.toggle('active', state.viewMode === 'day');
    $('tab-week-view').setAttribute('aria-selected', String(state.viewMode === 'week'));
    $('tab-day-view').setAttribute('aria-selected', String(state.viewMode === 'day'));
    document.querySelectorAll('#day-selector .day-btn').forEach(function (btn) {
      var selected = Number(btn.getAttribute('data-day')) === state.day;
      btn.classList.toggle('selected', selected);
      btn.setAttribute('aria-selected', String(selected));
    });
  }

  /* ================= 渲染：周视图 ================= */
  function renderWeekView() {
    // 表头：节次 + 7 天（日期 = 学期第一周周一 + (周-1)*7 + 偏移）
    var headRow = clear($('week-head-row'));
    headRow.appendChild(h('th', { class: 'th-period' }, '节次'));
    var todayStr = todayMid().toDateString();
    var todayCol = -1;
    for (var day = 1; day <= 7; day += 1) {
      var isToday = false;
      if (state.semester) isToday = dateOfColumn(day).toDateString() === todayStr;
      if (isToday) todayCol = day;
      var cls = (isToday ? 'today ' : '') + (day >= 6 ? 'weekend' : '');
      headRow.appendChild(h('th', { class: cls.trim() },
        h('span', { class: 'th-day' }, DAY_NAMES[day - 1]),
        h('span', { class: 'date-hint' }, state.semester ? fmtMD(dateOfColumn(day)) : '')
      ));
    }

    // 网格：连续节次合并为一个色块（rowspan）
    var entries = sessionsThisWeek();
    var startMap = {};  // 'day_section' → entry（块首）
    var covered = {};   // 被合并覆盖的格子
    entries.forEach(function (e) {
      var s = e.session;
      startMap[s.day_of_week + '_' + s.start_section] = e;
      for (var sec = s.start_section + 1; sec <= s.end_section; sec += 1) {
        covered[s.day_of_week + '_' + sec] = true;
      }
    });

    var visibleCount = 0;
    var tbody = clear($('schedule-body'));
    for (var section = 1; section <= 12; section += 1) {
      var tr = h('tr');
      var slot = slotOf(section);
      tr.appendChild(h('td', { class: 'period-label' },
        '第' + section + '节',
        slot && slot.start_time ? h('span', { class: 'time-range' }, slot.start_time + '-' + (slot.end_time || '')) : null
      ));
      for (var d = 1; d <= 7; d += 1) {
        if (covered[d + '_' + section]) continue;
        var td = h('td');
        var tdCls = [];
        if (d === todayCol) tdCls.push('today-col');
        if (d >= 6) tdCls.push('weekend-col');
        if (tdCls.length) td.className = tdCls.join(' ');
        var entry = startMap[d + '_' + section];
        if (entry) {
          var s = entry.session, c = entry.course;
          td.rowSpan = s.end_section - s.start_section + 1;
          var matched = matchesSearch(c);
          if (matched) visibleCount += 1;
          var block = h('div', {
            class: 'course-block ' + courseColorClass(c.course_name) + (matched ? '' : ' filtered-out'),
            role: 'button', tabindex: '0',
            title: c.course_name + ' · ' + (c.teacher_name || '') + ' · ' + (s.classroom || ''),
            onclick: (function (cid) { return function () { openDetail(cid); }; })(c.id),
            onkeydown: (function (cid) {
              return function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(cid); } };
            })(c.id)
          },
            h('div', { class: 'course-name' }, c.course_name),
            h('div', { class: 'course-period' }, s.start_section === s.end_section
              ? '第' + s.start_section + '节'
              : '第' + s.start_section + '-' + s.end_section + '节'),
            h('div', { class: 'course-room' }, s.classroom || ''),
            h('div', { class: 'course-teacher' }, c.teacher_name || '')
          );
          td.appendChild(block);
        }
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    $('week-no-match').hidden = !(state.search && visibleCount === 0);
  }

  /* ================= 渲染：日视图 ================= */
  function renderDayView() {
    var box = clear($('day-timeline-content'));
    var entries = sessionsThisWeek().filter(function (e) {
      return e.session.day_of_week === state.day;
    }).sort(function (a, b) { return a.session.start_section - b.session.start_section; });

    var visibleCount = 0;
    if (!entries.length) {
      box.appendChild(h('div', { class: 'empty-slot' }, '这一天没有课程'));
    } else {
      entries.forEach(function (e) {
        var s = e.session, c = e.course;
        var startSlot = slotOf(s.start_section);
        var endSlot = slotOf(s.end_section);
        var timeLabel = (startSlot && startSlot.start_time ? startSlot.start_time : '--')
          + ' - ' + (endSlot && endSlot.end_time ? endSlot.end_time : '--');
        var matched = matchesSearch(c);
        if (matched) visibleCount += 1;
        var card = h('div', {
          class: 'course-detail-card ' + courseColorClass(c.course_name) + (matched ? '' : ' filtered-out'),
          role: 'button', tabindex: '0',
          onclick: (function (cid) { return function () { openDetail(cid); }; })(c.id),
          onkeydown: (function (cid) {
            return function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openDetail(cid); } };
          })(c.id)
        },
          h('div', { class: 'course-detail-name' }, c.course_name),
          h('div', { class: 'course-detail-meta' },
            h('span', { class: 'course-detail-meta-item' }, icon('user', 'ico'), c.teacher_name || '—'),
            h('span', { class: 'course-detail-meta-item' }, icon('location', 'ico'), s.classroom || '—'),
            h('span', { class: 'course-detail-meta-item' }, icon('calendar', 'ico'), '第' + s.week_start + '-' + s.week_end + '周'),
            c.remark ? h('span', { class: 'course-detail-meta-item' }, icon('file', 'ico'), c.remark) : null
          )
        );
        box.appendChild(h('div', { class: 'day-timeline-row' },
          h('div', { class: 'day-timeline-time' },
            h('div', { class: 'time-label' }, icon('clock', 'ico'), timeLabel),
            h('div', { class: 'period-label' }, s.start_section === s.end_section
              ? '第' + s.start_section + '节'
              : '第' + s.start_section + '-' + s.end_section + '节')
          ),
          h('div', { class: 'day-timeline-content' }, card)
        ));
      });
    }
    $('day-no-match').hidden = !(state.search && visibleCount === 0);
  }

  /* ================= 渲染：视图可见性与空状态 ================= */
  function applyViewVisibility() {
    var noSemester = !state.semester;
    var noCourses = !courses().length;
    var showEmpty = noSemester || noCourses;
    $('view-empty').classList.toggle('hidden-view', !showEmpty);
    $('view-week').classList.toggle('hidden-view', showEmpty || state.viewMode !== 'week');
    $('view-day').classList.toggle('hidden-view', showEmpty || state.viewMode !== 'day');
    if (showEmpty) {
      if (noSemester) {
        $('empty-title').textContent = '当前没有进行中的学期';
        $('empty-desc').textContent = '请联系系统管理员创建并启用学期';
      } else if (isAdmin()) {
        $('empty-title').textContent = '本班暂无课程';
        $('empty-desc').textContent = '可通过「更多 → 导入课程与作业」批量导入，或点击右上角「添加课程」';
      } else {
        $('empty-title').textContent = '本班暂无课程';
        $('empty-desc').textContent = '本班暂无课程，请联系班级管理员导入';
      }
    }
  }

  /* ================= 课程详情弹窗 ================= */
  async function openDetail(courseId) {
    var local = findCourse(courseId);
    state.detailCourse = local;
    renderDetail(local);
    API.openModal($('modal-detail'));
    try {
      var data = await API.get('/api/courses/' + courseId);
      var remote = data && (data.course || data);
      if (remote && remote.id) {
        // 与本地数据合并，容错字段缺失
        var merged = Object.assign({}, local, remote);
        state.detailCourse = merged;
        renderDetail(merged);
      }
    } catch (err) { /* 详情接口不可用时保留本地渲染 */ }
  }

  function infoRow(label, value) {
    return h('div', { class: 'course-info-row' },
      h('span', { class: 'info-label' }, label),
      h('span', { class: 'info-value' }, value || '—'));
  }

  function renderDetail(course) {
    if (!course) return;
    $('detail-title').textContent = course.course_name || '课程详情';
    var info = clear($('detail-info'));
    info.appendChild(infoRow('课程编号', course.course_code));
    info.appendChild(infoRow('教师', course.teacher_name));
    info.appendChild(infoRow('考试日期', course.exam_date || '未安排'));
    info.appendChild(infoRow('备注', course.remark));

    var sessions = (course.sessions || []).slice().sort(function (a, b) {
      return a.day_of_week - b.day_of_week || a.start_section - b.start_section;
    });
    var sessEl = clear($('detail-sessions'));
    if (!sessions.length) sessEl.appendChild(h('div', null, '暂无上课安排'));
    sessions.forEach(function (s) {
      sessEl.appendChild(h('div', { class: 'session-item' },
        h('span', { class: 'session-day' }, DAY_NAMES[(s.day_of_week || 1) - 1]),
        h('span', null, '第' + s.start_section + (s.end_section > s.start_section ? '-' + s.end_section : '') + '节'),
        h('span', { class: 'session-meta' }, (s.classroom || '—') + ' · 第' + s.week_start + '-' + s.week_end + '周')
      ));
    });

    renderDetailHomework(course);

    $('btn-detail-edit').onclick = function () {
      API.closeModal($('modal-detail'));
      openCourseForm(state.detailCourse);
    };
    $('btn-detail-delete').onclick = function () { deleteCourse(state.detailCourse); };
    $('btn-hw-add').onclick = addHomework;
    $('input-hw-content').value = '';
    $('input-hw-deadline').value = '';
  }

  function detailHomeworkList(course) {
    if (Array.isArray(course.homework)) return course.homework;
    return allHomework().filter(function (hw) { return hw.course_id === course.id; });
  }

  function renderDetailHomework(course) {
    var listEl = clear($('detail-homework'));
    var list = detailHomeworkList(course).slice().sort(function (a, b) {
      return String(a.deadline).localeCompare(String(b.deadline));
    });
    if (!list.length) {
      listEl.appendChild(h('div', { class: 'detail-list' }, '暂无作业'));
      return;
    }
    list.forEach(function (hw) {
      listEl.appendChild(buildHomeworkRow(course, hw));
    });
  }

  function buildHomeworkRow(course, hw) {
    var st = hwStatus(hw.deadline);
    var row = h('div', { class: 'homework-item' },
      h('div', { class: 'homework-item-content' },
        h('div', { class: 'homework-item-name' }, hw.content),
        h('div', { class: 'homework-item-deadline' },
          '截止：' + hw.deadline + ' ',
          h('span', { class: 'status-' + st.cls }, st.text))));
    if (isAdmin()) {
      row.appendChild(h('div', { class: 'homework-actions' },
        h('button', {
          class: 'homework-op-btn', type: 'button',
          onclick: function () { editHomeworkRow(course, hw, row); }
        }, '编辑'),
        h('button', {
          class: 'homework-op-btn danger', type: 'button',
          onclick: function () { deleteHomework(hw); }
        }, '删除')
      ));
    }
    return row;
  }

  function editHomeworkRow(course, hw, rowEl) {
    var contentInput = h('input', { type: 'text', value: hw.content, maxlength: '200' });
    var dateInput = h('input', { type: 'date', value: hw.deadline });
    var editRow = h('div', { class: 'homework-item' },
      h('div', { class: 'homework-add-row homework-edit-row' },
        contentInput, dateInput,
        h('button', {
          class: 'btn btn-solid btn-sm', type: 'button',
          onclick: async function () {
            var content = contentInput.value.trim();
            var deadline = dateInput.value;
            if (!content || !deadline) { toast('请填写作业内容和截止日期', 'error'); return; }
            try {
              await API.put('/api/homework/' + hw.id, { content: content, deadline: deadline });
              toast('作业已更新', 'success');
              await refreshAfterChange(course.id);
            } catch (err) { API.showError(err); }
          }
        }, '保存'),
        h('button', {
          class: 'homework-op-btn', type: 'button',
          onclick: function () { renderDetailHomework(state.detailCourse || course); }
        }, '取消')
      ));
    if (rowEl && rowEl.parentNode) rowEl.parentNode.replaceChild(editRow, rowEl);
  }

  async function addHomework() {
    var course = state.detailCourse;
    if (!course) return;
    var content = $('input-hw-content').value.trim();
    var deadline = $('input-hw-deadline').value;
    if (!content || !deadline) { toast('请填写作业内容和截止日期', 'error'); return; }
    try {
      await API.post('/api/courses/' + course.id + '/homework', { content: content, deadline: deadline });
      toast('作业已添加', 'success');
      await refreshAfterChange(course.id);
    } catch (err) { API.showError(err); }
  }

  async function deleteHomework(hw) {
    if (!window.confirm('确认删除这条作业？')) return;
    try {
      await API.del('/api/homework/' + hw.id);
      toast('作业已删除', 'success');
      await refreshAfterChange(state.detailCourse && state.detailCourse.id);
    } catch (err) { API.showError(err); }
  }

  /* 作业/课程变更后：刷新课表数据与详情弹窗 */
  async function refreshAfterChange(courseId) {
    try {
      state.schedule = await API.get('/api/schedule?week=' + state.week);
    } catch (err) { /* 忽略，仍尝试刷新详情 */ }
    renderAll();
    if (courseId) {
      state.detailCourse = findCourse(courseId) || state.detailCourse;
      try {
        var data = await API.get('/api/courses/' + courseId);
        var remote = data && (data.course || data);
        if (remote && remote.id) state.detailCourse = Object.assign({}, state.detailCourse, remote);
      } catch (err2) { /* 保留本地 */ }
      if (state.detailCourse) renderDetail(state.detailCourse);
    }
  }

  async function deleteCourse(course) {
    if (!course) return;
    var sCount = (course.sessions || []).length;
    var hCount = detailHomeworkList(course).length;
    if (!window.confirm(
      '确认删除课程《' + course.course_name + '》？\n' +
      '将同时删除 ' + sCount + ' 条上课安排和 ' + hCount + ' 条作业，且不可恢复。')) return;
    try {
      await API.del('/api/courses/' + course.id);
      toast('课程已删除', 'success');
      API.closeModal($('modal-detail'));
      await loadSchedule();
    } catch (err) { API.showError(err); }
  }

  /* ================= 课程编辑弹窗（基本信息 + 多条上课安排） ================= */
  function openCourseForm(course) {
    state.editingCourseId = course ? course.id : null;
    $('course-form-title').textContent = course ? '编辑课程' : '添加课程';
    $('cf-code').value = course ? (course.course_code || '') : '';
    $('cf-name').value = course ? (course.course_name || '') : '';
    $('cf-teacher').value = course ? (course.teacher_name || '') : '';
    $('cf-exam').value = course && course.exam_date ? course.exam_date : '';
    $('cf-remark').value = course ? (course.remark || '') : '';
    hideError($('course-form-error'));
    var rows = clear($('session-rows'));
    var sessions = course && (course.sessions || []).length ? course.sessions : [null];
    sessions.forEach(function (s) { rows.appendChild(buildSessionRow(s)); });
    fillTeacherDatalist();
    API.openModal($('modal-course-form'));
  }

  function buildSessionRow(s) {
    var daySelect = h('select', { class: 'input day-select', 'aria-label': '星期' });
    DAY_SHORT.forEach(function (name, i) {
      daySelect.appendChild(h('option', { value: String(i + 1) }, name));
    });
    daySelect.value = String(s ? s.day_of_week : 1);
    var wc = (state.semester && state.semester.week_count) || 30;
    var row;
    var delBtn = h('button', {
      class: 'row-del', type: 'button', title: '删除该安排', 'aria-label': '删除该安排',
      onclick: function () { row.remove(); }
    }, icon('x', 'ico ico-16'));
    row = h('div', { class: 'session-row' },
      h('div', { class: 'sr-fields' },
        h('input', { class: 'input sr-classroom', type: 'text', placeholder: '教室', 'aria-label': '教室', value: s ? s.classroom : '', maxlength: '64' }),
        daySelect,
        h('span', { class: 'sr-group' },
          h('span', { class: 'sr-group-label' }, '节次'),
          h('input', { class: 'input num-input sr-start', type: 'number', min: '1', max: '12', 'aria-label': '开始节次', value: s ? s.start_section : 1 }),
          h('span', { class: 'sr-group-label' }, '至'),
          h('input', { class: 'input num-input sr-end', type: 'number', min: '1', max: '12', 'aria-label': '结束节次', value: s ? s.end_section : 2 })
        ),
        h('span', { class: 'sr-group' },
          h('span', { class: 'sr-group-label' }, '周次'),
          h('input', { class: 'input num-input sr-wstart', type: 'number', min: '1', max: String(wc), 'aria-label': '起始周', value: s ? s.week_start : 1 }),
          h('span', { class: 'sr-group-label' }, '至'),
          h('input', { class: 'input num-input sr-wend', type: 'number', min: '1', max: String(wc), 'aria-label': '结束周', value: s ? s.week_end : Math.min(wc, 16) })
        )
      ),
      delBtn
    );
    return row;
  }

  async function fillTeacherDatalist() {
    var names = {};
    courses().forEach(function (c) { if (c.teacher_name) names[c.teacher_name] = true; });
    if (!state.teachersLoaded) {
      state.teachersLoaded = true;
      try {
        var data = await API.get('/api/teachers');
        API.asList(data).forEach(function (t) {
          var name = typeof t === 'string' ? t : t.name;
          if (name) names[name] = true;
        });
      } catch (err) { /* 联想失败不阻塞 */ }
    }
    var dl = clear($('dlist-teachers'));
    Object.keys(names).sort().forEach(function (name) {
      dl.appendChild(h('option', { value: name }));
    });
  }

  function numOrNaN(v) { return v === '' ? NaN : Number(v); }

  async function saveCourseForm() {
    var errs = [];
    var code = $('cf-code').value.trim();
    var name = $('cf-name').value.trim();
    var teacher = $('cf-teacher').value.trim();
    var exam = $('cf-exam').value;
    var remark = $('cf-remark').value.trim();
    if (!name) errs.push('课程名不能为空');
    if (!teacher) errs.push('请完整填写课程信息');

    var wc = (state.semester && state.semester.week_count) || 30;
    var sessions = [];
    var rowEls = $('session-rows').querySelectorAll('.session-row');
    if (!rowEls.length) errs.push('请至少添加一条上课安排');
    rowEls.forEach(function (row, idx) {
      var label = '第 ' + (idx + 1) + ' 条安排：';
      var classroom = row.querySelector('.sr-classroom').value.trim();
      var day = Number(row.querySelector('.day-select').value);
      var ss = numOrNaN(row.querySelector('.sr-start').value);
      var se = numOrNaN(row.querySelector('.sr-end').value);
      var ws = numOrNaN(row.querySelector('.sr-wstart').value);
      var we = numOrNaN(row.querySelector('.sr-wend').value);
      if (!classroom) { errs.push(label + '请完整填写课程信息'); return; }
      if ([ss, se, ws, we].some(function (n) { return !Number.isInteger(n); })) {
        errs.push(label + '请输入有效数字'); return;
      }
      if (ss < 1 || se > 12) { errs.push(label + '节次范围为 1-12'); return; }
      if (ss > se) { errs.push(label + '开始节次不能大于结束节次'); return; }
      if (ws < 1 || we > wc) { errs.push(label + '周次范围为 1-' + wc); return; }
      if (ws > we) { errs.push(label + '起始周不能大于结束周'); return; }
      if (!(day >= 1 && day <= 7)) { errs.push(label + '星期无效'); return; }
      sessions.push({
        classroom: classroom, day_of_week: day,
        start_section: ss, end_section: se, week_start: ws, week_end: we
      });
    });

    var errEl = $('course-form-error');
    if (errs.length) { showError(errEl, errs.join('\n')); return; }
    hideError(errEl);

    var body = {
      course_code: code, course_name: name, teacher_name: teacher,
      exam_date: exam || null, remark: remark, sessions: sessions
    };
    var btn = $('btn-course-save');
    btn.disabled = true;
    try {
      if (state.editingCourseId) {
        await API.put('/api/courses/' + state.editingCourseId, body);
        toast('课程已保存', 'success');
      } else {
        await API.post('/api/courses', body);
        toast('课程已添加', 'success');
      }
      API.closeModal($('modal-course-form'));
      await loadSchedule();
    } catch (err) {
      // 409 冲突 / 400 校验：提示并保留表单供修改
      if (err.status === 409 || err.status === 400) {
        showError(errEl, err.message);
        toast(err.message, 'error');
      } else {
        API.showError(err);
      }
    } finally {
      btn.disabled = false;
    }
  }

  /* ================= 双 CSV 导入 ================= */
  function openImportModal() {
    $('file-course').value = '';
    $('file-homework').value = '';
    $('file-course-name').textContent = '';
    $('file-homework-name').textContent = '';
    hideError($('import-error'));
    clear($('import-errors'));
    $('import-result').classList.add('hidden');
    API.openModal($('modal-import'));
  }

  async function doImport() {
    var courseFile = $('file-course').files[0];
    var hwFile = $('file-homework').files[0];
    var errEl = $('import-error');
    hideError(errEl);
    clear($('import-errors'));
    $('import-result').classList.add('hidden');
    if (!courseFile || !hwFile) { showError(errEl, '请同时选择课程和作业两份 CSV 文件'); return; }
    if (courseFile.size > MAX_FILE_SIZE || hwFile.size > MAX_FILE_SIZE) {
      showError(errEl, '每个文件不能超过 2MB'); return;
    }
    if (!window.confirm('导入将覆盖本班当前学期的全部课程、上课安排和作业，确认继续？')) return;
    var fd = new FormData();
    fd.append('course_file', courseFile);
    fd.append('homework_file', hwFile);
    var btn = $('btn-do-import');
    btn.disabled = true;
    btn.textContent = '导入中…';
    try {
      var data = await API.upload('/api/import/course-homework', fd);
      var msg = '已导入 ' + API.pickNum(data, ['courses', 'courses_count', 'course_count']) + ' 门课程、'
        + API.pickNum(data, ['sessions', 'sessions_count', 'session_count']) + ' 条上课安排和 '
        + API.pickNum(data, ['homework', 'homework_count']) + ' 条作业';
      var resultEl = $('import-result');
      resultEl.textContent = msg;
      resultEl.classList.remove('hidden');
      toast('导入成功', 'success');
      await loadSchedule();
    } catch (err) {
      var lineErrs = err.details && (Array.isArray(err.details) ? err.details : err.details.errors);
      if (Array.isArray(lineErrs) && lineErrs.length) {
        var box = $('import-errors');
        lineErrs.forEach(function (le) {
          var file = le.file ? '[' + le.file + '] ' : '';
          var line = (le.line !== undefined && le.line !== null) ? '第 ' + le.line + ' 行：' : '';
          box.appendChild(h('div', { class: 'import-error-item' }, file + line + (le.message || le.error || '数据无效')));
        });
      }
      showError(errEl, err.message || '导入失败，原课程与作业保持不变');
    } finally {
      btn.disabled = false;
      btn.textContent = '开始导入';
    }
  }

  /* ================= CSV 模板下载 ================= */
  function downloadCsv(filename, text) {
    var blob = new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = h('a', { href: url, download: filename });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
  function downloadCourseTemplate() {
    downloadCsv('课程模板.csv',
      '课程编号,课程名,教师,教室,星期,开始节次,结束节次,起始周,结束周,考试日期,备注\n' +
      'MATH-01,高等数学,张老师,教学楼301,1,1,2,1,16,2026-12-20,带计算器\n' +
      'MATH-01,高等数学,张老师,教学楼205,3,3,4,1,16,2026-12-20,带计算器\n');
  }
  function downloadHomeworkTemplate() {
    downloadCsv('作业模板.csv',
      '课程编号,作业内容,截止日期\n' +
      'MATH-01,习题集第5章,2026-09-10\n');
  }

  /* ================= 班级设置 / 邀请码 ================= */
  function openClassSettings() {
    $('cs-class-code').textContent = (state.user && state.user.class_code) || '—';
    $('cs-class-name').textContent = (state.user && state.user.class_name) || '—';
    hideError($('cs-error'));
    API.openModal($('modal-class-settings'));
  }

  async function regenerateInvite() {
    if (!window.confirm('重新生成后旧邀请码立即失效，确认重新生成？')) return;
    var btn = $('btn-regen-invite');
    btn.disabled = true;
    try {
      var data = await API.post('/api/classes/me/invite-code/regenerate');
      var code = API.pickStr(data, ['invite_code', 'code']);
      if (!code) { showError($('cs-error'), '服务端未返回邀请码'); return; }
      API.closeModal($('modal-class-settings'));
      $('invite-code-text').textContent = code;
      API.openModal($('modal-invite'));
    } catch (err) {
      if (!err.notified) showError($('cs-error'), err.message || '生成失败，请稍后重试');
    } finally {
      btn.disabled = false;
    }
  }

  async function copyInvite() {
    var code = $('invite-code-text').textContent;
    try {
      await navigator.clipboard.writeText(code);
      toast('已复制', 'success');
    } catch (err) {
      // 剪贴板不可用时选中文字便于手动复制
      var range = document.createRange();
      range.selectNodeContents($('invite-code-text'));
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      toast('请手动复制选中的邀请码');
    }
  }

  /* ================= PDF 导出（打印当前周完整周视图） ================= */
  function preparePrint() {
    if (state.printPrepared) return;
    state.printPrepared = true;
    state.printBackup = { search: state.search };
    state.search = ''; // 打印前忽略搜索过滤，输出完整周视图
    renderWeekView();
    var cls = (state.user && (state.user.class_name || state.user.class_code)) || '班级课表';
    $('print-title').textContent = cls + ' · 第 ' + state.week + ' 周课表';
    document.body.classList.add('print-mode');
  }
  function restorePrint() {
    if (!state.printPrepared) return;
    state.printPrepared = false;
    document.body.classList.remove('print-mode');
    if (state.printBackup) {
      state.search = state.printBackup.search;
      $('input-search').value = state.search;
    }
    renderAll();
  }

  /* ================= More 菜单 ================= */
  function closeMoreMenu() {
    var menu = $('more-menu');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    $('btn-more').setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', onDocClick, true);
    document.removeEventListener('keydown', onMenuKey);
  }
  function onDocClick(e) {
    var anchor = $('more-menu-anchor');
    if (anchor && !anchor.contains(e.target)) closeMoreMenu();
  }
  function onMenuKey(e) {
    if (e.key === 'Escape') { closeMoreMenu(); $('btn-more').focus(); }
  }
  function toggleMoreMenu() {
    var menu = $('more-menu');
    if (!menu) return;
    if (menu.hidden) {
      menu.hidden = false;
      $('btn-more').setAttribute('aria-expanded', 'true');
      document.addEventListener('click', onDocClick, true);
      document.addEventListener('keydown', onMenuKey);
    } else {
      closeMoreMenu();
    }
  }

  /* ================= 静态图标注入 ================= */
  function injectIcons() {
    var map = [
      ['toggle-pwd', 'eye'],
      ['btn-back-login', null], // 特殊处理：图标 + 文本
      ['btn-week-prev', 'chevronLeft'],
      ['btn-week-next', 'chevronRight']
    ];
    map.forEach(function (pair) {
      var el = $(pair[0]);
      if (!el) return;
      if (pair[0] === 'btn-back-login') {
        el.appendChild(icon('arrowLeft', 'ico ico-16'));
        el.appendChild(document.createTextNode('返回登录'));
        return;
      }
      el.appendChild(icon(pair[1], 'ico ico-16'));
    });
    var searchIcon = document.querySelector('.header-search .search-icon');
    if (searchIcon) searchIcon.appendChild(icon('search', 'ico ico-16'));
    var emptyIcon = document.querySelector('.empty-state-icon');
    if (emptyIcon) emptyIcon.appendChild(icon('calendar', 'ico'));
    // More 菜单项图标
    var menuIcons = { 'btn-import': 'upload', 'btn-tpl-course': 'download', 'btn-tpl-homework': 'download', 'btn-class-settings': 'settings' };
    Object.keys(menuIcons).forEach(function (id) {
      var el = $(id);
      if (el) el.insertBefore(icon(menuIcons[id], 'ico'), el.firstChild);
    });
    // 弹窗关闭按钮
    document.querySelectorAll('.modal-close').forEach(function (el) {
      el.appendChild(icon('x', 'ico ico-16'));
    });
    // 添加课程按钮前缀
    var addBtn = $('btn-add-course');
    if (addBtn) addBtn.insertBefore(icon('plus', 'ico ico-16'), addBtn.firstChild);
    var addRowBtn = $('btn-add-session');
    if (addRowBtn) addRowBtn.insertBefore(icon('plus', 'ico ico-16'), addRowBtn.firstChild);
  }

  /* ================= 事件绑定 ================= */
  function bindEvents() {
    // 登录 / 激活
    $('tab-student').addEventListener('click', function () { switchLoginTab('student'); });
    $('tab-admin').addEventListener('click', function () { switchLoginTab('admin'); });
    $('login-btn').addEventListener('click', doLogin);
    $('login-pwd').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    $('login-id').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    $('toggle-pwd').addEventListener('click', function () {
      var input = $('login-pwd');
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      clear($('toggle-pwd')).appendChild(icon(show ? 'eyeOff' : 'eye', 'ico ico-16'));
      $('toggle-pwd').setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
    });
    $('btn-show-activate').addEventListener('click', function () { showPanel('activate'); });
    $('btn-back-login').addEventListener('click', function () { showPanel('login'); });
    $('activate-btn').addEventListener('click', doActivate);
    $('activate-pwd2').addEventListener('keydown', function (e) { if (e.key === 'Enter') doActivate(); });
    $('btn-logout').addEventListener('click', doLogout);

    // 工具栏
    $('btn-week-prev').addEventListener('click', function () {
      if (state.week <= 1) return;
      state.week -= 1;
      loadSchedule();
    });
    $('btn-week-next').addEventListener('click', function () {
      var wc = (state.semester && state.semester.week_count) || 30;
      if (state.week >= wc) return;
      state.week += 1;
      loadSchedule();
    });
    $('tab-week-view').addEventListener('click', function () {
      state.viewMode = 'week';
      state.viewModeTouched = true;
      renderAll();
    });
    $('tab-day-view').addEventListener('click', function () {
      state.viewMode = 'day';
      state.viewModeTouched = true;
      renderAll();
    });
    document.querySelectorAll('#day-selector .day-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.day = Number(btn.getAttribute('data-day'));
        renderToolbar();
        renderDayView();
      });
    });
    $('input-search').addEventListener('input', function () {
      state.search = $('input-search').value.trim();
      renderWeekView();
      renderDayView();
    });

    // 管理员功能
    $('btn-add-course').addEventListener('click', function () { openCourseForm(null); });
    $('btn-more').addEventListener('click', toggleMoreMenu);
    $('btn-tpl-course').addEventListener('click', function () { closeMoreMenu(); downloadCourseTemplate(); });
    $('btn-tpl-homework').addEventListener('click', function () { closeMoreMenu(); downloadHomeworkTemplate(); });
    $('btn-import').addEventListener('click', function () { closeMoreMenu(); openImportModal(); });
    $('btn-class-settings').addEventListener('click', function () { closeMoreMenu(); openClassSettings(); });
    $('btn-do-import').addEventListener('click', doImport);
    $('file-course').addEventListener('change', function () {
      $('file-course-name').textContent = this.files[0] ? this.files[0].name : '';
    });
    $('file-homework').addEventListener('change', function () {
      $('file-homework-name').textContent = this.files[0] ? this.files[0].name : '';
    });
    $('btn-regen-invite').addEventListener('click', regenerateInvite);
    $('btn-copy-invite').addEventListener('click', copyInvite);
    $('btn-add-session').addEventListener('click', function () {
      $('session-rows').appendChild(buildSessionRow(null));
    });
    $('btn-course-save').addEventListener('click', saveCourseForm);

    // PDF 导出
    $('btn-export-pdf').addEventListener('click', function () {
      preparePrint();
      window.print();
    });
    window.addEventListener('beforeprint', preparePrint);
    window.addEventListener('afterprint', restorePrint);

    // 视口变化：未手动切换过视图时，移动端默认日视图、桌面默认周视图
    MOBILE_MQ.addEventListener('change', function (e) {
      if (state.viewModeTouched) return;
      state.viewMode = e.matches ? 'day' : 'week';
      if (state.user) renderAll();
    });

    // 弹窗遮罩/关闭
    ['modal-detail', 'modal-course-form', 'modal-import', 'modal-class-settings', 'modal-invite']
      .forEach(API.bindModal);
  }

  /* ================= 启动 ================= */
  async function boot() {
    injectIcons();
    bindEvents();
    var user = null;
    try { user = await API.init(); } catch (err) { user = null; }
    if (user) afterLogin(user);
    else showLoginView();
  }
  boot();
})();
