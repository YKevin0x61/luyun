import { describe, expect, it, vi } from 'vitest'
import { afterBatchUpdate, afterQuickAdd } from '../plugins/dishStations.js'
import { listTablePlugins, resolveTablePlugin } from '../tablePlugins.js'

describe('resolveTablePlugin', () => {
  it('returns dish_stations plugin', () => {
    const plugin = resolveTablePlugin('dish_stations')
    expect(plugin?.table).toBe('dish_stations')
    expect(plugin?.Extras).toBeTruthy()
    expect(plugin?.readOnly).toBe(true)
    expect(typeof plugin.afterBatchUpdate).toBe('function')
  })

  it('returns null for unknown / empty tables', () => {
    expect(resolveTablePlugin('orders')).toBeNull()
    expect(resolveTablePlugin('')).toBeNull()
    expect(resolveTablePlugin(null)).toBeNull()
  })

  it('lists registered plugins', () => {
    expect(listTablePlugins().map((p) => p.table)).toContain('dish_stations')
  })
})

describe('dishStations afterBatchUpdate', () => {
  it('appends sync hint when station column updated', () => {
    expect(
      afterBatchUpdate({
        column: 'station',
        res: { message: '已更新 3 条', affected: 3 },
        ids: [1, 2, 3],
      }),
    ).toBe('已更新 3 条；如需同步订单档口请点击「补充档口」')
  })

  it('keeps default message for other columns', () => {
    expect(
      afterBatchUpdate({
        column: 'dish_name',
        res: { affected: 2 },
        ids: [1, 2],
      }),
    ).toBe('已更新 2 条')
  })
})

describe('dishStations afterQuickAdd', () => {
  it('reloads rows', () => {
    const reload = vi.fn()
    afterQuickAdd({ reload })
    expect(reload).toHaveBeenCalledOnce()
  })
})
