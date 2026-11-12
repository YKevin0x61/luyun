/** 复盘页的数据质量判据（票 09）。
 *
 * 只读票 08 写进 `prep_plan_context.data_quality_json` 的两个数（接口已解析成
 * `context.data_quality` 对象）：
 *   · `snapshot_hours`：当天写了几个小时的快照
 *   · `orders_today`：当天采到的订单条数
 * 兼容别名（票 08 落地前按描述写的名字，以及将来换名）：`covered_hours` /
 * `snapshot_count`、`order_lines` / `order_count` / `orders`（`orders` 若是对象则取它
 * 里面的 `total` / `count`）。
 *
 * **字段缺失、类型不对、JSON 坏了一律当作「没有信号」**：不抛错、不提示。原因很简单 ——
 * 这一条是解释项，采集正常的日子不该被一条误报盖住；而老库（票 08 之前）根本没有这个键。
 *
 * 阈值是「明显不对」的量级，不是精度判断：
 *   · 按 `context.runtime` 的开店/打烊时段算，缺省 07:00–21:30 共 15 个整点，
 *     覆盖不到预期的一半才算断档；
 *   · 正常一天是几百条订单，不到 20 条基本就是没采到。
 */
import {
  prepReviewQualityFewOrders,
  prepReviewQualitySparseHours,
} from './prepPlanCopy'
import { expectedSnapshotSlots } from './reviewDay'

/** 覆盖不到的预期比例：低于它才算「覆盖很少」。 */
export const QUALITY_COVERAGE_RATIO = 0.6
/** 不到这个张数的订单算「明显偏少」。 */
export const QUALITY_MIN_ORDER_LINES = 20

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

/** 后端可能给已解析的对象，也可能给 JSON 文本；坏数据当空对象。 */
export function readDataQuality(raw) {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw
  if (typeof raw !== 'string' || !raw.trim()) return {}
  try {
    return asObject(JSON.parse(raw))
  } catch (_) {
    return {}
  }
}

function toCount(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

function pickOrderLines(quality) {
  for (const key of ['orders_today', 'order_lines', 'order_count', 'orders']) {
    const direct = toCount(quality[key])
    if (direct !== null) return direct
    const nested = asObject(quality[key])
    const fromNested = toCount(nested.total ?? nested.count)
    if (fromNested !== null) return fromNested
  }
  return null
}

function pickCoveredHours(quality) {
  for (const key of ['snapshot_hours', 'covered_hours', 'snapshot_count']) {
    const value = toCount(quality[key])
    if (value !== null) return value
  }
  return null
}

/** 数据质量提示的原因（已经本地化）；空数组 = 数据看不出问题。 */
export function qualityReasons(raw, { runtime = null, isToday = false, nowHour = null } = {}) {
  const quality = readDataQuality(raw)
  const reasons = []
  const expected = expectedSnapshotSlots({ runtime, isToday, nowHour })
  const covered = pickCoveredHours(quality)

  // 预期不到 4 个钟点的时段（清早、刚部署）不判覆盖：那时少本来正常。
  if (covered !== null && expected >= 4 && covered < expected * QUALITY_COVERAGE_RATIO) {
    reasons.push(prepReviewQualitySparseHours(covered))
  }

  const orderLines = pickOrderLines(quality)
  const enoughSlots = expected >= 10 || (covered !== null && covered >= 6)
  if (orderLines !== null && enoughSlots && orderLines < QUALITY_MIN_ORDER_LINES) {
    reasons.push(prepReviewQualityFewOrders(orderLines))
  }
  return reasons
}
