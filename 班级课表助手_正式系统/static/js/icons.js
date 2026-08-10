/* ============================================================
 * 班级课表助手 · 统一 SVG 图标集（1.75px stroke，16/18/20px）
 * 用法：API.icon('search') 返回 SVG 元素；API.icon('search','ico ico-16')
 * 仅用于界面图标，服务端数据仍走 textContent。
 * ============================================================ */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  /* 每个图标为 path/circle/rect/line 的 SVG 内部片段 */
  var PATHS = {
    calendar: '<rect x="3" y="4.5" width="18" height="16" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/>',
    calendarMark: '<rect x="3" y="4.5" width="18" height="16" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/><rect x="6.6" y="12.4" width="4.2" height="3.4" rx="0.8" fill="currentColor" stroke="none"/><rect x="13.2" y="12.4" width="4.2" height="3.4" rx="0.8" fill="currentColor" stroke="none" opacity=".45"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20.2 20.2-3.8-3.8"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    chevronLeft: '<path d="m14.5 6-6 6 6 6"/>',
    chevronRight: '<path d="m9.5 6 6 6-6 6"/>',
    chevronDown: '<path d="m6 9.5 6 6 6-6"/>',
    more: '<circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none"/>',
    download: '<path d="M12 3.5v11m0 0 4-4m-4 4-4-4"/><path d="M4.5 16.5v2.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-2.5"/>',
    upload: '<path d="M12 14.5v-11m0 0-4 4m4-4 4 4"/><path d="M4.5 16.5v2.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-2.5"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.65 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.08A1.7 1.7 0 0 0 10.12 3V3a2 2 0 1 1 4 0v.09c0 .68.4 1.3 1.03 1.56.6.25 1.32.1 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.08c.26.63.88 1.03 1.56 1.03H21a2 2 0 1 1 0 4h-.09c-.68 0-1.3.4-1.51 1.03Z"/>',
    logout: '<path d="M9 21H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M4 4.5 20 19.5"/><path d="M10.6 6c.46-.07.93-.1 1.4-.1 6 0 9.5 6.1 9.5 6.1a17 17 0 0 1-2.7 3.4M6.6 7.4A16.4 16.4 0 0 0 2.5 12S6 18.1 12 18.1c1.2 0 2.3-.22 3.3-.6"/><path d="M9.9 10.4a3 3 0 0 0 4.2 4.2"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    location: '<path d="M19 10.5c0 5-7 10.5-7 10.5s-7-5.5-7-10.5a7 7 0 0 1 14 0Z"/><circle cx="12" cy="10.5" r="2.6"/>',
    user: '<circle cx="12" cy="8" r="3.6"/><path d="M4.5 20.5c.8-3.6 3.9-5.5 7.5-5.5s6.7 1.9 7.5 5.5"/>',
    book: '<path d="M4 19.5V5.8A1.8 1.8 0 0 1 5.8 4H20v14.5H6a2 2 0 0 0-2 2Zm0 0A1.8 1.8 0 0 1 5.8 21H20"/>',
    file: '<path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5L13.5 3Z"/><path d="M13.5 3v5.5H19"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4L12 4Z"/><path d="M12 10v4m0 2.6v.4"/>',
    check: '<path d="m4.5 12.5 5 5 10-11"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    edit: '<path d="M12 20h8.5"/><path d="M16.7 3.8a2.1 2.1 0 0 1 3 3L8 18.5l-4.2 1.2L5 15.5 16.7 3.8Z"/>',
    trash: '<path d="M4 7h16M9.5 7V4.8A.8.8 0 0 1 10.3 4h3.4a.8.8 0 0 1 .8.8V7m3.5 0-.8 12.2a1.8 1.8 0 0 1-1.8 1.8H8.4a1.8 1.8 0 0 1-1.8-1.8L5.8 7"/><path d="M10 11v6m4-6v6"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    arrowLeft: '<path d="M19 12H5m0 0 6-6m-6 6 6 6"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    users: '<circle cx="9" cy="8.5" r="3.2"/><path d="M2.8 19.5c.7-3.1 3.3-4.8 6.2-4.8s5.5 1.7 6.2 4.8"/><path d="M15.5 5.7a3.2 3.2 0 0 1 0 5.7M17.8 15c1.9.7 3.1 2.1 3.6 4.5"/>',
    shield: '<path d="M12 2.8 4.5 5.6v5.6c0 4.6 3.2 8.4 7.5 10 4.3-1.6 7.5-5.4 7.5-10V5.6L12 2.8Z"/>',
    list: '<path d="M8.5 6.5h11M8.5 12h11M8.5 17.5h11"/><path d="M4 6.5h.5M4 12h.5M4 17.5h.5"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="1.2"/><rect x="13" y="4" width="7" height="7" rx="1.2"/><rect x="4" y="13" width="7" height="7" rx="1.2"/><rect x="13" y="13" width="7" height="7" rx="1.2"/>',
    printer: '<path d="M7 8V3.5h10V8"/><rect x="3.5" y="8" width="17" height="8.5" rx="1.8"/><path d="M7 13.5h10v7H7z"/>',
    key: '<circle cx="8" cy="14.5" r="4.5"/><path d="m11.3 11.3 8.2-8.2M17 5l2.5 2.5M14 8l2 2"/>',
    refresh: '<path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1"/><path d="M20.5 3.5v4.3h-4.3"/>',
    inbox: '<path d="M3.5 13.5 6 4.8A1.5 1.5 0 0 1 7.4 3.7h9.2A1.5 1.5 0 0 1 18 4.8l2.5 8.7v5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5v-5Z"/><path d="M3.5 13.5H9a3 3 0 0 0 6 0h5.5"/>',
    transfer: '<path d="M4 8h13m0 0-3.5-3.5M17 8l-3.5 3.5"/><path d="M20 16H7m0 0 3.5-3.5M7 16l3.5 3.5"/>',
    history: '<path d="M4 12a8 8 0 1 1 2.3 5.7"/><path d="M4 12H2.5M4 12l-1.8 2.5M12 8v4l3 2"/>'
  };
  function icon(name, cls) {
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.75');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', cls || 'ico');
    /* 图标为本地静态常量，非用户数据，可用 innerHTML 注入 SVG 片段 */
    svg.innerHTML = PATHS[name] || PATHS.calendar;
    return svg;
  }
  window.ICON = icon;
})();
