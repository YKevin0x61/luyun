/** 班次表编辑页（票 11）的人话与顺序计算。
 *
 * 服务端给的是 `{id, name, sort_order, is_active, people, days}`：这层只负责翻成人话、
 * 算「上移 / 下移」之后的新顺序、判断哪些动作现在能做。**放在 util 里而不是页面里**
 * 是为了能用真单测钉住 —— 页面模板的 grep 断言抓不住「删不掉的按钮还是亮的」。
 */

/** 用量那一行：几个人的轮转里排着它、已经排过多少天班。
 *
 *  两个数说的是两件事（服务层 `shift_usage()` 的定义）：`people` 决定能不能停用
 *  （还有人排着它，停用之后规则一展开又写回来），`days` 决定能不能删
 *  （排过班的历史行会跟着这个班次显示，删掉那些天就没班次了）。
 */
export function usageLine(shift) {
  if (!shift) return ''
  const people = shift.people
    ? `${shift.people} 个人的轮转里排着它`
    : '没有人的轮转里排着它'
  const days = shift.days ? `已经排过 ${shift.days} 天班` : '还没排过班'
  return `${people} · ${days}`
}

/** 还在用 / 已停用：列表右边那个小标签。 */
export function statusText(shift) {
  return shift && shift.is_active ? '在用' : '已停用'
}

/** 停用 / 启用的按钮文案（停用的班次给的是「启用」）。 */
export function toggleLabel(shift) {
  return shift && shift.is_active ? '停用' : '启用'
}

/** 能不能删：没人用过、而且删完还得剩至少一条在用的。
 *
 *  服务端还会再判一次（那份才是权威），这里只是**别让按钮骗人** —— 点下去必然
 *  挨一句「用过了删不掉」或「至少得留一个能用的班次」的按钮，不如一开始就是灰的，
 *  并且把原因写在旁边。
 *
 *  `activeCount` 是**还在用**的班次条数（`/shifts/manage` 的 `active_count`）：
 *  停用的班次删掉不影响「还有没有人能排班」，在用的就不一样了。不给这个数时按 1 算 ——
 *  不确定时按钮灰着，这是保守的一边。
 */
export function canDelete(shift, activeCount = 1) {
  if (!shift) return false
  if (shift.people || shift.days) return false
  if (!shift.is_active) return true
  return activeCount > 1
}

/** 删不了的原因（放在按钮的 title 上，也让页面能照原话说）。 */
export function deleteBlockedReason(shift, activeCount = 1) {
  if (!shift) return ''
  const reasons = []
  if (shift.days) reasons.push(`已经排过 ${shift.days} 天班`)
  if (shift.people) reasons.push(`还有 ${shift.people} 个人的轮转里排着它`)
  if (reasons.length) {
    return `删不掉（${reasons.join('，')}）：停用就够了，历史排班照旧显示`
  }
  if (shift.is_active && activeCount <= 1) {
    return '删不掉（这是最后一个在用的班次）：先加一个或启用一个别的班次'
  }
  return ''
}

/** 上移 / 下移之后的新顺序（整表 id 数组）；到顶、到底返回 `null`。
 *
 *  服务端的重排要**给全**（见 `reorder_shifts`），所以这里返回的是整表的新顺序，
 *  页面拿它一次 PUT 过去 —— 而不是自己算两个 sort_order 分两次写（写一半就成了
 *  两张同位的班次）。
 */
export function moveShift(ids, index, delta) {
  const list = Array.isArray(ids) ? ids.slice() : []
  const target = index + delta
  if (!list.length || index < 0 || index >= list.length) return null
  if (target < 0 || target >= list.length) return null
  const [moved] = list.splice(index, 1)
  list.splice(target, 0, moved)
  return list
}
