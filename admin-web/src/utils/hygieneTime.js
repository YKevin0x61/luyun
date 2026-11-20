/**
 * 卫生模块统一的时间展示。
 *
 * 后端返回的 `occurred_at` / `captured_at` / `due_at` 都是带 `+08:00` 的 ISO 串，
 * 但**分隔符不保证是 `T`**：历史数据、手写 SQL 补的行、以及两种后端（SQLite / PG）
 * 之间都存在 `2026-09-20 08:30:00` 这种空格分隔的写法。
 *
 * 原来四处各写了一份 `/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/`（`formatStamp`、
 * `deadlineText` 的兜底、`formatWatermarkTime`、`HygieneBoardsView.weekLabel`），
 * 遇到空格分隔就把整串 ISO 原样吐到界面上——水印、看板、整改单上能看到
 * `2026-09-20 08:30:00+08:00`。这里统一吃两种分隔符，并保留「解析不了就原样返回」
 * 的降级（不要造出 `Invalid Date`）。
 */

const STAMP_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/

/**
 * `2026-09-20 08:30`；解析不了时原样返回，便于排查脏数据而不是显示成空白。
 * @param {unknown} value ISO 时间串
 * @returns {string}
 */
export function formatHygieneStamp(value) {
  const raw = String(value ?? '')
  const matched = STAMP_RE.exec(raw)
  if (!matched) return raw
  return `${matched[1]}-${matched[2]}-${matched[3]} ${matched[4]}:${matched[5]}`
}

/**
 * `09-20 08:30`（省略年份）；解析不了返回空串——调用方要自己补上下文文案。
 * @param {unknown} value ISO 时间串
 * @returns {string}
 */
export function formatHygieneShortStamp(value) {
  const matched = STAMP_RE.exec(String(value ?? ''))
  if (!matched) return ''
  return `${matched[2]}-${matched[3]} ${matched[4]}:${matched[5]}`
}
