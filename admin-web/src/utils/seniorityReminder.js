/** 「人事提醒」页（票 05）的展示口径。
 *
 *  与服务端的分工：**折算月、档位、差额全都是服务端算好的**
 *  （`services/identity/profile.py` 是唯一实现 —— 前端不自己算工龄，也不自己解日期），
 *  这里只把 `{state, current, should_be, gap, due_month, next_adjust_month}` 翻成人话。
 *
 *  只有一条是规矩、不是文案：**调过头的人没有「确认调整」**。一键把 500 改成 100
 *  就是自动下调，而 `docs/adr/0101` 明确说了高于应为的只标出来、绝不自动改写档案里的钱。
 *  要降也得超管去花名册里手动填 —— 那是一条有痕迹的人工动作。
 */

export const STATE_DUE = 'due'
export const STATE_OVER = 'over'

/** 这个人是不是「该调了」（现值低于应为，含历史欠调）。 */
export function isDue(item) {
  return item?.state === STATE_DUE
}

/** 能不能点「确认调整」。调过头的只展示，不给按钮。 */
export function canConfirm(item) {
  return isDue(item)
}

export function stateText(item) {
  return isDue(item) ? '该调' : '调过头'
}

/** 现值：`null` 是「没调过」，不是 0 元 —— 两件事在界面上必须不一样。 */
export function currentText(item) {
  const value = item?.current
  return value == null ? '未调' : `${value} 元`
}

/** 差额按方向说：该调的说「差多少」，调过头的说「高出多少」（不带负号）。 */
export function gapText(item) {
  if (!item) return ''
  const gap = Math.abs(Number(item.gap) || 0)
  return isDue(item) ? `差 ${gap} 元` : `高出 ${gap} 元`
}

/** `2027-09` → `2027 年 9 月`；空值 / 坏值回空串（页面据此不渲染那一句）。 */
export function monthText(month) {
  const text = String(month || '')
  if (!/^\d{4}-\d{2}$/.test(text)) return ''
  return `${text.slice(0, 4)} 年 ${Number(text.slice(5, 7))} 月`
}

/** 三块各几条。`due` 才等于服务端那个 `count`（首页那一格问的是「要处理几件」）。 */
export function reminderCounts(data) {
  const items = data?.seniority?.items || []
  return {
    due: items.filter(isDue).length,
    over: items.filter((item) => !isDue(item)).length,
    incomplete: (data?.incomplete?.items || []).length,
  }
}

/** 确认调整要写回去的值：服务端给的**应为**值（永远不是现值）。 */
export function confirmPayload(item) {
  return { seniority_bonus: item?.should_be ?? 0 }
}
