/* 厨务管家 · 方向三 · 招牌墙
   共享 UI：壳、招牌、竖排标签、红章按钮、图标、SVG 折线/条形。 */

(function () {
  var React = window.React;

  function fmt(n) {
    return Number(n).toLocaleString('en-US');
  }

  function money(n) {
    return '¥' + fmt(n);
  }

  function one(n) {
    return Number(n).toFixed(1);
  }

  function pct(n) {
    return one(n) + '%';
  }

  var NAV_ITEMS = [
    { key: 'today', label: '今天', page: 'today' },
    { key: 'business', label: '生意', page: 'sales' },
    { key: 'people', label: '人', page: 'calendar' },
    { key: 'floor', label: '现场', page: 'daily' },
    { key: 'kitchen', label: '厨房', page: null },
    { key: 'system', label: '系统', page: null },
    { key: 'me', label: '我的', page: null }
  ];

  var ROLE_NAV = {
    manager: ['today', 'business', 'people', 'floor', 'kitchen', 'system'],
    boss: ['today', 'business', 'floor'],
    chef: ['today', 'floor', 'kitchen', 'business'],
    staff: ['today', 'floor', 'kitchen', 'me']
  };

  var PHONE_TABS = {
    manager: [
      { key: 'today', label: '今天', page: 'today' },
      { key: 'business', label: '生意', page: 'sales' },
      { key: 'people', label: '人', page: 'calendar' },
      { key: 'floor', label: '现场', page: 'daily' }
    ],
    boss: [
      { key: 'today', label: '今天', page: 'today' },
      { key: 'business', label: '生意', page: 'sales' },
      { key: 'floor', label: '现场', page: 'daily' },
      { key: 'me', label: '我的', page: null }
    ],
    chef: [
      { key: 'today', label: '今天', page: 'today' },
      { key: 'floor', label: '现场', page: 'daily' },
      { key: 'kitchen', label: '厨房', page: null },
      { key: 'me', label: '我的', page: null }
    ],
    staff: [
      { key: 'today', label: '今天', page: 'today' },
      { key: 'floor', label: '卫生', page: 'daily' },
      { key: 'kitchen', label: '厨房', page: null },
      { key: 'me', label: '我的', page: null }
    ]
  };

  var PAGE_DOMAIN = {
    today: 'today',
    sales: 'business',
    daily: 'floor',
    calendar: 'people'
  };

  /* ---------- 图标：只用 SVG，不用 emoji ---------- */

  function Icon(props) {
    var name = props.name;
    var size = props.size || 18;
    var sw = props.strokeWidth || 2.4;
    var common = {
      className: 'icon',
      width: size,
      height: size,
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      strokeWidth: sw,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      'aria-hidden': 'true',
      focusable: 'false'
    };
    if (name === 'check') {
      return React.createElement('svg', common, React.createElement('path', { d: 'M4 12.5l5 5L20 6.5' }));
    }
    if (name === 'close') {
      return React.createElement('svg', common,
        React.createElement('path', { d: 'M6 6l12 12' }),
        React.createElement('path', { d: 'M18 6L6 18' })
      );
    }
    if (name === 'chevron') {
      return React.createElement('svg', common, React.createElement('path', { d: 'M9 5l7 7-7 7' }));
    }
    if (name === 'undo') {
      return React.createElement('svg', common,
        React.createElement('path', { d: 'M9 6H4v5' }),
        React.createElement('path', { d: 'M4 11c1.6-3.2 4.6-5 8.2-4.6 3.4.4 6 3 6.4 6.4.4 3.4-1.6 6.4-4.8 7.4' })
      );
    }
    if (name === 'download') {
      return React.createElement('svg', common,
        React.createElement('path', { d: 'M12 3v12' }),
        React.createElement('path', { d: 'M7 10l5 5 5-5' }),
        React.createElement('path', { d: 'M4 20h16' })
      );
    }
    if (name === 'up') {
      return React.createElement('svg', Object.assign({}, common, { fill: 'currentColor', stroke: 'none' }),
        React.createElement('path', { d: 'M12 5l5 8H7l5-8z' })
      );
    }
    if (name === 'alert') {
      return React.createElement('svg', common,
        React.createElement('path', { d: 'M12 4l9 16H3L12 4z' }),
        React.createElement('path', { d: 'M12 10v4' }),
        React.createElement('path', { d: 'M12 17.4v.6' })
      );
    }
    return null;
  }

  /* ---------- 招牌：实色块 + 奶白描边 + 硬投影 + 竖排窄条 ---------- */

  function Sign(props) {
    var cls = ['sign', 'sign--' + (props.tone || 'wall2')];
    if (props.tag) cls.push('sign--tagged');
    if (props.className) cls.push(props.className);
    var style = Object.assign({}, props.style || {});
    if (props.grow != null) style.flexGrow = props.grow;
    return React.createElement(
      'section',
      { className: cls.join(' '), style: style, 'aria-label': props.ariaLabel },
      props.tag
        ? React.createElement(
            'div',
            { className: 'sign__tag sign__tag--' + (props.tagTone || 'ink') },
            React.createElement('span', { className: 'vert' }, props.tag)
          )
        : null,
      React.createElement('div', { className: 'sign__body' }, props.children)
    );
  }

  function Stamp(props) {
    var cls = ['stamp'];
    if (props.ghost) cls.push('stamp--ghost');
    if (props.wide) cls.push('stamp--wide');
    if (props.className) cls.push(props.className);
    return React.createElement(
      'button',
      {
        type: 'button',
        className: cls.join(' '),
        onClick: props.onClick,
        disabled: props.disabled,
        'aria-label': props.ariaLabel,
        style: props.style
      },
      props.icon ? React.createElement(Icon, { name: props.icon, size: 16 }) : null,
      props.children
    );
  }

  /* ---------- 桌面壳 ---------- */

  function Rail(props) {
    var roleKey = props.roleKey || 'manager';
    var keys = ROLE_NAV[roleKey] || ROLE_NAV.manager;
    var items = keys
      .map(function (k) {
        return NAV_ITEMS.filter(function (n) { return n.key === k; })[0];
      })
      .filter(Boolean);

    return React.createElement(
      'nav',
      { className: 'rail', 'aria-label': '主导航' },
      React.createElement('div', { className: 'rail-brand', 'aria-label': '厨务管家' }, '厨'),
      React.createElement(
        'div',
        { className: 'rail-nav' },
        items.map(function (item) {
          var on = item.key === props.current;
          var off = !item.page;
          var cls = ['rail-item'];
          if (on) cls.push('is-on');
          if (off) cls.push('is-off');
          return React.createElement(
            'button',
            {
              key: item.key,
              type: 'button',
              className: cls.join(' '),
              onClick: function () { if (!off && props.onNav) props.onNav(item.page); },
              'aria-current': on ? 'page' : undefined,
              'aria-disabled': off ? 'true' : undefined
            },
            React.createElement('span', { className: 'rail-item__text' }, item.label)
          );
        })
      ),
      React.createElement('div', { className: 'rail-spacer' }),
      React.createElement(
        'div',
        { className: 'rail-foot' },
        React.createElement('span', { className: 'live__dot' }),
        React.createElement('span', { className: 'rail-foot__text' }, '营业中')
      )
    );
  }

  function StatusBar(props) {
    var role = props.role;
    var store = window.DATA.store;
    return React.createElement(
      'header',
      { className: 'statusbar' },
      React.createElement(
        'div',
        { className: 'status-store' },
        React.createElement('span', { className: 'status-chop' }),
        React.createElement('span', { className: 'status-name' }, store.name),
        React.createElement('span', { className: 'status-branch' }, store.branch)
      ),
      React.createElement('span', { className: 'status-sep' }),
      React.createElement('span', { className: 'status-day' }, '营业日 ' + store.businessDayLabel),
      React.createElement(
        'div',
        { className: 'status-right' },
        React.createElement(
          'div',
          { className: 'live' },
          React.createElement('span', { className: 'live__dot' }),
          React.createElement('span', { className: 'live__text' }, '实时已连接')
        ),
        React.createElement(
          'div',
          { className: 'identity' },
          React.createElement('span', { className: 'identity__role' }, role.label),
          React.createElement('span', { className: 'identity__name' }, role.name)
        )
      )
    );
  }

  /* ---------- 手机壳 ---------- */

  function PhoneShell(props) {
    var role = props.role;
    var roleKey = props.roleKey;
    var tabs = PHONE_TABS[roleKey] || PHONE_TABS.manager;
    return React.createElement(
      'div',
      { className: 'stage' },
      React.createElement(
        'div',
        { className: 'phone' },
        React.createElement(
          'header',
          { className: 'phone-top' },
          React.createElement('span', { className: 'phone-top__chop' }),
          React.createElement('span', { className: 'phone-top__title' }, props.title),
          props.date ? React.createElement('span', { className: 'phone-top__date' }, props.date) : null,
          React.createElement('span', { className: 'phone-top__role' }, role.label + ' ' + role.name)
        ),
        React.createElement('div', { className: 'phone-body' }, props.children),
        React.createElement(
          'nav',
          { className: 'phone-tabs', 'aria-label': '底部导航' },
          tabs.map(function (t) {
            var on = t.key === props.activeTab;
            var off = !t.page;
            var cls = ['ptab'];
            if (on) cls.push('is-on');
            if (off) cls.push('is-off');
            return React.createElement(
              'button',
              {
                key: t.key,
                type: 'button',
                className: cls.join(' '),
                onClick: function () { if (!off && props.onNav) props.onNav(t.page); },
                'aria-current': on ? 'page' : undefined,
                'aria-disabled': off ? 'true' : undefined
              },
              t.label
            );
          })
        ),
        props.overlay ? props.overlay : null
      )
    );
  }

  /* ---------- 数据小件 ---------- */

  function Occupancy(props) {
    var filled = props.filled;
    var total = props.total;
    var cells = [];
    for (var i = 0; i < total; i += 1) {
      cells.push(
        React.createElement('span', {
          key: i,
          className: 'occ__cell' + (i < filled ? ' is-on' : '')
        })
      );
    }
    return React.createElement('div', { className: 'occ', 'aria-label': '在店桌台 ' + filled + ' / ' + total }, cells);
  }

  function StationBars(props) {
    return React.createElement(
      'div',
      { className: 'station-list' },
      props.items.map(function (s) {
        return React.createElement(
          'div',
          { className: 'station-row', key: s.name },
          React.createElement('div', { className: 'station-name' }, s.name),
          React.createElement(
            'div',
            { className: 'station-track' },
            React.createElement('div', {
              className: 'station-fill',
              style: { width: s.pct + '%', background: 'var(--' + s.tone + ')' }
            })
          ),
          React.createElement('div', { className: 'station-pct num' }, pct(s.pct))
        );
      })
    );
  }

  /* 折线：黑底招牌里的 SVG，橙黄线，标最高点与周六 */
  function SalesLine(props) {
    var days = props.days;
    var W = 320;
    var H = 124;
    var left = 12;
    var right = 308;
    var top = 22;
    var bottom = 88;

    var vals = days.map(function (d) { return d.value; });
    var max = Math.max.apply(null, vals);
    var min = Math.min.apply(null, vals);
    var maxI = vals.indexOf(max);

    function x(i) {
      return left + (i * (right - left)) / (days.length - 1);
    }
    function y(v) {
      return bottom - ((v - min) / (max - min)) * (bottom - top);
    }

    var points = days.map(function (d, i) { return x(i) + ',' + y(d.value); }).join(' ');

    var grid = [0, 1, 2].map(function (k) {
      var gy = top + (k * (bottom - top)) / 2;
      return React.createElement('line', {
        key: 'g' + k,
        x1: left,
        x2: right,
        y1: gy,
        y2: gy,
        stroke: 'var(--paper-faint)',
        strokeWidth: 1
      });
    });

    var dots = days.map(function (d, i) {
      var isMax = i === maxI;
      return React.createElement('circle', {
        key: 'd' + d.label,
        cx: x(i),
        cy: y(d.value),
        r: isMax ? 5 : 3.5,
        fill: 'var(--paper)',
        stroke: 'var(--yellow)',
        strokeWidth: isMax ? 3 : 2
      });
    });

    var xLabels = days.map(function (d, i) {
      return React.createElement(
        'text',
        {
          key: 'x' + d.label,
          x: x(i),
          y: 108,
          textAnchor: 'middle',
          style: {
            fill: i === 5 ? 'var(--yellow)' : 'var(--paper-dim)',
            fontSize: 14,
            fontWeight: i === 5 ? 900 : 700,
            fontFamily: 'var(--font)'
          }
        },
        d.label
      );
    });

    return React.createElement(
      'svg',
      {
        className: 'chart-svg',
        viewBox: '0 0 ' + W + ' ' + H,
        role: 'img',
        'aria-label': '近 7 天营业额折线，周六最高，' + fmt(max) + ' 元'
      },
      React.createElement('title', null, '近 7 天营业额折线'),
      grid,
      React.createElement('line', {
        x1: x(maxI),
        x2: x(maxI),
        y1: top,
        y2: bottom,
        stroke: 'var(--paper-faint)',
        strokeWidth: 1,
        strokeDasharray: '3 4'
      }),
      React.createElement('polyline', {
        points: points,
        fill: 'none',
        stroke: 'var(--yellow)',
        strokeWidth: 3,
        strokeLinejoin: 'round',
        strokeLinecap: 'round'
      }),
      dots,
      React.createElement(
        'text',
        {
          x: Math.min(Math.max(x(maxI), 34), W - 34),
          y: 15,
          textAnchor: 'middle',
          style: {
            fill: 'var(--yellow)',
            fontSize: 14,
            fontWeight: 900,
            fontFamily: 'var(--font)'
          }
        },
        fmt(max)
      ),
      xLabels
    );
  }

  /* 抽象不锈钢台面：灰面 + 油污点 + 红圈标注，不用照片。
     竖幅构图，正好落在手机里两块并排的半屏照片框内。 */
  function Stove(props) {
    var dirty = props.dirty;
    var brushed = [];
    for (var i = 0; i < 35; i += 1) {
      brushed.push(
        React.createElement('line', {
          key: 'b' + i,
          x1: 0,
          x2: 180,
          y1: 92 + i * 8.4,
          y2: 92 + i * 8.4,
          stroke: 'var(--steel-1)',
          strokeOpacity: dirty ? 0.1 : 0.2,
          strokeWidth: 1
        })
      );
    }

    var dirt = dirty
      ? React.createElement(
          'g',
          null,
          React.createElement('ellipse', { cx: 88, cy: 232, rx: 36, ry: 19, fill: 'var(--ink)', opacity: 0.62 }),
          React.createElement('ellipse', { cx: 112, cy: 255, rx: 19, ry: 11, fill: 'var(--ink)', opacity: 0.55 }),
          React.createElement('ellipse', { cx: 66, cy: 272, rx: 22, ry: 9, fill: 'var(--ink)', opacity: 0.48 }),
          React.createElement('path', {
            d: 'M40 168 Q68 190 102 182',
            stroke: 'var(--ink)',
            strokeWidth: 10,
            strokeLinecap: 'round',
            fill: 'none',
            opacity: 0.4
          }),
          React.createElement('ellipse', { cx: 93, cy: 230, rx: 9, ry: 4, fill: 'var(--steel-1)', opacity: 0.45 }),
          React.createElement('ellipse', { cx: 118, cy: 252, rx: 5, ry: 2.5, fill: 'var(--steel-1)', opacity: 0.35 }),
          React.createElement('path', {
            d: 'M98 386 q-5 18 2 34',
            stroke: 'var(--ink)',
            strokeWidth: 6,
            fill: 'none',
            opacity: 0.55
          })
        )
      : null;

    return React.createElement(
      'svg',
      {
        className: 'photo__svg',
        viewBox: '0 0 180 430',
        preserveAspectRatio: 'xMidYMid slice',
        role: 'img',
        'aria-label': dirty ? '实拍：灶台表面有油污与残渣' : '标准图：洁净的不锈钢灶台'
      },
      React.createElement('rect', { x: 0, y: 0, width: 180, height: 430, fill: 'var(--steel-4)' }),

      /* 后挡板 */
      React.createElement('rect', { x: 0, y: 0, width: 180, height: 86, fill: 'var(--steel-3)' }),
      React.createElement('line', { x1: 0, y1: 86, x2: 180, y2: 86, stroke: 'var(--steel-1)', strokeWidth: 2 }),
      React.createElement('line', { x1: 60, y1: 0, x2: 60, y2: 86, stroke: 'var(--steel-4)', strokeWidth: 1, opacity: 0.55 }),
      React.createElement('line', { x1: 120, y1: 0, x2: 120, y2: 86, stroke: 'var(--steel-4)', strokeWidth: 1, opacity: 0.55 }),

      /* 台面与前沿 */
      React.createElement('rect', {
        x: 0,
        y: 86,
        width: 180,
        height: 300,
        fill: dirty ? 'var(--steel-3)' : 'var(--steel-2)'
      }),
      React.createElement('rect', { x: 0, y: 386, width: 180, height: 44, fill: 'var(--steel-3)' }),
      React.createElement('line', { x1: 0, y1: 386, x2: 180, y2: 386, stroke: 'var(--steel-1)', strokeWidth: 2 }),
      brushed,

      /* 两个灶眼 */
      React.createElement('circle', { cx: 56, cy: 198, r: 44, fill: 'var(--steel-3)', stroke: 'var(--steel-4)', strokeWidth: 4 }),
      React.createElement('circle', { cx: 56, cy: 198, r: 25, fill: 'var(--steel-4)' }),
      React.createElement('line', { x1: 22, y1: 198, x2: 90, y2: 198, stroke: 'var(--steel-4)', strokeWidth: 4 }),
      React.createElement('circle', { cx: 126, cy: 306, r: 38, fill: 'var(--steel-3)', stroke: 'var(--steel-4)', strokeWidth: 4 }),
      React.createElement('circle', { cx: 126, cy: 306, r: 20, fill: 'var(--steel-4)' }),
      React.createElement('line', { x1: 100, y1: 306, x2: 152, y2: 306, stroke: 'var(--steel-4)', strokeWidth: 4 }),

      dirt,

      /* 检查范围：实线圈 + 虚线圈 */
      React.createElement('circle', {
        cx: 88,
        cy: 250,
        r: 70,
        fill: 'none',
        stroke: 'var(--red)',
        strokeWidth: 3
      }),
      React.createElement('circle', {
        cx: 88,
        cy: 250,
        r: 77,
        fill: 'none',
        stroke: 'var(--red)',
        strokeWidth: 1,
        strokeDasharray: '4 5',
        opacity: 0.75
      })
    );
  }

  Object.assign(window, {
    fmt: fmt,
    money: money,
    one: one,
    pct: pct,
    Icon: Icon,
    Sign: Sign,
    Stamp: Stamp,
    Rail: Rail,
    StatusBar: StatusBar,
    PhoneShell: PhoneShell,
    Occupancy: Occupancy,
    StationBars: StationBars,
    SalesLine: SalesLine,
    Stove: Stove,
    NAV_ITEMS: NAV_ITEMS,
    ROLE_NAV: ROLE_NAV,
    PHONE_TABS: PHONE_TABS,
    PAGE_DOMAIN: PAGE_DOMAIN
  });
})();
