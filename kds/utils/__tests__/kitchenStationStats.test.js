import { describe, expect, it } from 'vitest'
import {
  buildCurrentStationStats,
  buildStationTabStats,
  decorateOrderWait
} from '../kitchenStationStats.js'

describe('kitchenStationStats', () => {
  it('buildStationTabStats counts pending and urgent', () => {
    const now = Date.now()
    const ordersByStation = {
      changfen: [
        { dish_status: '待出餐', order_time: new Date(now - 25 * 60 * 1000).toISOString() },
        { dish_status: '待出餐', order_time: new Date(now - 5 * 60 * 1000).toISOString() },
        { dish_status: '已上菜', order_time: new Date(now - 40 * 60 * 1000).toISOString() }
      ]
    }
    const stats = buildStationTabStats(
      ['changfen'],
      (id) => ordersByStation[id] || [],
      { urgentMs: 20 * 60 * 1000 }
    )
    expect(stats.changfen.pending).toBe(2)
    expect(stats.changfen.urgent).toBe(1)
  })

  it('does not count 已取消 退示 as pending or urgent', () => {
    const now = Date.now()
    const stats = buildStationTabStats(
      ['changfen'],
      () => [
        { dish_status: '待出餐', order_time: new Date(now - 5 * 60 * 1000).toISOString() },
        {
          dish_status: '已取消',
          status: '退菜',
          quantity: 0,
          order_time: new Date(now - 40 * 60 * 1000).toISOString()
        }
      ],
      { urgentMs: 20 * 60 * 1000 }
    )
    expect(stats.changfen.pending).toBe(1)
    expect(stats.changfen.urgent).toBe(0)
  })

  it('decorateOrderWait classifies by thresholds', () => {
    const now = Date.now()
    const orderTime = new Date(now - 18 * 60 * 1000).toISOString()
    const d = decorateOrderWait(orderTime, now, {
      warningMs: 15 * 60 * 1000,
      urgentMs: 20 * 60 * 1000
    })
    expect(d.isOvertime).toBe(false)
    expect(d.waitTimeClass).toBe('warning')
  })

  it('buildCurrentStationStats returns overtime and completed today', () => {
    const now = Date.now()
    const todayReady = new Date(now).toISOString()
    const stats = buildCurrentStationStats(
      [
        {
          dish_status: '待出餐',
          order_time: new Date(now - 30 * 60 * 1000).toISOString()
        },
        {
          dish_status: '已制作待上菜',
          order_time: new Date(now - 40 * 60 * 1000).toISOString(),
          ready_time: todayReady
        }
      ],
      { urgentMs: 20 * 60 * 1000 }
    )
    expect(stats.pendingCount).toBe(1)
    expect(stats.overtimeCount).toBe(1)
    expect(stats.completedToday).toBe(1)
    expect(stats.avgCookingTime).not.toBe('0分')
  })

  it('buildCurrentStationStats excludes 已取消 退示 from pendingCount', () => {
    const now = Date.now()
    const stats = buildCurrentStationStats(
      [
        {
          dish_status: '待出餐',
          order_time: new Date(now - 5 * 60 * 1000).toISOString()
        },
        {
          dish_status: '已取消',
          status: '退菜',
          quantity: 0,
          order_time: new Date(now - 30 * 60 * 1000).toISOString()
        }
      ],
      { urgentMs: 20 * 60 * 1000 }
    )
    expect(stats.pendingCount).toBe(1)
    expect(stats.overtimeCount).toBe(0)
  })
})
