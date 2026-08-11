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
  it('empty watched set means all stations', () => {
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
      active: true
    })
  })
})
