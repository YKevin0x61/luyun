/* 厨务管家 · 方向三 · 招牌墙
   mock 数据：数字逐字按 spec 第 6 节。
   日历只为 10/25 这一天给了完整文案；其余日期沿用同一套班次人数与花名册，
   让 7×5 网格不至于空洞，属于设计稿内必要的排班铺底。 */

(function () {
  var store = {
    name: '云味小馆',
    branch: '中山路店',
    full: '云味小馆 · 中山路店',
    date: '2026-10-25',
    dateLabel: '10 月 25 日',
    weekday: '周日',
    businessDay: '2026-10-25',
    businessDayLabel: '10 月 25 日 周日'
  };

  var roles = {
    manager: { key: 'manager', label: '店长', name: '陈静' },
    boss: { key: 'boss', label: '老板', name: '周志远' },
    chef: { key: 'chef', label: '厨师长', name: '王国强' },
    staff: { key: 'staff', label: '员工', name: '李小雨' }
  };

  var today = {
    revenue: 28640,
    revenueYesterday: 25480,
    revenueLastSat: 26820,
    orders: 412,
    avgTicket: 69.5,
    output: 1286,
    tables: 18,
    tablesTotal: 24,
    rate30: 76,
    overdueAcceptance: 1,
    pendingAcceptance: 9,
    pendingLeave: 2,
    rectificationOverdue: 1,
    hrReminders: 3,
    prepShort: 7
  };

  var shifts = {
    early: { key: 'early', label: '早班', short: '早', time: '09:00–15:00', count: 8, tone: 'yellow' },
    mid: { key: 'mid', label: '中班', short: '中', time: '12:00–20:00', count: 6, tone: 'green' },
    late: { key: 'late', label: '晚班', short: '晚', time: '16:00–22:30', count: 11, tone: 'blue' },
    off: { key: 'off', label: '休', short: '休', count: 3 },
    leave: { key: 'leave', label: '请假', short: '假', count: 1, who: '张伟' }
  };

  var todo = [
    {
      kind: '人',
      tone: 'yellow',
      title: '张伟 请假',
      meta: '事假 10/26–10/27，共 2 天',
      note: '待你批',
      action: '批准',
      alt: '驳回'
    },
    {
      kind: '卫',
      tone: 'paper',
      title: '后厨 灶台 等 9 项待验收',
      meta: '其中逾期 1 项',
      note: '待验收',
      action: '去验收',
      page: 'daily'
    },
    {
      kind: '整',
      tone: 'red',
      title: '前厅 B 区 垃圾桶未加盖',
      meta: '整改单 已超时 1 小时 20 分',
      note: '整改',
      action: '查看'
    }
  ];

  var prep = {
    short: 7,
    items: [
      { label: '热菜', n: 3, tone: 'red' },
      { label: '蒸笼', n: 2, tone: 'yellow' },
      { label: '凉菜', n: 2, tone: 'green' }
    ]
  };

  var slowest = {
    station: '烧烤',
    minutes: 12.5,
    scaleMax: 15
  };

  var sales = {
    amount: 186420,
    orders: 6028,
    avgTicket: 30.9,
    dishes: 86,
    returns: 14,
    rangeLabel: '10/19–10/25',
    days: [
      { label: '周一', value: 22180, date: '10/19' },
      { label: '周二', value: 24060, date: '10/20' },
      { label: '周三', value: 25340, date: '10/21' },
      { label: '周四', value: 26120, date: '10/22' },
      { label: '周五', value: 29480, date: '10/23' },
      { label: '周六', value: 31240, date: '10/24' },
      { label: '周日', value: 28000, date: '10/25' }
    ]
  };

  var stations = [
    { name: '热菜', pct: 42.6, tone: 'red' },
    { name: '蒸笼', pct: 18.3, tone: 'yellow' },
    { name: '烧烤', pct: 15.1, tone: 'green' },
    { name: '凉菜', pct: 9.7, tone: 'blue' },
    { name: '面点', pct: 8.2, tone: 'paper' },
    { name: '吧台', pct: 6.1, tone: 'paper-dim' }
  ];

  var dishes = [
    { rank: 1, name: '招牌酸菜鱼', station: '热菜', count: 682, pct: 11.3 },
    { rank: 2, name: '藤椒鸡', station: '热菜', count: 514, pct: 8.5 },
    { rank: 3, name: '干锅花菜', station: '蒸笼', count: 471, pct: 7.8 },
    { rank: 4, name: '红糖糍粑', station: '面点', count: 398, pct: 6.6 },
    { rank: 5, name: '手打鱼丸汤', station: '蒸笼', count: 342, pct: 5.7 }
  ];

  var hygiene = {
    passRate: 92,
    judged: 1864,
    pending: 9,
    overdue: 1,
    toShoot: 4,
    passed: 118,
    missed: 2,
    item: {
      name: '灶台',
      detail: '油污与残渣',
      zone: '后厨',
      index: 2,
      total: 9,
      nextName: '蒸笼区',
      nextIndex: 3
    },
    photo: { by: '王芳', shift: '白班', time: '10:24' }
  };

  var hr = {
    roster: 28,
    certificates: 3,
    birthdays: 1,
    seniority: 2,
    overtime: 3,
    overtimeHours: 5.5
  };

  var zones = ['前厅 A 区', '前厅 B 区', '后厨', '收银', '保洁', '全店'];

  var sideStaff = [
    { name: '王芳', shift: 'early', zone: '后厨' },
    { name: '李小雨', shift: 'early', zone: '前厅 A 区' },
    { name: '陈静', shift: 'early', zone: '全店' },
    { name: '王国强', shift: 'mid', zone: '后厨' },
    { name: '刘洋', shift: 'mid', zone: '后厨' },
    { name: '赵敏', shift: 'mid', zone: '前厅 B 区' },
    { name: '孙强', shift: 'late', zone: '后厨' },
    { name: '周婷', shift: 'late', zone: '前厅 A 区' },
    { name: '吴昊', shift: 'late', zone: '收银' },
    { name: '郑凯', shift: 'late', zone: '保洁' }
  ];

  var staffPool = [
    '王芳', '李小雨', '陈静', '王国强', '刘洋', '赵敏', '孙强',
    '周婷', '吴昊', '郑凯', '高翔', '马倩', '许峰', '何静'
  ];

  var pendingLeaves = [
    { name: '张伟', type: '事假', date: '10/26–10/27', days: 2 },
    { name: '高翔', type: '事假', date: '10/27', days: 1 }
  ];

  /* 10/1 按「周四」起排，于是 10/25 落在周日——与真实 2026 年 10 月日历一致。 */
  var weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  var patterns = [
    { early: 7, mid: 5, late: 9 },
    { early: 7, mid: 6, late: 9 },
    { early: 8, mid: 5, late: 10 },
    { early: 8, mid: 6, late: 10 },
    { early: 9, mid: 7, late: 11 },
    { early: 8, mid: 6, late: 11 },
    { early: 7, mid: 6, late: 10 }
  ];

  var days = [];
  for (var d = 1; d <= 31; d += 1) {
    var wi = (3 + (d - 1)) % 7;
    var counts = patterns[wi];
    if (d === 25) counts = { early: 8, mid: 6, late: 11 };
    if (d === 31) counts = { early: 0, mid: 0, late: 0 };
    var total = counts.early + counts.mid + counts.late;
    days.push({
      day: d,
      date: '2026-10-' + (d < 10 ? '0' + d : '' + d),
      month: 10,
      weekday: weekdays[wi],
      weekIndex: wi,
      early: counts.early,
      mid: counts.mid,
      late: counts.late,
      total: total,
      off: Math.max(0, hr.roster - total),
      pending: d === 26 || d === 27,
      unplanned: d === 31,
      isToday: d === 25
    });
  }

  var ghosts = [
    { day: 29, month: 9, date: '2026-09-29', weekday: '周一' },
    { day: 30, month: 9, date: '2026-09-30', weekday: '周二' }
  ];

  var calendar = {
    year: 2026,
    month: 10,
    monthLabel: '2026 年 10 月',
    selected: '2026-10-25',
    weekdays: weekdays,
    days: days,
    ghostsBefore: ghosts,
    ghostsAfter: [
      { day: 1, month: 11, date: '2026-11-01', weekday: '周日' },
      { day: 2, month: 11, date: '2026-11-02', weekday: '周日' }
    ],
    weekDates: ['2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-24', '2026-10-25', '2026-10-26'],
    selectedDay: days[24],
    sideStaff: sideStaff,
    staffPool: staffPool,
    zones: zones,
    pendingLeaves: pendingLeaves,
    shiftTime: {
      early: '09:00–15:00',
      mid: '12:00–20:00',
      late: '16:00–22:30',
      off: '—'
    }
  };

  window.DATA = {
    store: store,
    roles: roles,
    today: today,
    shifts: shifts,
    todo: todo,
    prep: prep,
    slowest: slowest,
    sales: sales,
    stations: stations,
    dishes: dishes,
    hygiene: hygiene,
    hr: hr,
    calendar: calendar
  };
})();
