/* ============================================================
 * 班级课表助手 · 统一 API 客户端
 * - 同源 credentials（Session Cookie）
 * - 启动时通过 GET /api/auth/me 恢复身份并缓存 csrf_token，
 *   之后所有写请求自动携带 X-CSRF-Token 头
 * - 统一解析 {"code","message","details"} 错误体：
 *   401 → 触发 onUnauthorized（页面切回登录视图）
 *   403 → 全局 toast 提示无权限
 *   409 → 抛出错误由页面处理（保留表单并展示冲突详情）
 * - 附带 toast / loading / DOM 创建（textContent 防 XSS）工具
 * ============================================================ */
(function () {
  'use strict';

  var csrfToken = null;
  var currentUser = null;
  var meExtra = null; // /api/auth/me 响应中除 user 外的其余字段（如 semester）
  var unauthorizedHandler = null;

  function makeError(status, code, message, details) {
    var err = new Error(message || '请求失败（HTTP ' + status + '）');
    err.status = status;
    err.code = code || 'ERROR';
    err.details = details || null;
    err.notified = false; // 是否已做过用户提示（避免页面重复 toast）
    return err;
  }

  async function request(path, opts) {
    opts = opts || {};
    var method = (opts.method || 'GET').toUpperCase();
    var headers = {};
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken || '';
    var resp;
    try {
      resp = await fetch(path, {
        method: method,
        headers: headers,
        credentials: 'same-origin',
        body: opts.formData ? opts.formData
          : (opts.body !== undefined ? JSON.stringify(opts.body) : undefined)
      });
    } catch (netErr) {
      throw makeError(0, 'NETWORK_ERROR', '网络异常，请检查连接后重试');
    }
    var data = null;
    if (resp.status !== 204) {
      try { data = await resp.json(); } catch (parseErr) { data = null; }
    }
    if (!resp.ok) {
      var err = makeError(
        resp.status,
        data && data.code,
        (data && data.message) || null,
        data && data.details
      );
      if (resp.status === 401) {
        // 未登录或会话失效：清空本地身份并通知页面切回登录视图
        currentUser = null;
        csrfToken = null;
        err.notified = true;
        if (unauthorizedHandler) unauthorizedHandler();
      } else if (resp.status === 403) {
        toast(err.message || '无权限执行此操作', 'error');
        err.notified = true;
      }
      throw err;
    }
    return data;
  }

  function get(path) { return request(path); }
  function post(path, body) { return request(path, { method: 'POST', body: body }); }
  function put(path, body) { return request(path, { method: 'PUT', body: body }); }
  function patch(path, body) { return request(path, { method: 'PATCH', body: body }); }
  function del(path) { return request(path, { method: 'DELETE' }); }
  function upload(path, formData) { return request(path, { method: 'POST', formData: formData }); }

  /* 后端用户对象不含班级字段，登录/me/激活响应里班级在同级 class 字段：
   * 合并到 user 上，页面可直接读 user.class_name / user.class_code */
  function mergeClass(user, data) {
    if (!user || !data || !data.class) return user;
    if (!user.class_name && data.class.class_name) user.class_name = data.class.class_name;
    if (!user.class_code && data.class.class_code) user.class_code = data.class.class_code;
    return user;
  }

  /* 启动恢复身份：返回当前用户或 null（未登录） */
  async function init() {
    try {
      var data = await get('/api/auth/me');
      var user = (data && data.user) || (data && data.role ? data : null);
      if (!user) { currentUser = null; return null; }
      currentUser = mergeClass(user, data);
      meExtra = data || null;
      if (data && data.csrf_token) csrfToken = data.csrf_token;
      return currentUser;
    } catch (err) {
      if (err.status === 401) { currentUser = null; return null; }
      throw err;
    }
  }

  /* 登录/激活成功后缓存会话信息（响应 JSON 内含 csrf_token） */
  function setSession(data) {
    if (!data) return;
    if (data.csrf_token) csrfToken = data.csrf_token;
    if (data.user) currentUser = mergeClass(data.user, data);
    meExtra = data;
  }

  function clearSession() {
    csrfToken = null;
    currentUser = null;
    meExtra = null;
  }

  /* ---------- Toast ---------- */
  var toastTimer = null;
  function toast(message, type) {
    var box = document.getElementById('global-toast');
    if (!box) {
      box = document.createElement('div');
      box.id = 'global-toast';
      box.className = 'toast';
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      document.body.appendChild(box);
    }
    box.textContent = String(message || '');
    box.className = 'toast show ' + (type || 'info');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.classList.remove('show'); }, 2200);
  }

  /* 统一错误提示：401/403 已提示过的不再重复 */
  function showError(err) {
    if (!err) return;
    if (err.notified) return;
    toast(err.message || '操作失败，请稍后重试', 'error');
  }

  /* ---------- Loading 遮罩 ---------- */
  var loadingCount = 0;
  function maskEl() {
    var mask = document.getElementById('global-loading');
    if (!mask) {
      mask = document.createElement('div');
      mask.id = 'global-loading';
      mask.className = 'loading-mask';
      var spinner = document.createElement('div');
      spinner.className = 'loading-spinner';
      mask.appendChild(spinner);
      document.body.appendChild(mask);
    }
    return mask;
  }
  function showLoading() {
    loadingCount += 1;
    if (loadingCount === 1) maskEl().classList.add('show');
  }
  function hideLoading() {
    loadingCount = Math.max(0, loadingCount - 1);
    if (loadingCount === 0) maskEl().classList.remove('show');
  }
  async function withLoading(promise) {
    showLoading();
    try { return await promise; } finally { hideLoading(); }
  }

  /* ---------- DOM 工具（一律 textContent/创建元素，防 XSS） ---------- */
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined) return;
        if (key === 'class') el.className = value;
        else if (key === 'text') el.textContent = String(value);
        else if (key === 'value') el.value = value;
        else if (key === 'checked') el.checked = !!value;
        else if (key === 'disabled') el.disabled = !!value;
        else if (key.slice(0, 2) === 'on' && typeof value === 'function') {
          el.addEventListener(key.slice(2), value);
        } else el.setAttribute(key, value);
      });
    }
    for (var i = 2; i < arguments.length; i += 1) {
      appendChild(el, arguments[i]);
    }
    return el;
  }
  function appendChild(el, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
      child.forEach(function (c) { appendChild(el, c); });
      return;
    }
    if (typeof child === 'string' || typeof child === 'number') {
      el.appendChild(document.createTextNode(String(child)));
    } else {
      el.appendChild(child);
    }
  }
  function clear(el) {
    if (el) el.textContent = '';
    return el;
  }

  /* ---------- 弹窗（ESC 关闭、焦点管理与焦点圈定） ---------- */
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function dialogOf(root) {
    return root.querySelector('.modal-dialog') || root;
  }

  function openModal(root) {
    if (typeof root === 'string') root = document.getElementById(root);
    if (!root) return;
    root.classList.add('show');
    root._prevFocus = document.activeElement;
    var dialog = dialogOf(root);
    if (!dialog.hasAttribute('tabindex')) dialog.setAttribute('tabindex', '-1');
    var first = dialog.querySelector(FOCUSABLE);
    (first || dialog).focus();
  }
  function closeModal(root) {
    if (typeof root === 'string') root = document.getElementById(root);
    if (!root) return;
    root.classList.remove('show');
    var prev = root._prevFocus;
    if (prev && typeof prev.focus === 'function' && document.contains(prev)) {
      try { prev.focus(); } catch (e) { /* 忽略 */ }
    }
    root._prevFocus = null;
  }
  /* 绑定弹窗：data-close 元素、ESC 关闭、Tab 焦点圈定 */
  function bindModal(root) {
    if (typeof root === 'string') root = document.getElementById(root);
    if (!root) return;
    root.querySelectorAll('[data-close]').forEach(function (el) {
      el.addEventListener('click', function () { closeModal(root); });
    });
    if (root._a11yBound) return;
    root._a11yBound = true;
    root.addEventListener('keydown', function (e) {
      if (!root.classList.contains('show')) return;
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeModal(root);
        return;
      }
      if (e.key !== 'Tab') return;
      var dialog = dialogOf(root);
      var items = Array.prototype.filter.call(
        dialog.querySelectorAll(FOCUSABLE),
        function (el) { return el.offsetParent !== null || el === document.activeElement; }
      );
      if (!items.length) return;
      var first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    });
  }

  /* ---------- 响应体容错工具（字段名集成时对齐） ---------- */
  /* 列表响应统一为 {items,page,page_size,total}；容忍直接返回数组 */
  function asList(data) {
    if (!data) return [];
    if (Array.isArray(data)) return data;
    if (Array.isArray(data.items)) return data.items;
    return [];
  }
  function pageOf(data) {
    return {
      page: (data && data.page) || 1,
      page_size: (data && data.page_size) || 20,
      total: (data && typeof data.total === 'number') ? data.total : asList(data).length
    };
  }
  /* 从对象中按候选键取第一个数字 */
  function pickNum(obj, keys) {
    if (!obj) return 0;
    for (var i = 0; i < keys.length; i += 1) {
      var v = obj[keys[i]];
      if (typeof v === 'number' && isFinite(v)) return v;
    }
    return 0;
  }
  function pickStr(obj, keys) {
    if (!obj) return '';
    for (var i = 0; i < keys.length; i += 1) {
      var v = obj[keys[i]];
      if (typeof v === 'string' && v) return v;
    }
    return '';
  }

  window.API = {
    request: request,
    get: get, post: post, put: put, patch: patch, del: del, upload: upload,
    init: init, setSession: setSession, clearSession: clearSession,
    getUser: function () { return currentUser; },
    getMeExtra: function () { return meExtra; },
    onUnauthorized: function (fn) { unauthorizedHandler = fn; },
    toast: toast, showError: showError,
    showLoading: showLoading, hideLoading: hideLoading, withLoading: withLoading,
    h: h, clear: clear,
    openModal: openModal, closeModal: closeModal, bindModal: bindModal,
    asList: asList, pageOf: pageOf, pickNum: pickNum, pickStr: pickStr
  };
})();
