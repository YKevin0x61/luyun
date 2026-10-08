/** 「人事提醒」页生日那一块（票 06）的展示口径。
 *
 *  与服务端的分工：**生日、这一年的生效日子、今天 / 已过 / 未到全是服务端算好的**
 *  （`services/identity/profile.py` 的 `birthday_from_id_card` / `birthday_in_year`
 *  是唯一实现）。前端不自己解身份证，也不自己比日期 —— 那两件事各写一份就会漂。
 *
 *  这里只有一处是规矩、不是文案：**闰日在平年折到 28 日时要说出来**。身份证上写着
 *  02-29、页面显示 2 月 28 日，不解释一句就像系统记错了人（`docs/adr/0102`）。
 */

export const STATE_TODAY = 'today'
export const STATE_PAST = 'past'
export const STATE_UPCOMING = 'upcoming'

/** `09-20` → `9 月 20 日`；空值 / 坏值回空串（调用方据此不渲染那一句）。 */
export function birthdayText(value) {
  const text = String(value ?? '')
  if (!/^\d{2}-\d{2}$/.test(text)) return ''
  return `${Number(text.slice(0, 2))} 月 ${Number(text.slice(3, 5))} 日`
}

/** 今天 / 已过 / 未到。认不出的状态按「还没到」渲染（它只影响一个词，不挡操作）。
 *
 *  名字带前缀是刻意的：`utils/seniorityReminder.js` 也有一个 `stateText`（该调 /
 *  调过头），两个一起 import 时不该靠 as 别名去分。
 */
export function birthdayStateText(item) {
  if (item?.state === STATE_TODAY) return '就是今天'
  return item?.state === STATE_PAST ? '已过' : '还没到'
}

/** 身份证上是闰日、这一年按 2 月 28 日提醒时补一句；其余情况回空串。 */
export function leapNoteText(item) {
  if (!item) return ''
  return item.birthday === '02-29' && item.on === '02-28'
    ? '闰日，平年按 28 日提醒'
    : ''
}
