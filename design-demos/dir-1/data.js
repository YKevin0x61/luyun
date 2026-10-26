/* 方向一 · 仪表台 —— mock 数据
   数字逐字来自 spec 第 6 节；日历里 10.25 之外的每日排班量是原型补的排期（spec 未给），
   页面只标了日期与人数，没有编具体人名。 */

window.D1 = (function () {
  var shop = {
    name: '云味小馆 · 中山路店',
    shortName: '云味小馆',
    day: '2026-10-25',
    dayLabel: '10.25 周日',
    dayLong: '10 月 25 日 · 周日',
    month: '2026 年 10 月',
    openFrom: '06:00'
  };

  var identity = {
    boss:    { key: 'boss',    role: '老板',   name: '周志远', label: '老板 · 周志远' },
    manager: { key: 'manager', role: '店长',   name: '陈静',   label: '店长 · 陈静' },
    chef:    { key: 'chef',    role: '厨师长', name: '王国强', label: '厨师长 · 王国强' },
    staff:   { key: 'staff',   role: '员工',   name: '李小雨', label: '员工 · 李小雨' }
  };

  var today = {
    revenue: '¥28,640',
    revenueNum: 28640,
    yesterday: '¥25,480',
    lastSat: '¥26,820',
    deltaDay: '+12.4%',
    deltaWeek: '+6.8%',
    orders: 412,
    avgTicket: '¥69.5',
    output: '1,286',
    tablesOn: 18,
    tablesTotal: 24,
    rateNow: 76,
    slowest: { station: '烧烤', minutes: '12.5' }
  };

  var counts = {
    verify: 9,
    verifyOverdue: 1,
    leavePending: 2,
    rectifyOverdue: 1,
    hrReminders: 3,
    prepMissing: 7
  };

  /* 7 天曲线：营业额是 spec 值；订单量由营业额 ÷ 客单 ¥30.9 得出 */
  var days7 = [
    { key: 'mon', label: '周一', value: 22180, orders: 718 },
    { key: 'tue', label: '周二', value: 24060, orders: 779 },
    { key: 'wed', label: '周三', value: 25340, orders: 820 },
    { key: 'thu', label: '周四', value: 26120, orders: 845 },
    { key: 'fri', label: '周五', value: 29480, orders: 954 },
    { key: 'sat', label: '周六', value: 31240, orders: 1011 },
    { key: 'sun', label: '周日', value: 28000, orders: 906 }
  ];

  var todos = [
    {
      id: 'leave',
      tag: '请假',
      title: '张伟 · 事假',
      meta: '10/26–10/27',
      sub: '服务员 · 前厅 · A 区',
      badge: '待你批',
      actions: ['批准', '驳回'],
      tone: 'normal'
    },
    {
      id: 'hygiene',
      tag: '卫生验收',
      title: '后厨 · 灶台 等 9 项待验收',
      meta: '最近提交 10:24',
      sub: '王芳',
      badge: '逾期 1',
      actions: ['去验收'],
      tone: 'overdue'
    },
    {
      id: 'rectify',
      tag: '整改单',
      title: '前厅 · B 区 垃圾桶未加盖',
      meta: '距截止 1 小时 20 分',
      badge: '今日',
      actions: ['查看'],
      tone: 'due'
    }
  ];

  var shifts = [
    { key: 'early', name: '早班', time: '09:00–15:00', start: 9, end: 15, count: 8, token: '--shift-early' },
    { key: 'mid',   name: '中班', time: '12:00–20:00', start: 12, end: 20, count: 6, token: '--shift-mid' },
    { key: 'late',  name: '晚班', time: '16:00–22:30', start: 16, end: 22.5, count: 11, token: '--shift-late' }
  ];

  var offDuty = { rest: 3, leave: { count: 1, name: '张伟', type: '事假', range: '10/26–10/27' } };

  var prep = {
    missing: 7,
    groups: [
      { name: '热菜', count: 3 },
      { name: '蒸笼', count: 2 },
      { name: '凉菜', count: 2 }
    ]
  };

  var hygiene = {
    passRate: '92%',
    judged: '1,864',
    verify: 9,
    toShoot: 4,
    passedToday: 118,
    missed: 2
  };

  var hr = {
    certExpiring: 3,
    birthday: 1,
    seniority: 2,
    overtime: { count: 3, hours: '5.5 小时' }
  };

  var sales = {
    period: '7d',
    periods: [
      { key: 'today', label: '今天' },
      { key: 'yesterday', label: '昨天' },
      { key: '7d', label: '近 7 天' },
      { key: 'month', label: '本月' }
    ],
    summary: [
      { label: '销售金额', value: '¥186,420', note: '7 天合计' },
      { label: '订单', value: '6,028', note: '7 天合计' },
      { label: '客单价', value: '¥30.9', note: '按订单计' },
      { label: '菜品数', value: '86', note: '有销量的菜' },
      { label: '退菜', value: '14 行', note: '作废与退单明细' }
    ],
    days: [
      { label: '周一', value: 22180 },
      { label: '周二', value: 24060 },
      { label: '周三', value: 25340 },
      { label: '周四', value: 26120 },
      { label: '周五', value: 29480 },
      { label: '周六', value: 31240 },
      { label: '周日', value: 28000 }
    ],
    topDay: { label: '周六', value: '¥31,240' },
    stations: [
      { name: '热菜', pct: 42.6 },
      { name: '蒸笼', pct: 18.3 },
      { name: '烧烤', pct: 15.1 },
      { name: '凉菜', pct: 9.7 },
      { name: '面点', pct: 8.2 },
      { name: '吧台', pct: 6.1 }
    ],
    top: [
      { rank: 1, name: '招牌酸菜鱼', station: '热菜', qty: '682 份', pct: '11.3%' },
      { rank: 2, name: '藤椒鸡', station: '凉菜', qty: '514 份', pct: '8.5%' },
      { rank: 3, name: '干锅花菜', station: '热菜', qty: '471 份', pct: '7.8%' },
      { rank: 4, name: '红糖糍粑', station: '面点', qty: '398 份', pct: '6.6%' },
      { rank: 5, name: '手打鱼丸汤', station: '蒸笼', qty: '342 份', pct: '5.7%' }
    ]
  };

  var daily = {
    title: '日常验收',
    summary: [
      { label: '待验收', value: 9, tone: 'signal' },
      { label: '待拍', value: 4, tone: 'ink' },
      { label: '已通过', value: 118, tone: 'ink' },
      { label: '漏验', value: 2, tone: 'ink' }
    ],
    item: {
      check: '灶台 · 油污与残渣',
      zone: '后厨',
      shotBy: '王芳',
      shiftName: '白班',
      shotAt: '10:24',
      queueNext: '蒸笼区',
      position: '3/9'
    },
    rejectHint: '哪里不合格（必填，员工能看到）'
  };

  /* ---------------- 排班月历 ---------------- */

  /* 列 0=周一 … 6=周日。10/25 是周日，所以 10/1 落在周四列。 */
  var monthDays = (function () {
    var out = [];
    out.push({ d: 28, out: true }, { d: 29, out: true }, { d: 30, out: true });
    for (var d = 1; d <= 31; d++) out.push({ d: d, out: false });
    out.push({ d: 1, out: true });
    return out;
  })();

  function planFor(d, col) {
    if (d === 25) return [8, 6, 11, 3, 1];
    var busy = col >= 4 ? 1 : 0;
    var e = 6 + ((d * 3) % 3) + busy;
    var m = 5 + ((d * 5) % 3);
    var l = 8 + ((d * 7) % 4) + busy;
    var r = 2 + ((d * 2) % 3);
    var leave = d === 26 || d === 27 ? 1 : (d % 9 === 0 ? 1 : 0);
    return [e, m, l, r, leave];
  }

  var zoneSplit = [
    { zone: '前厅', share: .45, token: 'front' },
    { zone: '后厨', share: .38, token: 'kitchen' },
    { zone: '吧台', share: .17, token: 'bar' }
  ];

  var calendar = {
    month: '2026 年 10 月',
    weekLabels: ['一', '二', '三', '四', '五', '六', '日'],
    days: monthDays.map(function (item, i) {
      var col = i % 7;
      if (item.out) return { d: item.d, out: true, col: col };
      var plan = planFor(item.d, col);
      var bars = [];
      var shiftDefs = [
        { key: 'early', n: plan[0] },
        { key: 'mid', n: plan[1] },
        { key: 'late', n: plan[2] }
      ];
      shiftDefs.forEach(function (s) {
        zoneSplit.forEach(function (z) {
          var n = Math.max(1, Math.round(s.n * z.share));
          bars.push({ shift: s.key, zone: z.zone, n: n, w: n });
        });
      });
      return {
        d: item.d,
        out: false,
        col: col,
        plan: plan,
        bars: bars,
        rest: plan[3],
        leave: plan[4],
        head: plan[0] + plan[1] + plan[2] + plan[3] + plan[4],
        pending: item.d === 26 || item.d === 27,
        selected: item.d === 25
      };
    }),
    roster: [
      { name: '李小雨', shift: '早班', time: '09:00–15:00', zone: '前厅 · A 区', token: '--shift-early' },
      { name: '王芳',   shift: '早班', time: '09:00–15:00', zone: '后厨 · 蒸笼', token: '--shift-early' },
      { name: '刘海',   shift: '早班', time: '09:00–15:00', zone: '前厅 · B 区', token: '--shift-early' },
      { name: '赵敏',   shift: '早班', time: '09:00–15:00', zone: '吧台', token: '--shift-early' },
      { name: '陈静',   shift: '中班', time: '12:00–20:00', zone: '全店', token: '--shift-mid' },
      { name: '孙磊',   shift: '中班', time: '12:00–20:00', zone: '后厨 · 灶台', token: '--shift-mid' },
      { name: '吴倩',   shift: '中班', time: '12:00–20:00', zone: '前厅 · A 区', token: '--shift-mid' },
      { name: '王国强', shift: '晚班', time: '16:00–22:30', zone: '后厨 · 灶台', token: '--shift-late' },
      { name: '郑凯',   shift: '晚班', time: '16:00–22:30', zone: '前厅 · B 区', token: '--shift-late' },
      { name: '张伟',   shift: '请假', time: '10/26–10/27', zone: '事假 · 待批', token: '--signal' }
    ],
    pendingLeaves: [
      { id: 'p1', name: '张伟', type: '事假', range: '10/26–10/27', zone: '前厅 · A 区' },
      { id: 'p2', name: '赵敏', type: '病假', range: '10/26', zone: '吧台' }
    ],
    fixedShift: {
      count: 18,
      rules: [
        { name: '前厅 · A 区', detail: '周一至周五 早班 2 人' },
        { name: '后厨 · 灶台', detail: '周三至周日 晚班 2 人' }
      ]
    },
    zones: ['前厅 · A 区', '前厅 · B 区', '后厨 · 灶台', '后厨 · 蒸笼', '吧台', '保洁']
  };

  return {
    shop: shop,
    identity: identity,
    today: today,
    counts: counts,
    days7: days7,
    todos: todos,
    shifts: shifts,
    offDuty: offDuty,
    prep: prep,
    hygiene: hygiene,
    hr: hr,
    sales: sales,
    daily: daily,
    calendar: calendar
  };
})();
