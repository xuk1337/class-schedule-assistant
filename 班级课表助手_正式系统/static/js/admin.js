/* ============================================================
 * 班级课表助手 · 系统管理后台（仅 system_admin）
 * 学期 / 班级 / 学生名单 / 班级管理员 / 审计日志
 * 数据全部来自 Flask API；不使用 localStorage 保存业务数据。
 * ============================================================ */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var h = API.h, clear = API.clear, toast = API.toast, icon = window.ICON;
  var PAGE_SIZE = 20;
  var MAX_FILE_SIZE = 2 * 1024 * 1024;

  var state = {
    user: null,
    section: 'semesters',
    semesters: [],
    classes: [],
    studentClass: '',      // 学生名单筛选班级
    usersPage: 1,
    auditPage: 1,
    auditFilters: {},
    transferUserId: null
  };

  var STATUS_BADGES = {
    active: ['badge-green', '已启用'],
    pending: ['badge-yellow', '待激活'],
    disabled: ['badge-red', '已停用'],
    inactive: ['badge-gray', '未启用']
  };
  var ROLE_NAMES = { student: '学生', admin: '班级管理员', system_admin: '系统管理员' };
  var SECTION_TITLES = {
    semesters: '学期管理',
    classes: '班级管理',
    students: '学生名单',
    admins: '管理员管理',
    audit: '审计日志'
  };
  /* 名单导入结果（跨 renderStudents 重绘保留） */
  var rosterFeedback = null; // {imported:Number|null, errors:Array, message:String|null}

  function renderRosterFeedback(resultBox) {
    clear(resultBox);
    if (!rosterFeedback) return;
    if (rosterFeedback.imported !== null && rosterFeedback.imported !== undefined) {
      resultBox.appendChild(h('div', { class: 'import-result' }, '成功导入 ' + rosterFeedback.imported + ' 名学生'));
    }
    if (rosterFeedback.errors && rosterFeedback.errors.length) appendLineErrors(resultBox, rosterFeedback.errors);
    if (rosterFeedback.message) {
      resultBox.appendChild(h('div', { class: 'form-error show roster-message' }, rosterFeedback.message));
    }
  }

  function statusBadge(s) {
    var conf = STATUS_BADGES[s] || ['badge-gray', s || '—'];
    return h('span', { class: 'badge ' + conf[0] }, conf[1]);
  }
  function classNameOf(id) {
    for (var i = 0; i < state.classes.length; i += 1) {
      if (state.classes[i].id === id) return state.classes[i].class_name;
    }
    return id ? ('#' + id) : '—';
  }
  /* 班级列表项的有效管理员姓名（后端为嵌套 admin 对象，容错扁平字段） */
  function adminNameOf(row) {
    return (row && row.admin && row.admin.name) || (row && row.admin_name) || '';
  }
  function semesterNameOf(id) {
    for (var i = 0; i < state.semesters.length; i += 1) {
      if (state.semesters[i].id === id) return state.semesters[i].name;
    }
    return id ? ('#' + id) : '—';
  }

  /* ================= 通用表格 / 分页 / 区块模板 ================= */
  function buildTable(columns, rows, emptyText) {
    var thead = h('thead', null, h('tr', null, columns.map(function (c) { return h('th', null, c.label); })));
    var tbody = h('tbody');
    if (!rows.length) {
      tbody.appendChild(h('tr', null,
        h('td', { colspan: String(columns.length) },
          h('div', { class: 'admin-empty' },
            h('div', { class: 'empty-ico' }, icon('inbox', 'ico')),
            emptyText || '暂无数据'))));
    } else {
      rows.forEach(function (row) {
        tbody.appendChild(h('tr', null, columns.map(function (c) {
          var attrs = { 'data-label': c.label };
          if (c.wrap) attrs.class = 'cell-wrap';
          var td = h('td', attrs);
          var v = c.render(row);
          if (v === null || v === undefined) td.textContent = '—';
          else if (typeof v === 'string' || typeof v === 'number') td.textContent = String(v);
          else td.appendChild(v);
          return td;
        })));
      });
    }
    return h('div', { class: 'data-table-wrap' }, h('table', { class: 'data-table' }, thead, tbody));
  }

  function buildPagination(pageInfo, onFlip) {
    var totalPages = Math.max(1, Math.ceil(pageInfo.total / pageInfo.page_size));
    return h('div', { class: 'pagination' },
      h('button', {
        type: 'button', disabled: pageInfo.page <= 1,
        onclick: function () { onFlip(pageInfo.page - 1); }
      }, '‹ 上一页'),
      h('span', null, '第 ' + pageInfo.page + ' / ' + totalPages + ' 页 · 共 ' + pageInfo.total + ' 条'),
      h('button', {
        type: 'button', disabled: pageInfo.page >= totalPages,
        onclick: function () { onFlip(pageInfo.page + 1); }
      }, '下一页 ›'));
  }

  /* 稳定页面模板：标题 + 说明 + 主操作 */
  function section(title, desc, actions) {
    return h('section', { class: 'admin-section' },
      h('div', { class: 'admin-section-header' },
        h('div', null,
          h('div', { class: 'admin-section-title' }, title),
          desc ? h('div', { class: 'admin-section-desc' }, desc) : null),
        actions && actions.length ? h('div', { class: 'admin-section-actions' }, actions) : null));
  }

  function classSelect(idAttr, withAll) {
    var sel = h('select', { class: 'input', id: idAttr });
    if (withAll) sel.appendChild(h('option', { value: '' }, '全部班级'));
    state.classes.forEach(function (c) {
      sel.appendChild(h('option', { value: String(c.id) }, c.class_name + '（' + c.class_code + '）'));
    });
    return sel;
  }

  function showError(el, msg) { el.textContent = msg; el.classList.add('show'); }
  function hideError(el) { el.classList.remove('show'); }

  /* ================= 行级菜单（低频操作收纳） ================= */
  var openMenu = null;
  function closeRowMenu() {
    if (openMenu) {
      openMenu.remove();
      openMenu = null;
      document.removeEventListener('click', onMenuDocClick, true);
      document.removeEventListener('keydown', onMenuKey);
    }
  }
  function onMenuDocClick(e) {
    if (openMenu && !openMenu.contains(e.target)) closeRowMenu();
  }
  function onMenuKey(e) {
    if (e.key === 'Escape') closeRowMenu();
  }
  /* items: [{label, icon, danger, onClick}] */
  function rowMenuButton(items) {
    var anchor = h('span', { class: 'menu-anchor' });
    var btn = h('button', {
      class: 'row-menu-btn', type: 'button', 'aria-label': '更多操作', 'aria-haspopup': 'menu',
      onclick: function (e) {
        e.stopPropagation();
        if (openMenu) { closeRowMenu(); return; }
        var menu = h('div', { class: 'menu-pop', role: 'menu' });
        items.forEach(function (it) {
          menu.appendChild(h('button', {
            class: 'menu-item' + (it.danger ? ' danger' : ''), type: 'button', role: 'menuitem',
            onclick: function (ev) { ev.stopPropagation(); closeRowMenu(); it.onClick(); }
          }, it.icon ? icon(it.icon, 'ico') : null, it.label));
        });
        anchor.appendChild(menu);
        openMenu = menu;
        document.addEventListener('click', onMenuDocClick, true);
        document.addEventListener('keydown', onMenuKey);
      }
    }, icon('more', 'ico ico-16'));
    anchor.appendChild(btn);
    return anchor;
  }

  /* ================= 敏感信息一次性展示 ================= */
  function openSecret(title, hint, text) {
    $('secret-title').textContent = title;
    $('secret-hint').textContent = hint;
    $('secret-text').textContent = text;
    API.openModal($('modal-secret'));
  }

  /* ================= 基础数据 ================= */
  async function loadMeta() {
    try {
      var sems = await API.get('/api/system/semesters?page_size=100');
      state.semesters = API.asList(sems);
    } catch (err) { API.showError(err); }
    try {
      var clss = await API.get('/api/system/classes?page_size=200');
      state.classes = API.asList(clss);
    } catch (err) { API.showError(err); }
  }

  /* ================= 板块一：学期管理 ================= */
  async function renderSemesters() {
    var box = clear($('admin-content'));
    var c = section('学期管理', '创建学期并启用唯一进行中的学期', [
      h('button', {
        class: 'btn btn-solid btn-sm', type: 'button',
        onclick: function () {
          $('sem-name').value = ''; $('sem-start').value = ''; $('sem-weeks').value = '';
          hideError($('sem-error'));
          API.openModal($('modal-semester'));
        }
      }, icon('plus', 'ico ico-16'), '新建学期')
    ]);
    box.appendChild(c);
    var list;
    try {
      var data = await API.withLoading(API.get('/api/system/semesters?page_size=100'));
      list = API.asList(data);
      state.semesters = list;
    } catch (err) { API.showError(err); return; }
    c.appendChild(buildTable([
      { label: '学期名称', render: function (r) { return h('span', { class: 'cell-main' }, r.name); } },
      { label: '第一周周一', render: function (r) { return r.start_date; } },
      { label: '周数', render: function (r) { return r.week_count; } },
      { label: '状态', render: function (r) { return statusBadge(r.status); } },
      {
        label: '操作', render: function (r) {
          if (r.status === 'active') return h('span', { class: 'cell-sub' }, '当前学期');
          return h('button', {
            class: 'btn btn-outline btn-sm', type: 'button',
            onclick: function () { enableSemester(r); }
          }, '启用');
        }
      }
    ], list, '暂无学期，请先新建'));
  }

  async function enableSemester(row) {
    if (!window.confirm('启用「' + row.name + '」后，它将成为唯一进行中的学期，确认启用？')) return;
    try {
      await API.patch('/api/system/semesters/' + row.id, { status: 'active' });
      toast('学期已启用', 'success');
      await loadMeta();
      renderSemesters();
    } catch (err) { API.showError(err); }
  }

  async function saveSemester() {
    var name = $('sem-name').value.trim();
    var start = $('sem-start').value;
    var weeks = Number($('sem-weeks').value);
    var errEl = $('sem-error');
    hideError(errEl);
    if (!name || !start || !$('sem-weeks').value) { showError(errEl, '请完整填写学期信息'); return; }
    var d = start.split('-');
    if (new Date(+d[0], +d[1] - 1, +d[2]).getDay() !== 1) { showError(errEl, '第一周周一必须是星期一'); return; }
    if (!Number.isInteger(weeks) || weeks < 1 || weeks > 30) { showError(errEl, '周数应为 1-30 的整数'); return; }
    try {
      await API.post('/api/system/semesters', { name: name, start_date: start, week_count: weeks });
      toast('学期已创建', 'success');
      API.closeModal($('modal-semester'));
      await loadMeta();
      renderSemesters();
    } catch (err) {
      if (!err.notified) showError(errEl, err.message || '创建失败');
    }
  }

  /* ================= 板块二：班级管理 ================= */
  async function renderClasses() {
    var box = clear($('admin-content'));
    var c = section('班级管理', '班级代码、所属学期与有效管理员', [
      h('button', {
        class: 'btn btn-solid btn-sm', type: 'button',
        onclick: function () {
          $('cls-code').value = ''; $('cls-name').value = '';
          hideError($('cls-error'));
          var sel = clear($('cls-semester'));
          state.semesters.forEach(function (s) {
            sel.appendChild(h('option', { value: String(s.id) }, s.name + (s.status === 'active' ? '（当前）' : '')));
          });
          API.openModal($('modal-class'));
        }
      }, icon('plus', 'ico ico-16'), '新建班级')
    ]);
    box.appendChild(c);
    var list;
    try {
      var data = await API.withLoading(API.get('/api/system/classes?page_size=200'));
      list = API.asList(data);
      state.classes = list;
    } catch (err) { API.showError(err); return; }
    c.appendChild(buildTable([
      { label: '班级代码', render: function (r) { return h('span', { class: 'cell-main' }, r.class_code); } },
      { label: '班级名称', render: function (r) { return r.class_name; } },
      { label: '所属学期', render: function (r) { return r.semester_name || semesterNameOf(r.semester_id); } },
      { label: '状态', render: function (r) { return statusBadge(r.status); } },
      { label: '有效管理员', render: function (r) { return adminNameOf(r) || '—'; } },
      {
        label: '操作', render: function (r) {
          var disabled = r.status === 'disabled';
          return h('button', {
            class: 'btn ' + (disabled ? 'btn-outline' : 'btn-danger') + ' btn-sm', type: 'button',
            onclick: function () { toggleClass(r, disabled); }
          }, disabled ? '启用' : '停用');
        }
      }
    ], list, '暂无班级，请先新建'));
  }

  async function toggleClass(row, enable) {
    var action = enable ? '启用' : '停用';
    if (!window.confirm('确认' + action + '班级「' + row.class_name + '」？' +
      (enable ? '' : '停用后该班禁止新激活和业务写入。'))) return;
    try {
      await API.patch('/api/system/classes/' + row.id, { status: enable ? 'active' : 'disabled' });
      toast('班级已' + action, 'success');
      await loadMeta();
      renderClasses();
    } catch (err) { API.showError(err); }
  }

  async function saveClass() {
    var code = $('cls-code').value.trim();
    var name = $('cls-name').value.trim();
    var semId = Number($('cls-semester').value);
    var errEl = $('cls-error');
    hideError(errEl);
    if (!code || !name || !semId) { showError(errEl, '请完整填写班级信息'); return; }
    try {
      var data = await API.post('/api/system/classes', {
        class_code: code, class_name: name, semester_id: semId
      });
      API.closeModal($('modal-class'));
      await loadMeta();
      renderClasses();
      var invite = API.pickStr(data, ['invite_code']) ||
        (data && data.class ? API.pickStr(data.class, ['invite_code']) : '');
      if (invite) {
        openSecret('班级创建成功', '班级初始邀请码如下，请立即复制并线下发放给本班管理员：', invite);
      } else {
        toast('班级已创建', 'success');
      }
    } catch (err) {
      if (!err.notified) showError(errEl, err.message || '创建失败');
    }
  }

  /* ================= 板块三：学生名单 ================= */
  async function renderStudents(page) {
    state.usersPage = page || state.usersPage || 1;
    var box = clear($('admin-content'));

    // 名单导入区块
    var importSection = section('学生名单导入', 'CSV 文件或粘贴文本，每行一条：姓名,学号');
    var classSel = classSelect('import-class', false);
    var fileInput = h('input', { type: 'file', accept: '.csv,text/csv', class: 'input roster-file' });
    var textArea = h('textarea', {
      class: 'input import-textarea', rows: '4',
      placeholder: '或直接粘贴名单文本，每行一条：姓名,学号（首行可为表头）'
    });
    var resultBox = h('div', { class: 'import-result-box' });
    renderRosterFeedback(resultBox);
    importSection.appendChild(h('div', { class: 'filter-bar' },
      h('span', { class: 'filter-label' }, '目标班级'), classSel, fileInput,
      h('button', {
        class: 'btn btn-solid btn-sm', type: 'button',
        onclick: function () { doRosterImport(classSel.value, fileInput, textArea); }
      }, '开始导入')));
    importSection.appendChild(textArea);
    importSection.appendChild(resultBox);
    box.appendChild(importSection);

    // 学生列表区块
    var filterSel = classSelect('student-filter', true);
    filterSel.value = state.studentClass || '';
    filterSel.addEventListener('change', function () {
      state.studentClass = filterSel.value;
      state.usersPage = 1;
      renderStudents(1);
    });
    var listSection = section('学生列表', '转班、启停与重置密码');
    listSection.appendChild(h('div', { class: 'filter-bar' },
      h('span', { class: 'filter-label' }, '按班级筛选'), filterSel));
    box.appendChild(listSection);

    var query = '/api/system/users?role=student&page=' + state.usersPage + '&page_size=' + PAGE_SIZE;
    if (state.studentClass) query += '&class_id=' + encodeURIComponent(state.studentClass);
    var data;
    try { data = await API.withLoading(API.get(query)); } catch (err) { API.showError(err); return; }
    var list = API.asList(data);
    listSection.appendChild(buildTable([
      { label: '姓名', render: function (r) { return h('span', { class: 'cell-main' }, r.name); } },
      { label: '学号', render: function (r) { return r.student_no; } },
      { label: '班级', render: function (r) { return r.class_name || classNameOf(r.class_id); } },
      { label: '状态', render: function (r) { return statusBadge(r.status); } },
      {
        label: '操作', render: function (r) {
          var disabled = r.status === 'disabled';
          return h('div', { class: 'row-actions' },
            h('button', {
              class: 'btn ' + (disabled ? 'btn-outline' : 'btn-danger') + ' btn-sm', type: 'button',
              onclick: function () { toggleUser(r, disabled, function () { renderStudents(); }); }
            }, disabled ? '启用' : '停用'),
            rowMenuButton([
              { label: '转班', icon: 'transfer', onClick: function () { openTransfer(r); } },
              { label: '重置密码', icon: 'key', onClick: function () { resetPassword(r); } }
            ]));
        }
      }
    ], list, '暂无学生记录'));
    listSection.appendChild(buildPagination(API.pageOf(data), function (p) { renderStudents(p); }));
  }

  async function doRosterImport(classId, fileInput, textArea) {
    rosterFeedback = null;
    renderRosterFeedbackPreview();
    if (!classId) { toast('请选择要导入的班级', 'error'); return; }
    var file = fileInput.files[0];
    var text = textArea.value.trim();
    if (!file && text) {
      file = new File([text], 'pasted-roster.csv', { type: 'text/csv' });
    }
    if (!file) { toast('请选择 CSV 文件或粘贴名单文本', 'error'); return; }
    if (file.size > MAX_FILE_SIZE) { toast('名单文件不能超过 2MB', 'error'); return; }
    var fd = new FormData();
    fd.append('file', file);
    try {
      var data = await API.withLoading(API.upload('/api/system/classes/' + classId + '/students/import', fd));
      var warnErrs = data && (data.errors || (data.details && data.details.errors));
      rosterFeedback = {
        imported: API.pickNum(data, ['imported', 'success_count', 'count', 'created']),
        errors: Array.isArray(warnErrs) ? warnErrs : [],
        message: null
      };
      toast('名单导入完成', 'success');
      fileInput.value = ''; textArea.value = '';
      renderStudents(1);
    } catch (err) {
      var lineErrs = err.details && (Array.isArray(err.details) ? err.details : err.details.errors);
      rosterFeedback = {
        imported: null,
        errors: Array.isArray(lineErrs) ? lineErrs : [],
        message: (err.message || '导入失败') + '（名单整批未写入）'
      };
      renderStudents();
    }
  }

  /* 立即清空当前页上的结果区（不重绘整个板块） */
  function renderRosterFeedbackPreview() {
    var box = document.querySelector('#admin-content .import-result-box');
    if (box) renderRosterFeedback(box);
  }

  function appendLineErrors(box, errs) {
    var wrap = h('div', { class: 'import-errors' });
    errs.forEach(function (le) {
      var file = le.file ? '[' + le.file + '] ' : '';
      var line = (le.line !== undefined && le.line !== null) ? '第 ' + le.line + ' 行：' : '';
      wrap.appendChild(h('div', { class: 'import-error-item' }, file + line + (le.message || le.error || '数据无效')));
    });
    box.appendChild(wrap);
  }

  function openTransfer(user) {
    state.transferUserId = user.id;
    $('transfer-hint').textContent =
      '将学生「' + (user.name || '') + '（' + (user.student_no || '') + '）」转入目标班级，只更新班级归属，不复制课表数据。';
    var sel = clear($('transfer-class'));
    state.classes.forEach(function (c) {
      if (c.id !== user.class_id && c.status !== 'disabled') {
        sel.appendChild(h('option', { value: String(c.id) }, c.class_name + '（' + c.class_code + '）'));
      }
    });
    hideError($('transfer-error'));
    API.openModal($('modal-transfer'));
  }

  async function saveTransfer() {
    var classId = Number($('transfer-class').value);
    if (!classId) { showError($('transfer-error'), '请选择目标班级'); return; }
    try {
      await API.patch('/api/system/users/' + state.transferUserId, { class_id: classId });
      toast('转班成功', 'success');
      API.closeModal($('modal-transfer'));
      renderStudents();
    } catch (err) {
      if (!err.notified) showError($('transfer-error'), err.message || '转班失败');
    }
  }

  async function toggleUser(user, enable, refresh) {
    var action = enable ? '启用' : '停用';
    if (!window.confirm('确认' + action + '账号「' + (user.name || user.login_id || '') + '」？')) return;
    try {
      await API.patch('/api/system/users/' + user.id, { status: enable ? 'active' : 'disabled' });
      toast('账号已' + action, 'success');
      refresh();
    } catch (err) { API.showError(err); }
  }

  async function resetPassword(user) {
    if (!window.confirm('确认为「' + (user.name || user.login_id || '') + '」重置密码？旧密码将立即失效。')) return;
    try {
      var data = await API.post('/api/system/users/' + user.id + '/reset-password');
      var pwd = API.pickStr(data, ['new_password', 'password', 'initial_password']);
      if (pwd) openSecret('密码重置成功', '新密码如下，请立即复制并线下转达本人：', pwd);
      else toast('密码已重置', 'success');
    } catch (err) { API.showError(err); }
  }

  /* ================= 板块四：管理员管理 ================= */
  async function renderAdmins() {
    var box = clear($('admin-content'));
    var c = section('班级管理员', '任命、交接、启停与重置初始密码', [
      h('button', {
        class: 'btn btn-solid btn-sm', type: 'button',
        onclick: function () {
          $('ap-name').value = ''; $('ap-login').value = ''; $('ap-pwd').value = '';
          hideError($('ap-error'));
          var sel = clear($('ap-class'));
          state.classes.forEach(function (cls) {
            var curAdmin = adminNameOf(cls);
            sel.appendChild(h('option', { value: String(cls.id) },
              cls.class_name + '（' + cls.class_code + '）' + (curAdmin ? ' · 现任：' + curAdmin : '')));
          });
          API.openModal($('modal-appoint'));
        }
      }, icon('plus', 'ico ico-16'), '任命 / 交接管理员')
    ]);
    box.appendChild(c);
    var list;
    try {
      var data = await API.withLoading(API.get('/api/system/users?role=admin&page_size=100'));
      list = API.asList(data);
    } catch (err) { API.showError(err); return; }
    c.appendChild(buildTable([
      { label: '姓名', render: function (r) { return h('span', { class: 'cell-main' }, r.name); } },
      { label: '登录账号', render: function (r) { return r.login_id; } },
      { label: '负责班级', render: function (r) { return r.class_name || classNameOf(r.class_id); } },
      { label: '状态', render: function (r) { return statusBadge(r.status); } },
      {
        label: '操作', render: function (r) {
          var disabled = r.status === 'disabled';
          return h('div', { class: 'row-actions' },
            h('button', {
              class: 'btn ' + (disabled ? 'btn-outline' : 'btn-danger') + ' btn-sm', type: 'button',
              onclick: function () { toggleUser(r, disabled, renderAdmins); }
            }, disabled ? '启用' : '停用'),
            rowMenuButton([
              { label: '重置初始密码', icon: 'key', onClick: function () { resetPassword(r); } }
            ]));
        }
      }
    ], list, '暂无班级管理员，请先任命'));
  }

  async function saveAppoint() {
    var classId = Number($('ap-class').value);
    var name = $('ap-name').value.trim();
    var loginId = $('ap-login').value.trim();
    var pwd = $('ap-pwd').value;
    var errEl = $('ap-error');
    hideError(errEl);
    if (!classId || !name || !loginId || !pwd) { showError(errEl, '请完整填写管理员信息'); return; }
    if (pwd.length < 8) { showError(errEl, '初始密码至少 8 位'); return; }
    var target = null;
    state.classes.forEach(function (c) { if (c.id === classId) target = c; });
    var targetAdmin = adminNameOf(target);
    if (target && targetAdmin &&
      !window.confirm('班级「' + target.class_name + '」已有管理员 ' + targetAdmin +
        '，提交后将停用其账号并完成交接，确认继续？')) return;
    try {
      await API.put('/api/system/classes/' + classId + '/admin', {
        name: name, login_id: loginId, password: pwd
      });
      toast('管理员任命成功', 'success');
      API.closeModal($('modal-appoint'));
      await loadMeta();
      renderAdmins();
    } catch (err) {
      if (!err.notified) showError(errEl, err.message || '任命失败');
    }
  }

  /* ================= 板块五：审计日志 ================= */
  async function renderAudit(page) {
    state.auditPage = page || state.auditPage || 1;
    var box = clear($('admin-content'));
    var c = section('审计日志', '关键业务操作的留痕与追溯');
    box.appendChild(c);

    var actorInput = h('input', { class: 'input', type: 'text', placeholder: '操作者用户 ID', value: state.auditFilters.actor_user_id || '' });
    var classSel = classSelect('audit-class', true);
    classSel.value = state.auditFilters.class_id || '';
    var actionInput = h('input', { class: 'input', type: 'text', placeholder: '动作，如 import', value: state.auditFilters.action || '' });
    var fromInput = h('input', { class: 'input', type: 'date', value: state.auditFilters.date_from || '', 'aria-label': '开始日期' });
    var toInput = h('input', { class: 'input', type: 'date', value: state.auditFilters.date_to || '', 'aria-label': '结束日期' });
    c.appendChild(h('div', { class: 'filter-bar' },
      actorInput, classSel, actionInput, fromInput, h('span', null, '至'), toInput,
      h('button', {
        class: 'btn btn-solid btn-sm', type: 'button',
        onclick: function () {
          state.auditFilters = {
            actor_user_id: actorInput.value.trim(),
            class_id: classSel.value,
            action: actionInput.value.trim(),
            date_from: fromInput.value,
            date_to: toInput.value
          };
          renderAudit(1);
        }
      }, '查询')));

    var f = state.auditFilters;
    var query = '/api/system/audit-logs?page=' + state.auditPage + '&page_size=' + PAGE_SIZE;
    if (f.actor_user_id) query += '&actor_user_id=' + encodeURIComponent(f.actor_user_id);
    if (f.class_id) query += '&class_id=' + encodeURIComponent(f.class_id);
    if (f.action) query += '&action=' + encodeURIComponent(f.action);
    if (f.date_from) query += '&date_from=' + encodeURIComponent(f.date_from);
    if (f.date_to) query += '&date_to=' + encodeURIComponent(f.date_to);

    var data;
    try { data = await API.withLoading(API.get(query)); } catch (err) { API.showError(err); return; }
    var list = API.asList(data);
    c.appendChild(buildTable([
      { label: '时间', render: function (r) { return r.created_at; } },
      { label: '操作者', render: function (r) { return r.actor_name || r.actor_login_id || r.actor_user_id; } },
      { label: '动作', render: function (r) { return r.action; } },
      {
        label: '目标', render: function (r) {
          var t = [r.target_type, r.target_id].filter(function (x) { return x !== null && x !== undefined && x !== ''; });
          return t.length ? t.join(' #') : '—';
        }
      },
      { label: '班级', render: function (r) { return r.class_name || (r.class_id ? classNameOf(r.class_id) : '—'); } },
      { label: '结果', render: function (r) { return r.result === 'success' ? h('span', { class: 'badge badge-green' }, '成功') : h('span', { class: 'badge badge-red' }, r.result || '失败'); } },
      {
        label: '详情', wrap: true, render: function (r) {
          var detail = r.detail !== undefined ? r.detail : r.detail_json;
          if (!detail) return '—';
          var text = typeof detail === 'string' ? detail : JSON.stringify(detail);
          var wrap = h('div', { class: 'audit-detail' },
            h('span', { class: 'audit-detail-text' }, text));
          if (text.length > 40) {
            var toggle = h('button', {
              class: 'audit-detail-toggle', type: 'button',
              onclick: function () {
                var expanded = wrap.classList.toggle('expanded');
                toggle.textContent = expanded ? '收起' : '展开';
              }
            }, '展开');
            wrap.appendChild(toggle);
          }
          return wrap;
        }
      }
    ], list, '暂无审计日志'));
    c.appendChild(buildPagination(API.pageOf(data), function (p) { renderAudit(p); }));
  }

  /* ================= 板块路由 ================= */
  function renderSection(name) {
    state.section = name;
    if (name !== 'students') rosterFeedback = null;
    document.querySelectorAll('#admin-nav .admin-nav-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-section') === name);
    });
    var headerTitle = $('header-title');
    if (headerTitle && SECTION_TITLES[name]) headerTitle.textContent = SECTION_TITLES[name];
    closeDrawer();
    if (name === 'semesters') renderSemesters();
    else if (name === 'classes') renderClasses();
    else if (name === 'students') renderStudents(1);
    else if (name === 'admins') renderAdmins();
    else if (name === 'audit') renderAudit(1);
  }

  /* ================= 抽屉导航（移动端） ================= */
  function openDrawer() {
    $('admin-app').classList.add('drawer-open');
    $('drawer-toggle').setAttribute('aria-expanded', 'true');
  }
  function closeDrawer() {
    var app = $('admin-app');
    if (app) app.classList.remove('drawer-open');
    var t = $('drawer-toggle');
    if (t) t.setAttribute('aria-expanded', 'false');
  }

  /* ================= 登录 / 视图 ================= */
  function showLogin() {
    $('admin-app').classList.add('hidden');
    $('admin-forbidden').classList.add('hidden');
    $('admin-login').classList.remove('hidden');
  }
  function showForbidden() {
    $('admin-app').classList.add('hidden');
    $('admin-login').classList.add('hidden');
    $('admin-forbidden').classList.remove('hidden');
  }
  async function enterApp() {
    $('admin-login').classList.add('hidden');
    $('admin-forbidden').classList.add('hidden');
    $('admin-app').classList.remove('hidden');
    $('admin-name-display').textContent =
      (state.user.name || '') + '（' + (state.user.login_id || '') + '）';
    var avatar = $('admin-avatar');
    if (avatar) avatar.textContent = (state.user.name || state.user.login_id || '系').charAt(0);
    await loadMeta();
    renderSection(state.section);
  }
  function checkRole(user) {
    if (user && user.role === 'system_admin') { state.user = user; enterApp(); }
    else if (user) showForbidden();
    else showLogin();
  }

  async function doLogin() {
    var id = $('admin-login-id').value.trim();
    var pwd = $('admin-login-pwd').value;
    var errEl = $('admin-login-error');
    hideError(errEl);
    if (!id || !pwd) { showError(errEl, '请输入管理员账号和密码'); return; }
    var btn = $('admin-login-btn');
    btn.disabled = true;
    btn.textContent = '登录中…';
    try {
      var data = await API.post('/api/auth/login', { login_id: id, password: pwd });
      API.setSession(data || {});
      var user = API.getUser();
      if (!user) { await API.init().catch(function () { return null; }); user = API.getUser(); }
      if (!user || user.role !== 'system_admin') {
        showForbidden();
      } else {
        state.user = user;
        enterApp();
      }
    } catch (err) {
      if (err.status === 401) showError(errEl, '管理员账号或密码不正确');
      else showError(errEl, err.message || '登录失败，请稍后重试');
    } finally {
      btn.disabled = false;
      btn.textContent = '登 录';
    }
  }

  async function doLogout() {
    try { await API.post('/api/auth/logout'); } catch (e) { /* 忽略 */ }
    API.clearSession();
    window.location.reload();
  }

  /* ================= 静态图标注入 ================= */
  function injectIcons() {
    var pwd = $('admin-toggle-pwd');
    if (pwd) pwd.appendChild(icon('eye', 'ico ico-16'));
    document.querySelectorAll('#admin-nav .admin-nav-btn').forEach(function (btn) {
      btn.insertBefore(icon(btn.getAttribute('data-icon') || 'grid', 'ico'), btn.firstChild);
    });
    var forbidden = document.querySelector('.forbidden-icon');
    if (forbidden) forbidden.appendChild(icon('shield', 'ico'));
    var toggle = $('drawer-toggle');
    if (toggle) toggle.appendChild(icon('menu', 'ico'));
    document.querySelectorAll('.modal-close').forEach(function (el) {
      el.appendChild(icon('x', 'ico ico-16'));
    });
    var warn = $('secret-warning');
    if (warn) warn.insertBefore(icon('alert', 'ico'), warn.firstChild);
  }

  /* ================= 事件绑定与启动 ================= */
  function bindEvents() {
    $('admin-login-btn').addEventListener('click', doLogin);
    $('admin-login-pwd').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    $('admin-login-id').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    $('admin-toggle-pwd').addEventListener('click', function () {
      var input = $('admin-login-pwd');
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      clear($('admin-toggle-pwd')).appendChild(icon(show ? 'eyeOff' : 'eye', 'ico ico-16'));
      $('admin-toggle-pwd').setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
    });
    $('admin-logout').addEventListener('click', doLogout);
    $('forbidden-logout').addEventListener('click', doLogout);
    $('forbidden-back').addEventListener('click', function () { window.location.href = '/'; });
    document.querySelectorAll('#admin-nav .admin-nav-btn').forEach(function (btn) {
      btn.addEventListener('click', function () { renderSection(btn.getAttribute('data-section')); });
    });
    $('drawer-toggle').addEventListener('click', function () {
      if ($('admin-app').classList.contains('drawer-open')) closeDrawer();
      else openDrawer();
    });
    $('drawer-backdrop').addEventListener('click', closeDrawer);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeDrawer();
    });
    $('btn-sem-save').addEventListener('click', saveSemester);
    $('btn-cls-save').addEventListener('click', saveClass);
    $('btn-transfer-save').addEventListener('click', saveTransfer);
    $('btn-ap-save').addEventListener('click', saveAppoint);
    $('btn-secret-copy').addEventListener('click', async function () {
      try {
        await navigator.clipboard.writeText($('secret-text').textContent);
        toast('已复制', 'success');
      } catch (err) { toast('复制失败，请手动选择文本复制'); }
    });
    ['modal-semester', 'modal-class', 'modal-transfer', 'modal-appoint', 'modal-secret']
      .forEach(API.bindModal);
  }

  API.onUnauthorized(showLogin);

  async function boot() {
    injectIcons();
    bindEvents();
    var user = null;
    try { user = await API.init(); } catch (err) { user = null; }
    checkRole(user);
  }
  boot();
})();
