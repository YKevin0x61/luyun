/**
 * 「合计」视图的计算：把后端按档口返回的逐槽位（5 分钟）单量合并成一条整店曲线。
 *
 * 口径与 `/api/orders/station-speed` 的 SQL 保持一致：
 *  - 只累加 `type === 'today'` 的系列；`yesterday` / `last_week` / `last_month` 是对比线，
 *    不进合计。
 *  - 空档口与楼面（`loumian`）已在后端 SQL 里排除，这里不再过滤 —— 前端看到的
 *    `series` 就是「参与统计的档口」全集，逐点相加即可，不需要第二条取数路径。
 *
 * 全部是纯函数：组件只负责画图，算错了能在单测里复现（`__tests__/stationSpeed.test.js`）。
 */

/** @typedef {{ slots: string[], data: number[] }} MergedSpeed */

/**
 * 逐槽位合并所有档口的某个日期口径的单量。
 *
 * `type` 与后端 series 的 type 对齐：today（默认）/ yesterday / last_week /
 * last_month。合计视图的主线取 today，三条对比参考线各调一次本函数。
 *
 * @param {{ slots?: string[], series?: Array<{ type?: string, data?: number[] }> } | null} result
 * @param {string} [type='today']
 * @returns {MergedSpeed}
 */
export function mergeStationSpeedSeries(result, type = 'today') {
  const slots = result?.slots || []
  if (!slots.length) return { slots: [], data: [] }

  const data = new Array(slots.length).fill(0)
  for (const item of result?.series || []) {
    if (!item || item.type !== type) continue
    const values = item.data || []
    // series.data 理论上与 slots 等长；短了按 0 补齐，长了忽略（表外槽位不属于这张图）。
    const n = Math.min(values.length, data.length)
    for (let i = 0; i < n; i += 1) {
      data[i] += Number(values[i] || 0)
    }
  }
  return { slots: slots.slice(), data }
}

/**
 * 合计曲线的摘要（洞察卡用）。
 *
 * @param {object | null} result
 * @returns {{ total: number, peak: number, peakTime: string, avgActive: number, activeSlots: number }}
 */
export function summarizeMergedSpeed(result) {
  const { slots, data } = mergeStationSpeedSeries(result)
  const total = data.reduce((sum, value) => sum + value, 0)
  const peak = data.length ? Math.max(...data) : 0
  const peakIndex = data.indexOf(peak)
  const activeSlots = data.filter((value) => value > 0).length
  return {
    total,
    peak,
    // 全 0 时 indexOf 会命中 0（07:00），那只是首槽位、不是峰值时段，别拿它冒充。
    peakTime: peak > 0 ? slots[peakIndex] : '—',
    avgActive: activeSlots ? total / activeSlots : 0,
    activeSlots,
  }
}

/**
 * 合计曲线里进单最多的前 N 个槽位（峰值时段）。
 *
 * 同单量按时间先后排：先到的更靠前，避免 `sort` 的稳定性成为唯一依据。
 *
 * @param {object | null} result
 * @param {number} [limit=4]
 * @returns {Array<{ time: string, value: number, index: number }>}
 */
export function topMergedSpeedSlots(result, limit = 4) {
  const { slots, data } = mergeStationSpeedSeries(result)
  return data
    .map((value, index) => ({ time: slots[index] || '—', value, index }))
    .filter((row) => row.value > 0)
    .sort((a, b) => b.value - a.value || a.index - b.index)
    .slice(0, limit)
}
