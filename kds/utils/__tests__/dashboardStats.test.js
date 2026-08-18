import { describe, expect, it } from 'vitest'
import {
  buildWatchedStationStatuses,
  countPendingAndUrgent,
  countUnmappedDishNames,
  filterMergedDishesByWatched,
  isStationInWatched
} from '../dashboardStats.js'

const PENDING = '待出餐'

describe('isStationInWatched', () => {
  it('empty filter means all stations (hub mosaic)', () => {
    expect(isStationInWatched('changfen', [])).toBe(true)
  })

  it('filters to watched ids', () => {
    expect(isStationInWatched('changfen', ['changfen'])).toBe(true)
    expect(isStationInWatched('xibing', ['changfen'])).toBe(false)
  })
})

describe('filterMergedDishesByWatched', () => {
  const dishes = [{ station: 'changfen' }, { station: 'xibing' }]

  it('returns all when watched is empty', () => {
    expect(filterMergedDishesByWatched(dishes, [])).toEqual(dishes)
  })

  it('keeps only watched stations', () => {
    expect(filterMergedDishesByWatched(dishes, ['xibing'])).toEqual([{ station: 'xibing' }])
  })
})

describe('countPendingAndUrgent', () => {
  it('counts pending dishes and urgent subset', () => {
    const dishes = [
      { orders: [{ dish_status: PENDING }], urgentCount: 2 },
      { orders: [{ dish_status: PENDING }], urgentCount: 0 },
      { orders: [{ dish_status: '已上菜' }], urgentCount: 1 }
    ]
    expect(countPendingAndUrgent(dishes, PENDING)).toEqual({ total: 2, urgent: 1 })
  })

  it('sums pending 份数 not dish groups', () => {
    const dishes = [
      { orders: [{ dish_status: PENDING, quantity: 3 }], urgentCount: 1 },
      { orders: [{ dish_status: PENDING, quantity: 2, served_quantity: 1 }], urgentCount: 0 },
      { orders: [{ dish_status: '已上菜', quantity: 9 }], urgentCount: 1 }
    ]
    expect(countPendingAndUrgent(dishes, PENDING)).toEqual({ total: 4, urgent: 1 })
  })

  it('excludes refund 待出餐 rows from 待制作, matching kitchen console', () => {
    const dishes = [
      {
        orders: [
          { dish_status: PENDING, quantity: 1 },
          { dish_status: PENDING, quantity: 1, status: '退菜', change_type: '退菜' }
        ],
        urgentCount: 1
      },
      {
        orders: [
          {
            dish_status: PENDING,
            quantity: 1,
            status: '退菜',
            business_flow_id: 'YY001_虾饺_refund_1'
          }
        ],
        urgentCount: 2
      }
    ]
    expect(countPendingAndUrgent(dishes, PENDING)).toEqual({ total: 1, urgent: 1 })
  })

  it('excludes 已取消 退示 from 待制作 even when quantity is 0', () => {
    const dishes = [
      {
        orders: [
          { dish_status: PENDING, quantity: 2 },
          {
            dish_status: '已取消',
            status: '退菜',
            quantity: 0,
            business_flow_id: 'flow-notice'
          }
        ],
        urgentCount: 1
      }
    ]
    expect(countPendingAndUrgent(dishes, PENDING)).toEqual({ total: 2, urgent: 1 })
  })
})

describe('countUnmappedDishNames', () => {
  it('counts unique names with empty or qita station', () => {
    const dishes = [
      { dishName: 'A', station: 'qita' },
      { dishName: 'A', station: '' },
      { dishName: 'B', station: null },
      { dishName: 'C', station: 'changfen' }
    ]
    expect(countUnmappedDishNames(dishes, 'qita')).toBe(2)
  })
})

describe('buildWatchedStationStatuses', () => {
  const stations = [
    { id: 'changfen', name: '肠粉档', color: '#4ECDC4' },
    { id: 'xibing', name: '西饼档', color: '#FF6B6B' }
  ]

  it('scopes rows to watched stations and pending counts', () => {
    const dishes = [
      { station: 'changfen', orders: [{ dish_status: PENDING }] },
      { station: 'changfen', orders: [{ dish_status: PENDING }] },
      { station: 'xibing', orders: [{ dish_status: PENDING }] }
    ]
    const rows = buildWatchedStationStatuses(stations, dishes, ['changfen'], PENDING)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      id: 'changfen',
      pendingCount: 2,
      urgentCount: 0,
      active: true,
      completedToday: 0,
      avgCookingTime: '0分'
    })
  })

  it('counts today’s cooked lines with ready time as secondary stats', () => {
    const now = Date.now()
    const todayReady = new Date(now).toISOString()
    const yesterdayReady = new Date(now - 25 * 60 * 60 * 1000).toISOString()
    const dishes = [
      {
        station: 'changfen',
        orders: [{ dish_status: PENDING, order_time: new Date(now - 5 * 60 * 1000).toISOString() }],
        urgentCount: 0
      },
      {
        station: 'changfen',
        orders: [
          {
            dish_status: '已制作待上菜',
            order_time: new Date(now - 10 * 60 * 1000).toISOString(),
            ready_time: todayReady
          }
        ]
      },
      {
        station: 'changfen',
        orders: [
          {
            dish_status: '已上菜',
            order_time: new Date(now - 10 * 60 * 1000).toISOString(),
            ready_time: yesterdayReady
          }
        ]
      },
      {
        station: 'changfen',
        orders: [
          {
            dish_status: '已制作待上菜',
            order_time: new Date(now - 10 * 60 * 1000).toISOString()
          }
        ]
      },
      {
        station: 'xibing',
        orders: [
          {
            dish_status: '已制作待上菜',
            order_time: new Date(now - 8 * 60 * 1000).toISOString(),
            ready_time: todayReady
          },
          {
            dish_status: '已上菜',
            order_time: new Date(now - 12 * 60 * 1000).toISOString(),
            ready_time: todayReady
          }
        ]
      }
    ]
    const rows = buildWatchedStationStatuses(stations, dishes, [], PENDING)
    expect(rows.find((r) => r.id === 'changfen')).toMatchObject({
      pendingCount: 1,
      urgentCount: 0,
      completedToday: 1,
      avgCookingTime: '10分'
    })
    expect(rows.find((r) => r.id === 'xibing')).toMatchObject({
      pendingCount: 0,
      urgentCount: 0,
      completedToday: 2,
      avgCookingTime: '10分'
    })
  })

  it('sums pending 份数 per station, ignoring cooked lines', () => {
    const dishes = [
      {
        station: 'changfen',
        orders: [
          { dish_status: PENDING, quantity: 3 },
          { dish_status: PENDING, quantity: 2 }
        ]
      },
      {
        station: 'changfen',
        orders: [{ dish_status: '已上菜', quantity: 10 }]
      },
      {
        station: 'xibing',
        orders: [{ dish_status: PENDING, quantity: 4, served_quantity: 1 }]
      }
    ]
    const rows = buildWatchedStationStatuses(stations, dishes, [], PENDING)
    expect(rows.find((r) => r.id === 'changfen')).toMatchObject({
      pendingCount: 5,
      urgentCount: 0
    })
    expect(rows.find((r) => r.id === 'xibing')).toMatchObject({
      pendingCount: 3,
      urgentCount: 0
    })
  })

  it('counts urgent pending dishes per station', () => {
    const dishes = [
      { station: 'changfen', orders: [{ dish_status: PENDING }], urgentCount: 2 },
      { station: 'changfen', orders: [{ dish_status: PENDING }], urgentCount: 0 },
      { station: 'changfen', orders: [{ dish_status: '已上菜' }], urgentCount: 3 },
      { station: 'xibing', orders: [{ dish_status: PENDING }], urgentCount: 1 }
    ]
    const rows = buildWatchedStationStatuses(stations, dishes, [], PENDING)
    expect(rows.find((r) => r.id === 'changfen')).toMatchObject({
      pendingCount: 2,
      urgentCount: 1,
      completedToday: 0,
      avgCookingTime: '0分'
    })
    expect(rows.find((r) => r.id === 'xibing')).toMatchObject({
      pendingCount: 1,
      urgentCount: 1,
      completedToday: 0,
      avgCookingTime: '0分'
    })
  })

  it('does not treat refund-only 待出餐 as 待制作', () => {
    const dishes = [
      {
        station: 'changfen',
        orders: [
          { dish_status: PENDING, quantity: 1, status: '退菜', change_type: '退菜' },
          { dish_status: PENDING, quantity: 1, status: '退菜', business_flow_id: 'cf_refund_1' }
        ],
        urgentCount: 2
      },
      {
        station: 'xibing',
        orders: [
          { dish_status: PENDING, quantity: 2 },
          { dish_status: PENDING, quantity: 1, status: '退菜', change_type: '退菜' }
        ],
        urgentCount: 0
      }
    ]
    const rows = buildWatchedStationStatuses(stations, dishes, [], PENDING)
    expect(rows.find((r) => r.id === 'changfen')).toMatchObject({
      pendingCount: 0,
      urgentCount: 0,
      active: false
    })
    expect(rows.find((r) => r.id === 'xibing')).toMatchObject({
      pendingCount: 2,
      urgentCount: 0,
      active: true
    })
  })

  it('does not count 已取消 退示 toward station 待制作', () => {
    const dishes = [
      {
        station: 'changfen',
        orders: [
          { dish_status: PENDING, quantity: 1 },
          { dish_status: '已取消', status: '退菜', quantity: 0 }
        ],
        urgentCount: 1
      }
    ]
    const rows = buildWatchedStationStatuses(stations, dishes, [], PENDING)
    expect(rows.find((r) => r.id === 'changfen')).toMatchObject({
      pendingCount: 1,
      urgentCount: 1,
      active: true
    })
  })
})
