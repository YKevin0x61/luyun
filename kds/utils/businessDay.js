/**
 * 营业日（中国时间）日界。
 *
 * KDS 跑在门店设备上，设备系统时区不可信（Android 盒子出厂 UTC、没配 NTP、运维设错），
 * 所以「今天」一律按东八区固定偏移算，不读设备时区：后端 `api/orders.py` 把收到的
 * 时间戳按 `CHINA_TZ` 归一，端侧只有用同一个锚点，窗口才落在同一个营业日上。
 *
 * 用固定 +08:00 而不是 Intl/Asia/Shanghai：中国 1991 年后不再实行夏令时，
 * 固定偏移与 Asia/Shanghai 等价，且不依赖设备的 tzdata 与运行时 ICU 数据。
 */

/** 东八区固定偏移（毫秒）。 */
export const CST_OFFSET_MS = 8 * 60 * 60 * 1000

const DAY_MS = 24 * 60 * 60 * 1000

/**
 * @param {number|Date} [now]
 * @returns {number} 毫秒时间戳
 */
function toMillis(now) {
  return now instanceof Date ? now.getTime() : Number(now)
}

/**
 * 中国日当天 00:00:00.000 的绝对毫秒数。
 * @param {number|Date} [now]
 * @returns {number}
 */
export function startOfChinaDay(now = Date.now()) {
  const ms = toMillis(now)
  return Math.floor((ms + CST_OFFSET_MS) / DAY_MS) * DAY_MS - CST_OFFSET_MS
}

/**
 * 当天的拉单窗口（ISO 8601 UTC，交给后端按 CHINA_TZ 归一）。
 * @param {number|Date} [now]
 * @returns {{ start: string, end: string }}
 */
export function chinaDayRange(now = Date.now()) {
  const startMs = startOfChinaDay(now)
  return {
    start: new Date(startMs).toISOString(),
    end: new Date(startMs + DAY_MS - 1).toISOString()
  }
}

/**
 * 中国日历日 `YYYY-MM-DD`（用作营业日键）。
 * @param {number|Date} [now]
 * @returns {string}
 */
export function chinaDateKey(now = Date.now()) {
  const shifted = new Date(toMillis(now) + CST_OFFSET_MS)
  const year = shifted.getUTCFullYear()
  const month = String(shifted.getUTCMonth() + 1).padStart(2, '0')
  const day = String(shifted.getUTCDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * 时间点是否落在 `now` 所在的中国日。
 * @param {string|number|Date} time
 * @param {number|Date} [now]
 * @returns {boolean}
 */
export function isChinaToday(time, now = Date.now()) {
  const target = toMillis(time instanceof Date ? time : new Date(time))
  if (!Number.isFinite(target)) return false
  return startOfChinaDay(target) === startOfChinaDay(now)
}
