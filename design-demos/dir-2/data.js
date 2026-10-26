/* dir-2 作业流 —— mock 数据（数字逐字取 spec 第 6 节，不另编） */

window.DATA = {
  store: {
    name: '云味小馆',
    branch: '中山路店',
    date: '2026-10-25',
    dateLabel: '10 月 25 日',
    weekday: '周日',
    openFrom: '06:00'
  },

  /* 今天（P1 / spec §6「今天」） */
  today: {
    revenue: 28640,
    orders: 412,
    avg: 69.5,
    items: 1286,
    tablesUsed: 18,
    tablesTotal: 24,
    rate30: 76,
    slowest: '烧烤',
    slowestMin: 12.5,
    yest: 25480,
    lastSat: 26820,
    deltaYest: '+12.4%',
    deltaLastSat: '+6.8%'
  },

  todo: {
    hygiene: 9,
    hygieneOverdue: 1,
    leave: 2,
    rectify: 1,
    hr: 3,
    prep: 7
  },

  /* 排班（28 人） */
  shifts: [
    { key: 'am', label: '早班', count: 8, time: '09:00–15:00' },
    { key: 'mid', label: '中班', count: 6, time: '12:00–20:00' },
    { key: 'pm', label: '晚班', count: 11, time: '16:00–22:30' }
  ],
  offCount: 3,
  leaveToday: { name: '张伟', type: '事假', from: '10/26', to: '10/27', days: 2 },

  prep: {
    total: 7,
    parts: [
      { label: '热菜', n: 3 },
      { label: '蒸笼', n: 2 },
      { label: '凉菜', n: 2 }
    ]
  },

  rectify: {
    zone: '前厅 B 区',
    item: '垃圾桶未加盖',
    left: '1 小时 20 分',
    count: 1
  },

  /* 日期带：10/19–10/25（10/25 是周日，故 10/19 是周一） */
  week: [
    { d: '19', w: '周日' }, { d: '20', w: '周一' }, { d: '21', w: '周二' },
    { d: '22', w: '周三' }, { d: '23', w: '周四' }, { d: '24', w: '周五' },
    { d: '25', w: '周日' }
  ],

  /* 销售（近 7 天） */
  sales: {
    amount: 186420,
    orders: 6028,
    avg: 30.9,
    dishes: 86,
    refund: 14,
    days: [
      { w: '周一', v: 22180 }, { w: '周二', v: 24060 }, { w: '周三', v: 25340 },
      { w: '周四', v: 26120 }, { w: '周五', v: 29480 }, { w: '周六', v: 31240 },
      { w: '周日', v: 28000 }
    ],
    stations: [
      { name: '热菜', pct: 42.6 }, { name: '蒸笼', pct: 18.3 }, { name: '烧烤', pct: 15.1 },
      { name: '凉菜', pct: 9.7 }, { name: '面点', pct: 8.2 }, { name: '吧台', pct: 6.1 }
    ],
    top: [
      { rank: 1, name: '招牌酸菜鱼', station: '热菜', qty: 682, pct: '11.3%' },
      { rank: 2, name: '藤椒鸡', station: '凉菜', qty: 514, pct: '8.5%' },
      { rank: 3, name: '干锅花菜', station: '热菜', qty: 471, pct: '7.8%' },
      { rank: 4, name: '红糖糍粑', station: '面点', qty: 398, pct: '6.6%' },
      { rank: 5, name: '手打鱼丸汤', station: '蒸笼', qty: 342, pct: '5.7%' }
    ]
  },

  /* 卫生 */
  hygiene: {
    passRate: 92,
    judged: 1864,
    pending: 9,
    toShoot: 4,
    passed: 118,
    missed: 2,
    current: {
      zone: '后厨',
      area: '灶台',
      issue: '油污与残渣',
      by: '王芳',
      shift: '早班',
      at: '10:24'
    },
    next: { zone: '蒸笼区', index: 3, total: 9 }
  },

  /* 10/25 当天名单 */
  roster: [
    { name: '陈静', role: '店长', shift: '早班', time: '09:00–15:00', zone: '全店' },
    { name: '王芳', role: '后厨', shift: '早班', time: '09:00–15:00', zone: '后厨 灶台' },
    { name: '李小雨', role: '服务员', shift: '早班', time: '09:00–15:00', zone: '前厅 A 区' },
    { name: '周敏', role: '服务员', shift: '早班', time: '09:00–15:00', zone: '前厅 B 区' },
    { name: '赵磊', role: '后厨', shift: '早班', time: '09:00–15:00', zone: '后厨 蒸笼' },
    { name: '王国强', role: '厨师长', shift: '中班', time: '12:00–20:00', zone: '后厨' },
    { name: '孙倩', role: '收银', shift: '中班', time: '12:00–20:00', zone: '前厅 收银台' },
    { name: '刘洋', role: '服务员', shift: '中班', time: '12:00–20:00', zone: '前厅 A 区' },
    { name: '吴强', role: '保洁', shift: '中班', time: '12:00–20:00', zone: '全店' },
    { name: '郑海', role: '后厨', shift: '晚班', time: '16:00–22:30', zone: '后厨 烧烤' },
    { name: '张伟', role: '服务员', shift: '请假', time: '—', zone: '前厅 A 区' }
  ],

  /* 待批请假 2 条 */
  pendingLeave: [
    { name: '张伟', type: '事假', range: '10/26–10/27', days: 2 },
    { name: '郑海', type: '调休', range: '10/29', days: 1 }
  ],

  /* 月历：2026 年 10 月，10/25 是周日（真实日历），10/1 是周四 */
  cal: {
    year: 2026,
    month: 10,
    weekdays: ['一', '二', '三', '四', '五', '六', '日'],
    today: 25,
    leaveDays: [26, 27],
    pendingDay: 29,
    unscheduled: [31],
    /* 每天在岗人数（10/25 = 早 8 + 中 6 + 晚 11 = 25） */
    counts: [23, 23, 26, 27, 26, 22, 22, 23, 23, 26, 27, 26, 22, 22, 23, 24, 26, 27, 26, 22, 22, 23, 24, 26, 25, 24, 22, 22, 23, 24, 26],
    fixed: 3
  }
};
