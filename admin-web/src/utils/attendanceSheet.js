/**
 * 考勤表（考勤表导出票 01）的界面口径：逐日三态与卡片上那两项的说法。
 *
 * 服务端下发的是**状态码**（`work` / `rest` / `todo` / `null`）与派生值（`new_hire`
 * 可能是 `null`）：「是什么」在服务端算，「怎么写」只在这一处翻 —— 卡片与（票 08 的）
 * 导出件共用同一批字，两处不会各说各话。
 */

const DAY_STATE_TEXT = {
  work: '上班',
  rest: '休',
  todo: '待补',
}

/** 那天这个人是什么：上班 / 休 / 待补。今天之后（`null`）**留空** —— 那是「还没到」，
 *  不是「要人工确认」，两者在纸面上必须长得不一样。 */
export function dayStateText(state) {
  return DAY_STATE_TEXT[state] || ''
}

/** 是否新入职：`null` = 入职日期为空、这一项算不出来 —— 留空，不是「否」。 */
export function newHireText(value) {
  if (value === true) return '是'
  if (value === false) return '否'
  return ''
}

/** 当月净时长（小时，可为负、可带 .5）：`+2 小时` / `−1.5 小时` / `0 小时`。
 *  带符号是因为「这个月净欠的」与「这个月净加的」要一眼分得开；减号用全角，与
 *  `utils/overtimeEntry.js` 的 `formatHalfHours` 同一个字符。 */
export function formatNetHours(hours) {
  const value = Number(hours) || 0
  if (value === 0) return '0 小时'
  const sign = value > 0 ? '+' : '−'
  const size = Math.abs(value)
  return `${sign}${Number.isInteger(size) ? size : size.toFixed(1)} 小时`
}
