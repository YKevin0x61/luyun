/* 厨务管家 · 方向三 · 招牌墙
   四张代表页：今天 / 销售报表 / 日常验收 / 排班月历。 */

(function () {
  var React = window.React;
  var useState = React.useState;
  var useEffect = React.useEffect;

  var Sign = window.Sign;
  var Stamp = window.Stamp;
  var Icon = window.Icon;
  var Occupancy = window.Occupancy;
  var StationBars = window.StationBars;
  var SalesLine = window.SalesLine;
  var Stove = window.Stove;
  var PhoneShell = window.PhoneShell;
  var fmt = window.fmt;
  var money = window.money;
  var one = window.one;
  var pct = window.pct;

  function shortDate(date) {
    var p = date.split('-');
    return Number(p[1]) + '.' + p[2];
  }

  /* =========================================================
     1 · 今天（桌面 1440，店长）
     ========================================================= */

  function TodayPage(props) {
    var D = window.DATA;
    var T = D.today;
    var S = D.shifts;
    var readOnly = !!props.readOnly;
    var leaveState = useState('pending');
    var leave = leaveState[0];
    var setLeave = leaveState[1];
    var working = S.early.count + S.mid.count + S.late.count;
    var prepTicks = [];
    D.prep.items.forEach(function (it) {
      for (var i = 0; i < it.n; i += 1) prepTicks.push(it.tone);
    });

    return React.createElement(
      'div',
      { className: 'wall' },

      /* 第一排：生意 · 现场 · 生意 */
      React.createElement(
        'div',
        { className: 'wall-row', style: { flexGrow: 1.05 } },
        React.createElement(
          Sign,
          { tone: 'red', tag: '生意', grow: 5.6, ariaLabel: '今日营业额' },
          React.createElement(
            'div',
            { className: 'rev-line', style: { justifyContent: 'space-between' } },
            React.createElement(
              'div',
              null,
              React.createElement('div', { className: 'kicker' }, '今日营业额'),
              React.createElement(
                'div',
                { className: 'money', style: { marginTop: 10 } },
                React.createElement('span', { className: 'money__cur' }, '¥'),
                React.createElement('span', { className: 'money__val num' }, fmt(T.revenue))
              )
            ),
            React.createElement(
              'div',
              { style: { textAlign: 'right', paddingTop: 2 } },
              React.createElement('span', { className: 'plate plate--paper' }, '营业中')
            )
          ),
          React.createElement(
            'div',
            { className: 'rev-sub' },
            React.createElement(
              'span',
              { className: 'rev-sub__item' },
              React.createElement(Icon, { name: 'up', size: 12 }),
              '较昨日 +12.4%'
            ),
            React.createElement(
              'span',
              { className: 'rev-sub__item' },
              React.createElement(Icon, { name: 'up', size: 12 }),
              '较上周六 +6.8%'
            )
          ),
          React.createElement(
            'div',
            { className: 'rev-stats' },
            React.createElement(
              'div',
              { className: 'rev-stat' },
              React.createElement('span', { className: 'rev-stat__n num' }, fmt(T.orders)),
              React.createElement('span', { className: 'rev-stat__u' }, '单')
            ),
            React.createElement(
              'div',
              { className: 'rev-stat' },
              React.createElement('span', { className: 'rev-stat__k' }, '客单'),
              React.createElement('span', { className: 'rev-stat__n num' }, '¥' + one(T.avgTicket))
            ),
            React.createElement(
              'div',
              { className: 'rev-stat' },
              React.createElement('span', { className: 'rev-stat__k' }, '出品'),
              React.createElement('span', { className: 'rev-stat__n num' }, fmt(T.output)),
              React.createElement('span', { className: 'rev-stat__u' }, '件')
            )
          )
        ),

        React.createElement(
          Sign,
          { tone: 'yellow', tag: '现场', grow: 2.7, ariaLabel: '待验收 9 项' },
          React.createElement(
            'div',
            { className: 'yield' },
            React.createElement('div', { className: 'kicker' }, '待验收'),
            React.createElement(
              'div',
              { className: 'yield__big' },
              React.createElement('span', { className: 'yield__n num' }, T.pendingAcceptance),
              React.createElement('span', { className: 'yield__u' }, '项')
            ),
            React.createElement(
              'div',
              { className: 'yield__overdue' },
              React.createElement('span', { className: 'plate plate--red' }, '逾期 1 项')
            ),
            React.createElement('div', { className: 'yield__note' }, '按标准图逐项对照')
          )
        ),

        React.createElement(
          Sign,
          { tone: 'green', tag: '生意', grow: 2.7, ariaLabel: '在店桌台 18 / 24' },
          React.createElement(
            'div',
            { className: 'kicker' },
            '在店桌台'
          ),
          React.createElement(
            'div',
            { className: 'tables__big' },
            React.createElement('span', { className: 'tables__n num' }, T.tables),
            React.createElement('span', { style: { fontSize: 26, fontWeight: 900, paddingTop: 6, color: 'var(--paper-dim)' } }, '/' + T.tablesTotal),
            React.createElement('span', { className: 'tables__u' }, '桌')
          ),
          React.createElement(Occupancy, { filled: T.tables, total: T.tablesTotal }),
          React.createElement(
            'div',
            { className: 'tables__rate' },
            '近 30 分钟 ',
            React.createElement('span', { className: 'num', style: { fontSize: 19, fontWeight: 900 } }, T.rate30),
            ' 单/时'
          )
        )
      ),

      /* 第二排：待办 · 人 */
      React.createElement(
        'div',
        { className: 'wall-row', style: { flexGrow: 1.55 } },
        React.createElement(
          Sign,
          { tone: 'ink', tag: '今天', tagTone: 'red', grow: 2.2, ariaLabel: '今天要处理' },
          React.createElement(
            'div',
            { className: 'todo-head' },
            React.createElement('h2', { className: 'todo-title' }, '今天要处理'),
            React.createElement(
              'div',
              { style: { display: 'flex', alignItems: 'center', gap: 8 } },
              React.createElement('span', { className: 'plate plate--red' }, '待批请假 ' + (leave === 'pending' ? 2 : 1)),
              React.createElement('span', { className: 'plate plate--paper' }, (leave === 'pending' ? 3 : 2) + ' 件')
            )
          ),
          React.createElement(
            'div',
            { className: 'todo-list' },
            D.todo.map(function (item, idx) {
              var isLeave = idx === 0;
              return React.createElement(
                'div',
                { className: 'todo-row', key: item.title },
                React.createElement(
                  'div',
                  { className: 'todo-char todo-char--' + item.tone },
                  item.kind
                ),
                React.createElement(
                  'div',
                  { className: 'todo-main' },
                  React.createElement(
                    'div',
                    { style: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' } },
                    React.createElement('div', { className: 'todo-title-line' }, item.title),
                    isLeave && leave === 'pending'
                      ? React.createElement('span', { className: 'plate plate--yellow' }, '待你批')
                      : null,
                    idx === 1
                      ? React.createElement('span', { className: 'plate plate--red' }, '逾期 1')
                      : null,
                    idx === 2
                      ? React.createElement('span', { className: 'plate plate--red' }, '逾期')
                      : null
                  ),
                  React.createElement('div', { className: 'todo-meta' }, item.meta)
                ),
                React.createElement(
                  'div',
                  { className: 'todo-actions' },
                  isLeave && !readOnly && leave === 'pending'
                    ? React.createElement(
                        React.Fragment,
                        null,
                        React.createElement(
                          Stamp,
                          {
                            onClick: function () { setLeave('approved'); },
                            ariaLabel: '批准张伟的请假'
                          },
                          '批准'
                        ),
                        React.createElement(
                          Stamp,
                          {
                            ghost: true,
                            onClick: function () { setLeave('rejected'); },
                            ariaLabel: '驳回张伟的请假'
                          },
                          '驳回'
                        )
                      )
                    : null,
                  isLeave && !readOnly && leave !== 'pending'
                    ? React.createElement(
                        'span',
                        { className: 'plate ' + (leave === 'approved' ? 'plate--green' : 'plate--red') },
                        leave === 'approved' ? '已批准' : '已驳回'
                      )
                    : null,
                  idx === 1 && !readOnly
                    ? React.createElement(
                        Stamp,
                        {
                          icon: 'check',
                          onClick: function () { if (props.go) props.go('daily'); },
                          ariaLabel: '去验收后厨灶台等 9 项'
                        },
                        '去验收'
                      )
                    : null,
                  idx === 2 && !readOnly
                    ? React.createElement(
                        Stamp,
                        { ghost: true, ariaLabel: '查看 B 区整改单' },
                        '查看'
                      )
                    : null
                )
              );
            })
          )
        ),

        React.createElement(
          Sign,
          { tone: 'ink', tag: '人', tagTone: 'yellow', grow: 1, ariaLabel: '今天谁上班' },
          React.createElement(
            'div',
            { className: 'who-head' },
            React.createElement('h2', { className: 'who-title' }, '今天谁上班'),
            React.createElement('span', { className: 'meta', style: { fontSize: 13 } }, '在班 ' + working + ' 人')
          ),
          React.createElement(
            'div',
            { className: 'who-list' },
            ['early', 'mid', 'late'].map(function (k) {
              return React.createElement(
                'div',
                { className: 'who-row', key: k },
                React.createElement('span', { className: 'who-bar who-bar--' + k }),
                React.createElement(
                  'div',
                  null,
                  React.createElement('div', { className: 'who-label' }, S[k].label),
                  React.createElement('div', { className: 'who-time' }, S[k].time)
                ),
                React.createElement('div', { className: 'who-count num' }, S[k].count)
              );
            })
          ),
          React.createElement(
            'div',
            { className: 'who-foot' },
            React.createElement('span', null, '休 3，含请假 1'),
            React.createElement('span', { className: 'plate plate--red' }, '张伟 事假')
          )
        )
      ),

      /* 第三排：备货 · 最慢档口 */
      React.createElement(
        'div',
        { className: 'wall-row', style: { flexGrow: 1.0 } },
        React.createElement(
          Sign,
          { tone: 'paper', tag: '厨房', grow: 1.55, ariaLabel: '备货还差 7 项' },
          React.createElement(
            'div',
            { className: 'prep-row' },
            React.createElement(
              'div',
              { className: 'prep-hero' },
              React.createElement('div', { className: 'kicker' }, '备货还差'),
              React.createElement(
                'div',
                { className: 'prep-big' },
                React.createElement('span', { className: 'prep-n num' }, D.prep.short),
                React.createElement('span', { className: 'prep-u' }, '项')
              ),
              React.createElement(
                'div',
                { className: 'prep-ticks', 'aria-label': '还差 7 项' },
                prepTicks.map(function (tone, i) {
                  return React.createElement('span', {
                    key: 't' + i,
                    className: 'prep-tick prep-tick--' + tone
                  });
                })
              )
            ),
            React.createElement(
              'div',
              { className: 'prep-cells' },
              D.prep.items.map(function (it) {
                return React.createElement(
                  'div',
                  { className: 'prep-cell', key: it.label },
                  React.createElement('span', { className: 'prep-cell__n num' }, it.n),
                  React.createElement('span', { className: 'prep-cell__k' }, it.label)
                );
              })
            ),
            readOnly
              ? null
              : React.createElement(
                  Stamp,
                  { onClick: function () {}, ariaLabel: '去备货' },
                  '去备货'
                )
          )
        ),

        React.createElement(
          Sign,
          { tone: 'ink', tag: '厨房', tagTone: 'yellow', grow: 1, ariaLabel: '档口最慢 烧烤 12.5 分钟' },
          React.createElement('div', { className: 'kicker' }, '档口最慢'),
          React.createElement(
            'div',
            { className: 'slow__row' },
            React.createElement('span', { className: 'slow__name' }, D.slowest.station),
            React.createElement('span', { className: 'slow__n num' }, one(D.slowest.minutes)),
            React.createElement('span', { className: 'slow__u' }, '分钟'),
            React.createElement('span', { className: 'plate plate--red', style: { marginLeft: 'auto' } }, '全店最慢')
          ),
          React.createElement(
            'div',
            { className: 'scale' },
            React.createElement(
              'div',
              { className: 'scale__track' },
              React.createElement('div', {
                className: 'scale__fill',
                style: { width: (D.slowest.minutes / D.slowest.scaleMax) * 100 + '%' }
              })
            ),
            React.createElement(
              'div',
              { className: 'scale__labels' },
              React.createElement('span', null, '0'),
              React.createElement('span', null, '5'),
              React.createElement('span', null, '10'),
              React.createElement('span', null, '15 分')
            )
          ),
          React.createElement(
            'div',
            { className: 'slow__note' },
            React.createElement(Icon, { name: 'alert', size: 15 }),
            '全店最慢，先看这一档'
          )
        )
      )
    );
  }

  /* =========================================================
     2 · 销售报表（手机 390，老板 周志远）
     ========================================================= */

  function SalesPeriodChips() {
    var periods = ['今天', '昨天', '近 7 天', '本月'];
    return React.createElement(
      'div',
      { className: 'chips', role: 'group', 'aria-label': '统计期间' },
      periods.map(function (p) {
        var on = p === '近 7 天';
        return React.createElement(
          'span',
          {
            key: p,
            className: 'chip' + (on ? ' is-on' : ' chip--locked'),
            'aria-disabled': on ? undefined : 'true'
          },
          p
        );
      })
    );
  }

  function SalesPage(props) {
    var D = window.DATA;
    var S = D.sales;

    function exportCsv() {
      var lines = [];
      lines.push('销售报表 近 7 天');
      lines.push('销售金额,' + S.amount);
      lines.push('订单,' + S.orders);
      lines.push('客单价,' + S.avgTicket);
      lines.push('菜品数,' + S.dishes);
      lines.push('退菜行数,' + S.returns);
      lines.push('');
      lines.push('周,销售额');
      S.days.forEach(function (d) {
        lines.push(d.label + ',' + d.value);
      });
      lines.push('');
      lines.push('档口,占比');
      D.stations.forEach(function (s) {
        lines.push(s.name + ',' + s.pct + '%');
      });
      lines.push('');
      lines.push('名次,菜品,档口,数量,占比');
      D.dishes.forEach(function (d) {
        lines.push(d.rank + ',' + d.name + ',' + d.station + ',' + d.count + ',' + d.pct + '%');
      });
      var csv = '\ufeff' + lines.join('\r\n');
      var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = '销售报表-近7天.csv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    return React.createElement(
      PhoneShell,
      {
        title: '销售报表',
        date: '10.25 周日',
        role: props.role,
        roleKey: props.roleKey,
        activeTab: 'business',
        onNav: props.go
      },
      React.createElement(
        'div',
        { className: 'sales-head' },
        React.createElement(
          'div',
          { className: 'sales-spine' },
          React.createElement('span', null, '销售报表')
        ),
        React.createElement(
          'div',
          { className: 'sales-head__main' },
          React.createElement('div', { className: 'sales-eyebrow' }, '云味小馆 中山路店'),
          React.createElement('h1', { className: 'sales-h1' }, '近 7 天'),
          React.createElement(
            'div',
            { className: 'sales-sub' },
            React.createElement('span', null, '统计周期 7 天'),
            React.createElement('span', { className: 'readonly' }, '只读')
          )
        )
      ),

      React.createElement(SalesPeriodChips),

      React.createElement(
        'div',
        { className: 'm-stack', style: { marginTop: 6 } },

        React.createElement(
          Sign,
          { tone: 'red', tag: '生意', className: 'sales-hero', ariaLabel: '销售金额 186,420' },
          React.createElement('div', { className: 'sales-hero__label' }, '销售金额'),
          React.createElement(
            'div',
            { className: 'sales-hero__money' },
            React.createElement('span', { className: 'sales-hero__cur' }, '¥'),
            React.createElement('span', { className: 'sales-hero__val num' }, fmt(S.amount))
          ),
          React.createElement(
            'div',
            { className: 'sales-hero__foot' },
            React.createElement('span', null, '近 7 天合计'),
            React.createElement('span', null, '7 个营业日')
          )
        ),

        React.createElement(
          'div',
          { className: 'm-row', style: { marginTop: -8 } },
          React.createElement(
            Sign,
            { tone: 'paper', className: 'sales-mini', ariaLabel: '订单 6,028' },
            React.createElement('div', { className: 'sales-mini__label' }, '订单'),
            React.createElement('div', { className: 'sales-mini__n num' }, fmt(S.orders)),
            React.createElement(
              'div',
              { className: 'sales-mini__sub' },
              React.createElement('span', null, '退菜 ' + S.returns + ' 行')
            )
          ),
          React.createElement(
            Sign,
            { tone: 'paper', className: 'sales-mini', ariaLabel: '客单价 30.9' },
            React.createElement('div', { className: 'sales-mini__label' }, '客单'),
            React.createElement('div', { className: 'sales-mini__n num' }, '¥' + one(S.avgTicket)),
            React.createElement(
              'div',
              { className: 'sales-mini__sub' },
              React.createElement('span', null, '菜品 ' + S.dishes + ' 道')
            )
          )
        ),

        React.createElement(
          Sign,
          {
            tone: 'ink',
            tag: '生意',
            tagTone: 'yellow',
            className: 'chart-sign',
            ariaLabel: '近 7 天营业额折线'
          },
          React.createElement(
            'div',
            { className: 'chart-head' },
            React.createElement('div', { className: 'chart-title' }, '近 7 天营业额'),
            React.createElement('div', { className: 'chart-range' }, '周一 至 周日')
          ),
          React.createElement(
            'div',
            { className: 'chart-wrap' },
            React.createElement(SalesLine, { days: S.days })
          )
        ),

        React.createElement(
          Sign,
          {
            tone: 'ink',
            tag: '生意',
            tagTone: 'blue',
            className: 'station-sign',
            ariaLabel: '档口收入占比'
          },
          React.createElement(
            'div',
            { className: 'chart-head' },
            React.createElement('div', { className: 'chart-title' }, '档口收入占比'),
            React.createElement('div', { className: 'chart-range' }, '7 天')
          ),
          React.createElement(StationBars, { items: D.stations })
        )
      ),

      React.createElement(
        'div',
        { className: 'dish-head' },
        React.createElement('div', { className: 'dish-head__title' }, '菜品榜 前 5'),
        React.createElement('div', { className: 'dish-head__sub' }, '按销量')
      ),

      React.createElement(
        'div',
        { className: 'dish-list' },
        D.dishes.map(function (d) {
          return React.createElement(
            'div',
            { className: 'dish-row', key: d.rank },
            React.createElement('div', { className: 'dish-rank num' }, d.rank),
            React.createElement('div', { className: 'dish-name' }, d.name),
            React.createElement('div', { className: 'dish-station' }, d.station),
            React.createElement('div', { className: 'dish-count num' }, fmt(d.count)),
            React.createElement('div', { className: 'dish-pct num' }, pct(d.pct))
          );
        })
      ),

      React.createElement(
        'button',
        {
          type: 'button',
          className: 'export',
          onClick: exportCsv,
          'aria-label': '导出 CSV'
        },
        React.createElement(Icon, { name: 'download', size: 18 }),
        '导出 CSV'
      )
    );
  }

  /* =========================================================
     3 · 日常验收（手机 390，厨师长 王国强）
     ========================================================= */

  function DailyPage(props) {
    var D = window.DATA;
    var H = D.hygiene;
    var readOnly = !!props.readOnly;
    var openState = useState(false);
    var open = openState[0];
    var setOpen = openState[1];
    var reasonState = useState('');
    var reason = reasonState[0];
    var setReason = reasonState[1];
    var errState = useState('');
    var err = errState[0];
    var setErr = errState[1];
    var verdictState = useState('idle');
    var verdict = verdictState[0];
    var setVerdict = verdictState[1];

    function closeModal() {
      setOpen(false);
      setErr('');
    }

    function submitReject() {
      if (!reason.trim()) {
        setErr('请写清楚哪里不合格，员工会看到这段文字');
        return;
      }
      setOpen(false);
      setVerdict('rejected');
    }

    useEffect(
      function () {
        if (!open) return undefined;
        var el = document.getElementById('reject-reason');
        if (el) el.focus();
        function onKey(e) {
          if (e.key === 'Escape') closeModal();
        }
        window.addEventListener('keydown', onKey);
        return function () {
          window.removeEventListener('keydown', onKey);
        };
      },
      [open]
    );

    var overlay = open
      ? React.createElement(
          'div',
          {
            className: 'scrim',
            onMouseDown: function (e) {
              if (e.target === e.currentTarget) closeModal();
            }
          },
          React.createElement(
            'div',
            {
              className: 'sheet',
              role: 'dialog',
              'aria-modal': 'true',
              'aria-label': '驳回理由'
            },
            React.createElement(
              'div',
              { className: 'sheet-head' },
              React.createElement(
                'div',
                null,
                React.createElement('div', { className: 'sheet-title' }, '驳回这一项'),
                React.createElement('div', { className: 'sheet-sub' }, H.item.name + ' ' + H.item.detail + '　' + H.item.zone)
              ),
              React.createElement(
                'button',
                {
                  type: 'button',
                  className: 'sheet-close',
                  'aria-label': '关闭',
                  onClick: closeModal
                },
                React.createElement(Icon, { name: 'close', size: 20 })
              )
            ),
            React.createElement(
              'label',
              { className: 'sheet-label', htmlFor: 'reject-reason' },
              '哪里不合格（必填，员工能看到）'
            ),
            React.createElement('textarea', {
              id: 'reject-reason',
              className: 'reason',
              value: reason,
              onChange: function (e) {
                setReason(e.target.value);
                if (err) setErr('');
              }
            }),
            err
              ? React.createElement('div', { className: 'sheet-err' }, err)
              : React.createElement('div', { className: 'sheet-hint' }, '员工会在手机端看到这段文字'),
            React.createElement(
              'div',
              { className: 'sheet-actions' },
              React.createElement(
                'button',
                {
                  type: 'button',
                  className: 'big-btn',
                  style: { background: 'var(--ink)' },
                  onClick: closeModal
                },
                '取消'
              ),
              React.createElement(
                'button',
                {
                  type: 'button',
                  className: 'big-btn big-btn--reject',
                  onClick: submitReject
                },
                '提交驳回'
              )
            )
          )
        )
      : null;

    var actions = readOnly
      ? React.createElement(
          'div',
          { className: 'verdict' },
          React.createElement(Icon, { name: 'alert', size: 18 }),
          React.createElement('span', { className: 'verdict__txt' }, '只读身份，不能验收')
        )
      : verdict === 'idle'
        ? React.createElement(
            'div',
            { className: 'actions' },
            React.createElement(
              'button',
              {
                type: 'button',
                className: 'big-btn big-btn--pass',
                onClick: function () { setVerdict('passed'); }
              },
              React.createElement(Icon, { name: 'check', size: 20 }),
              '通过'
            ),
            React.createElement(
              'button',
              {
                type: 'button',
                className: 'big-btn big-btn--reject',
                onClick: function () { setOpen(true); }
              },
              React.createElement(Icon, { name: 'close', size: 20 }),
              '驳回'
            )
          )
        : React.createElement(
            'div',
            { className: 'verdict verdict--' + (verdict === 'passed' ? 'pass' : 'reject') },
            React.createElement(Icon, { name: verdict === 'passed' ? 'check' : 'close', size: 18 }),
            React.createElement(
              'span',
              { className: 'verdict__txt' },
              verdict === 'passed'
                ? '已通过，下一项 蒸笼区（3/9）'
                : '已驳回，理由已记录，员工能看到'
            )
          );

    return React.createElement(
      PhoneShell,
      {
        title: '日常验收',
        date: '10.25 周日',
        role: props.role,
        roleKey: props.roleKey,
        activeTab: 'floor',
        onNav: props.go,
        overlay: overlay
      },
      React.createElement(
        'div',
        { className: 'daily-body' },

        React.createElement(
          'div',
          { className: 'mini-row', 'aria-label': '今日卫生摘要' },
          React.createElement(
            'div',
            { className: 'mini mini--yellow' },
            React.createElement('div', { className: 'mini__n num' }, H.pending),
            React.createElement('div', { className: 'mini__k' }, '待验收')
          ),
          React.createElement(
            'div',
            { className: 'mini mini--paper' },
            React.createElement('div', { className: 'mini__n num' }, H.toShoot),
            React.createElement('div', { className: 'mini__k' }, '待拍')
          ),
          React.createElement(
            'div',
            { className: 'mini mini--green' },
            React.createElement('div', { className: 'mini__n num' }, H.passed),
            React.createElement('div', { className: 'mini__k' }, '已通过')
          ),
          React.createElement(
            'div',
            { className: 'mini mini--red' },
            React.createElement('div', { className: 'mini__n num' }, H.missed),
            React.createElement('div', { className: 'mini__k' }, '漏验')
          )
        ),

        React.createElement(
          Sign,
          {
            tone: 'ink',
            tag: '现场',
            tagTone: 'red',
            className: 'verify-card',
            ariaLabel: '验收 灶台 油污与残渣'
          },
          React.createElement(
            'div',
            { className: 'verify-head' },
            React.createElement(
              'div',
              null,
              React.createElement('div', { className: 'kicker' }, '检查项'),
              React.createElement('div', { className: 'verify-title' }, H.item.name),
              React.createElement('div', { className: 'verify-sub' }, H.item.detail)
            ),
            React.createElement(
              'div',
              { className: 'verify-head__right' },
              React.createElement('span', { className: 'plate plate--paper' }, H.item.zone),
              React.createElement('span', { className: 'verify-count' }, '第 ' + H.item.index + ' / ' + H.item.total + ' 项')
            )
          ),
          React.createElement(
            'div',
            { className: 'photo-row' },
            React.createElement(
              'div',
              { className: 'photo' },
              React.createElement('div', { className: 'photo-tag' }, '标准图'),
              React.createElement(Stove, { dirty: false })
            ),
            React.createElement(
              'div',
              { className: 'photo' },
              React.createElement(
                'div',
                { className: 'photo-tag' },
                '实拍',
                React.createElement('span', { className: 'photo-tag__sub' }, H.photo.by + ' ' + H.photo.shift + ' ' + H.photo.time)
              ),
              React.createElement(Stove, { dirty: true }),
              React.createElement('span', { className: 'grease-tag' }, '油污残留')
            )
          )
        ),

        React.createElement(
          'div',
          { className: 'queue' },
          React.createElement(
            'div',
            { className: 'queue__tag' },
            React.createElement('span', null, '下一')
          ),
          React.createElement(
            'div',
            { className: 'queue__main' },
            React.createElement('div', { className: 'queue__k' }, '下一项'),
            React.createElement('div', { className: 'queue__t' }, H.item.nextName + '（' + H.item.nextIndex + '/' + H.item.total + '）')
          ),
          React.createElement(Icon, { name: 'chevron', size: 18 })
        ),

        actions
      )
    );
  }

  /* =========================================================
     4 · 排班月历（桌面 1440，店长）
     ========================================================= */

  function CalendarPage(props) {
    var D = window.DATA;
    var C = D.calendar;
    var S = D.shifts;
    var readOnly = !!props.readOnly;
    var viewState = useState('month');
    var view = viewState[0];
    var setView = viewState[1];
    var selState = useState(C.selected);
    var selected = selState[0];
    var setSelected = selState[1];
    var shiftState = useState(null);
    var shift = shiftState[0];
    var setShift = shiftState[1];
    var zoneState = useState('前厅');
    var zone = zoneState[0];
    var setZone = zoneState[1];
    var adjState = useState([]);
    var adj = adjState[0];
    var setAdj = adjState[1];
    var msgState = useState('');
    var msg = msgState[0];
    var setMsg = msgState[1];

    function findDay(date) {
      for (var i = 0; i < C.days.length; i += 1) {
        if (C.days[i].date === date) return C.days[i];
      }
      return null;
    }

    function countsFor(date) {
      var d = findDay(date);
      if (!d) return null;
      var early = d.early;
      var mid = d.mid;
      var late = d.late;
      var offAdj = 0;
      adj.forEach(function (a) {
        if (a.date !== date) return;
        if (a.shift === 'early') early += 1;
        else if (a.shift === 'mid') mid += 1;
        else if (a.shift === 'late') late += 1;
        else offAdj += 1;
      });
      var total = early + mid + late;
      return {
        early: early,
        mid: mid,
        late: late,
        total: total,
        off: Math.max(0, D.hr.roster - total) + offAdj
      };
    }

    function shiftName(k) {
      return S[k] ? S[k].label : '';
    }

    function onCell(day) {
      if (!day) return;
      setSelected(day.date);
      if (readOnly || !shift) {
        setMsg('');
        return;
      }
      var c = countsFor(day.date);
      if (shift !== 'off' && c.total >= 28) {
        setMsg('该日已满 28 人，没有加人');
        return;
      }
      setAdj(adj.concat([{ date: day.date, shift: shift }]));
      setMsg('已加 ' + shortDate(day.date) + ' ' + shiftName(shift) + ' ' + zone);
    }

    function undo() {
      if (!adj.length) return;
      var next = adj.slice(0, adj.length - 1);
      setAdj(next);
      setMsg('已撤销最近一批');
    }

    function staffFor(day, c) {
      if (day.date === C.selected) return C.sideStaff;
      var pool = C.staffPool;
      var start = (day.day * 3) % pool.length;
      var n = Math.min(10, c.total);
      var used = { early: 0, mid: 0, late: 0 };
      var rows = [];
      for (var i = 0; i < n; i += 1) {
        var best = null;
        var bestScore = -1;
        ['early', 'mid', 'late'].forEach(function (k) {
          if (used[k] >= c[k]) return;
          var score = c[k] / (used[k] + 1);
          if (score > bestScore) {
            bestScore = score;
            best = k;
          }
        });
        if (!best) break;
        used[best] += 1;
        rows.push({
          name: pool[(start + i) % pool.length],
          shift: best,
          zone: C.zones[(day.day + i) % C.zones.length]
        });
      }
      return rows;
    }

    function buildMonthCells() {
      var out = [];
      C.ghostsBefore.forEach(function (g) {
        out.push({ ghost: true, key: 'g' + g.date, day: g });
      });
      C.days.forEach(function (d) {
        out.push({ ghost: false, key: d.date, day: d });
      });
      C.ghostsAfter.forEach(function (g) {
        out.push({ ghost: true, key: 'g' + g.date, day: g });
      });
      return out;
    }

    function renderBars(c) {
      var order = ['early', 'mid', 'late', 'off'];
      return React.createElement(
        'div',
        { className: 'cell-bars' },
        order.map(function (k) {
          var n = c[k];
          var w = Math.min(100, (n / 12) * 100);
          return React.createElement(
            'div',
            { className: 'cell-track', key: k },
            React.createElement('div', {
              className: 'cell-bar cell-bar--' + k,
              style: { width: w + '%' }
            })
          );
        })
      );
    }

    function renderMonthCell(item) {
      var day = item.day;
      if (item.ghost) {
        return React.createElement(
          'div',
          { className: 'cell is-ghost', key: item.key, 'aria-hidden': 'true' },
          React.createElement(
            'div',
            { className: 'cell-top' },
            React.createElement('span', { className: 'cell-date' }, day.month + '/' + day.day)
          )
        );
      }
      var c = countsFor(day.date);
      var isSel = day.date === selected;
      var cls = ['cell'];
      if (isSel) cls.push('is-sel');
      if (c.total === 0) cls.push('is-empty');
      return React.createElement(
        'button',
        {
          type: 'button',
          className: cls.join(' '),
          key: item.key,
          onClick: function () { onCell(day); },
          'aria-label':
            day.month + ' 月 ' + day.day + ' 日 ' + day.weekday +
            '，在班 ' + c.total + ' 人' + (day.pending ? '，有待批请假' : '')
        },
        React.createElement(
          'div',
          { className: 'cell-top' },
          React.createElement('span', { className: 'cell-date' }, day.day),
          day.pending ? React.createElement('span', { className: 'cell-pending' }, '待批') : null
        ),
        c.total === 0
          ? React.createElement('div', { className: 'cell-empty-note' }, '未排')
          : React.createElement(
              React.Fragment,
              null,
              renderBars(c),
              React.createElement(
                'div',
                { className: 'cell-count' },
                React.createElement('span', { className: 'cell-count__n num' }, c.total),
                React.createElement('span', null, '人')
              )
            )
      );
    }

    function renderWeekCell(day) {
      var c = countsFor(day.date);
      var isSel = day.date === selected;
      var staff = staffFor(day, c);
      var cls = ['cell', 'is-week'];
      if (isSel) cls.push('is-sel');
      return React.createElement(
        'button',
        {
          type: 'button',
          className: cls.join(' '),
          key: day.date,
          onClick: function () { onCell(day); },
          'aria-label': day.month + ' 月 ' + day.day + ' 日 ' + day.weekday + '，在班 ' + c.total + ' 人'
        },
        React.createElement(
          'div',
          { className: 'week-day' },
          React.createElement('span', { className: 'cell-date' }, day.day),
          React.createElement('span', { className: 'week-day__w' }, day.weekday)
        ),
        c.total === 0
          ? React.createElement('div', { className: 'cell-empty-note' }, '未排')
          : React.createElement(
              React.Fragment,
              null,
              React.createElement(
                'div',
                { className: 'week-shifts' },
                ['early', 'mid', 'late'].map(function (k) {
                  return React.createElement(
                    'div',
                    { className: 'week-shift', key: k },
                    React.createElement('span', {
                      className: 'week-dot',
                      style: { background: 'var(--' + S[k].tone + ')' }
                    }),
                    React.createElement('span', { className: 'week-shift__k' }, S[k].short + '班'),
                    React.createElement('span', { className: 'week-shift__n num' }, c[k] + ' 人')
                  );
                })
              ),
              React.createElement(
                'div',
                { className: 'week-names' },
                (function () {
                  var groups = { early: [], mid: [], late: [] };
                  staff.forEach(function (s) { groups[s.shift].push(s); });
                  var preview = [];
                  if (groups.early[0]) preview.push(groups.early[0]);
                  if (groups.early[1]) preview.push(groups.early[1]);
                  if (groups.mid[0]) preview.push(groups.mid[0]);
                  if (groups.late[0]) preview.push(groups.late[0]);
                  return preview.map(function (s, i) {
                    return React.createElement(
                      'span',
                      { className: 'week-names__row', key: s.name + i },
                      React.createElement('span', { className: 'week-names__n' }, s.name),
                      React.createElement('span', null, S[s.shift].short)
                    );
                  });
                })()
              )
            ),
        day.pending ? React.createElement('span', { className: 'cell-pending' }, '待批') : null,
        React.createElement(
          'div',
          { className: 'cell-count' },
          React.createElement('span', { className: 'cell-count__n num' }, c.total),
          React.createElement('span', null, '人')
        )
      );
    }

    var monthCells = buildMonthCells();
    var selDay = findDay(selected);
    var selCounts = selDay ? countsFor(selected) : null;
    var selStaff = selDay && selCounts ? staffFor(selDay, selCounts) : [];
    var weekDays = C.weekDates.map(findDay).filter(Boolean);

    var sideContent = null;
    if (selDay) {
      sideContent = React.createElement(
        React.Fragment,
        null,
        React.createElement(
          'div',
          { className: 'side-head' },
          React.createElement('span', { className: 'side-date' }, shortDate(selected)),
          React.createElement('span', { className: 'side-week' }, selDay.weekday),
          selected === C.selected
            ? React.createElement('span', { className: 'side-today' }, '今天')
            : null
        ),
        React.createElement(
          'div',
          { className: 'side-sum' },
          '在班 ' + selCounts.total + '　休 ' + selCounts.off + (selected === C.selected ? '　含请假 1' : '')
        ),
        React.createElement(
          'div',
          { className: 'side-list' },
          selCounts.total === 0
            ? React.createElement('div', { className: 'side-pending__d', style: { padding: '10px 0' } }, '这一天还没排班')
            : selStaff.map(function (s, i) {
                return React.createElement(
                  'div',
                  { className: 'side-row', key: s.name + i },
                  React.createElement(
                    'div',
                    { style: { minWidth: 0 } },
                    React.createElement('div', { className: 'side-name' }, s.name),
                    React.createElement(
                      'div',
                      { className: 'side-detail' },
                      C.shiftTime[s.shift] + '　' + s.zone
                    )
                  ),
                  React.createElement(
                    'span',
                    { className: 'side-shift side-shift--' + s.shift },
                    S[s.shift].short
                  )
                );
              })
        ),
        selCounts.total > selStaff.length
          ? React.createElement(
              'div',
              { className: 'side-more' },
              '另有 ' + (selCounts.total - selStaff.length) + ' 人在班'
            )
          : null,
        React.createElement(
          'div',
          { className: 'side-section' },
          React.createElement('div', { className: 'side-section__t' }, '待批请假'),
          React.createElement('span', { className: 'plate plate--red' }, '2 条')
        ),
        C.pendingLeaves.map(function (p) {
          return React.createElement(
            'div',
            { className: 'side-pending', key: p.name + p.date },
            React.createElement(
              'div',
              { className: 'side-pending__main' },
              React.createElement('div', { className: 'side-pending__n' }, p.name + ' ' + p.type),
              React.createElement(
                'div',
                { className: 'side-pending__d' },
                p.date + '　' + p.days + ' 天'
              )
            ),
            React.createElement('span', { className: 'side-pending__tag' }, '待批')
          );
        }),
        readOnly
          ? React.createElement(
              'div',
              { className: 'side-rule' },
              React.createElement('span', null, '固定班规则')
            )
          : React.createElement(
              'button',
              {
                type: 'button',
                className: 'side-rule',
                onClick: function () { setMsg('固定班规则页不在本原型'); }
              },
              React.createElement('span', null, '固定班规则'),
              React.createElement(Icon, { name: 'chevron', size: 16 })
            )
      );
    }

    return React.createElement(
      'div',
      { className: 'cal-page' },

      React.createElement(
        'div',
        { className: 'cal-head' },
        React.createElement(
          'div',
          { className: 'cal-title-wrap' },
          React.createElement('div', { className: 'cal-kicker' }, '排班'),
          React.createElement('h1', { className: 'cal-h1' }, C.monthLabel)
        ),
        React.createElement(
          'div',
          { className: 'cal-tools' },
          React.createElement('span', { className: 'meta', style: { fontSize: 13 } }, '花名册 28 人'),
          React.createElement(
            'div',
            { className: 'seg', role: 'group', 'aria-label': '视图切换' },
            React.createElement(
              'button',
              {
                type: 'button',
                className: 'seg-btn' + (view === 'month' ? ' is-on' : ''),
                onClick: function () { setView('month'); },
                'aria-pressed': view === 'month'
              },
              '月'
            ),
            React.createElement(
              'button',
              {
                type: 'button',
                className: 'seg-btn' + (view === 'week' ? ' is-on' : ''),
                onClick: function () { setView('week'); },
                'aria-pressed': view === 'week'
              },
              '周'
            )
          )
        )
      ),

      React.createElement(
        'div',
        { className: 'cal-body' },
        React.createElement(
          'section',
          { className: 'cal-panel', 'aria-label': '排班月历' },
          React.createElement(
            'div',
            { className: 'legend' },
            React.createElement(
              'div',
              { className: 'legend-item' },
              React.createElement('span', { className: 'legend-swatch legend-swatch--early' }),
              '早班'
            ),
            React.createElement(
              'div',
              { className: 'legend-item' },
              React.createElement('span', { className: 'legend-swatch legend-swatch--mid' }),
              '中班'
            ),
            React.createElement(
              'div',
              { className: 'legend-item' },
              React.createElement('span', { className: 'legend-swatch legend-swatch--late' }),
              '晚班'
            ),
            React.createElement(
              'div',
              { className: 'legend-item' },
              React.createElement('span', { className: 'legend-swatch legend-swatch--off' }),
              '休 含假'
            ),
            React.createElement(
              'div',
              { className: 'legend-item' },
              React.createElement('span', { className: 'legend-swatch legend-swatch--pending' }),
              '待批'
            ),
            React.createElement(
              'div',
              { className: 'legend-item' },
              React.createElement('span', { className: 'legend-swatch legend-swatch--empty' }),
              '未排'
            )
          ),
          view === 'week'
            ? React.createElement(
                'div',
                { className: 'cal-weekdays is-week' },
                React.createElement('span', { className: 'week-head__k' }, '本周'),
                React.createElement('span', { className: 'week-head__d' }, '10/20–10/26'),
                React.createElement('span', { className: 'week-head__k' }, '周一 至 周日')
              )
            : React.createElement(
                'div',
                { className: 'cal-weekdays' },
                C.weekdays.map(function (w) {
                  return React.createElement('div', { className: 'cal-weekday', key: w }, w);
                })
              ),
          React.createElement(
            'div',
            { className: 'cal-grid' + (view === 'week' ? ' is-week' : '') },
            view === 'week'
              ? weekDays.map(renderWeekCell)
              : monthCells.map(renderMonthCell)
          )
        ),

        React.createElement(
          'aside',
          { className: 'cal-side', 'aria-label': '当天名单' },
          sideContent
        )
      ),

      readOnly
        ? null
        : React.createElement(
        'div',
        { className: 'cal-pen', 'aria-label': '排班笔栏' },
        React.createElement(
          'div',
          { className: 'pen-tag' },
          React.createElement('span', null, '排班')
        ),
        React.createElement(
          'div',
          { className: 'pen-group' },
          React.createElement('span', { className: 'pen-label' }, '班次'),
          ['early', 'mid', 'late', 'off'].map(function (k) {
            return React.createElement(
              'button',
              {
                type: 'button',
                key: k,
                className: 'pen-shift pen-shift--' + k + (shift === k ? ' is-on' : ''),
                onClick: function () { setShift(shift === k ? null : k); },
                'aria-pressed': shift === k
              },
              S[k].short
            );
          })
        ),
        React.createElement(
          'div',
          { className: 'pen-group' },
          React.createElement('span', { className: 'pen-label' }, '工作区'),
          ['前厅', '后厨', '保洁', '收银'].map(function (z) {
            return React.createElement(
              'button',
              {
                type: 'button',
                key: z,
                className: 'pen-zone' + (zone === z ? ' is-on' : ''),
                onClick: function () { setZone(z); },
                'aria-pressed': zone === z
              },
              z
            );
          })
        ),
        React.createElement(
          'div',
          { className: 'pen-hint' },
          shift ? '点日期格子加一位' : '选班次，点日期加人'
        ),
        msg ? React.createElement('div', { className: 'pen-msg' }, msg) : null,
        React.createElement(
          'button',
          {
            type: 'button',
            className: 'pen-undo',
            onClick: undo,
            disabled: adj.length === 0,
            'aria-label': '撤销最近一批'
          },
          React.createElement(Icon, { name: 'undo', size: 16 }),
          '撤销最近一批'
        )
      )
    );
  }

  Object.assign(window, {
    TodayPage: TodayPage,
    SalesPage: SalesPage,
    DailyPage: DailyPage,
    CalendarPage: CalendarPage
  });
})();
