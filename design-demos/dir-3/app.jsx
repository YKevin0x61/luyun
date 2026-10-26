/* 厨务管家 · 方向三 · 招牌墙
   读 ?p=&role= 选页与身份；桌面页铺满视口，手机页居中在 390×844 框内。 */

(function () {
  var React = window.React;
  var ReactDOM = window.ReactDOM;
  var DATA = window.DATA;
  var Rail = window.Rail;
  var StatusBar = window.StatusBar;
  var TodayPage = window.TodayPage;
  var SalesPage = window.SalesPage;
  var DailyPage = window.DailyPage;
  var CalendarPage = window.CalendarPage;
  var PAGE_DOMAIN = window.PAGE_DOMAIN;

  var PAGES = ['today', 'sales', 'daily', 'calendar'];
  var ROLE_KEYS = ['manager', 'boss', 'chef', 'staff'];

  function readParams() {
    var p = 'today';
    var role = 'manager';
    try {
      var params = new URLSearchParams(window.location.search);
      var qp = params.get('p');
      var qr = params.get('role');
      if (qp && PAGES.indexOf(qp) !== -1) p = qp;
      if (qr && ROLE_KEYS.indexOf(qr) !== -1) role = qr;
    } catch (e) {
      /* 原型不需要兼容老浏览器 */
    }
    return { p: p, role: role };
  }

  function App() {
    var q = readParams();
    var roleKey = q.role;
    var role = DATA.roles[roleKey];
    var page = q.p;
    var current = PAGE_DOMAIN[page] || 'today';

    function go(next) {
      window.location.href = '?p=' + next + '&role=' + roleKey;
    }

    var canToday = roleKey === 'manager';
    var canDaily = roleKey === 'manager' || roleKey === 'chef';
    var canCalendar = roleKey === 'manager';

    if (page === 'sales') {
      return React.createElement(SalesPage, { role: role, roleKey: roleKey, go: go });
    }
    if (page === 'daily') {
      return React.createElement(DailyPage, {
        role: role,
        roleKey: roleKey,
        go: go,
        readOnly: !canDaily
      });
    }

    var body =
      page === 'calendar'
        ? React.createElement(CalendarPage, { readOnly: !canCalendar })
        : React.createElement(TodayPage, { go: go, readOnly: !canToday });

    return React.createElement(
      'div',
      { className: 'desk' },
      React.createElement(Rail, { roleKey: roleKey, current: current, onNav: go }),
      React.createElement(
        'div',
        { className: 'desk-main' },
        React.createElement(StatusBar, { role: role }),
        React.createElement('main', { className: 'desk-page' }, body)
      )
    );
  }

  var root = document.getElementById('root');
  ReactDOM.createRoot(root).render(React.createElement(App));
})();
