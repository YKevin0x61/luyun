/** 班次表编辑页（票 11、票 10）的人话与顺序计算。
 *
 * 服务端给的是 `{id, name, sort_order, is_active, duty_slot, people, days}`：这层只负责
 * 翻成人话、算「上移 / 下移」之后的新顺序、判断哪些动作现在能做、把卫生档位翻成三选一。
 * **放在 util 里而不是页面里**是为了能用真单测钉住 —— 页面模板的 grep 断言抓不住
 * 「删不掉的按钮还是亮的」，也抓不住「清空发成了 null」。
 */

/** 用量那一行：几个人的轮转里排着它、已经排过多少天班。
 *
 *  两个数说的是两件事（服务层 `shift_usage()` 的定义）：`people` 决定能不能停用
 *  （还有人排着它，停用之后规则一展开又写回来），`days` 决定能不能删
 *  （排过班的历史行会跟着这个班次显示，删掉那些天就没班次了）。
 */
export function usageLine(shift) {
  if (!shift) return ''
  const people = shift.people ? `${shift.people} 人在轮转` : '没人在轮转'
  const days = shift.days ? `已排 ${shift.days} 天` : '还没排过班'
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

/** 卫生的日常检查档位（票 10）：这条班次排到的人做白班档还是夜班档的日常检查。
 *
 *  认的是**班次 id 上的这一列**（`staff_shifts.duty_slot`，`migrations/pg/0010_shift_duty_slot.sql`），
 *  不是班次名 —— 店长在班次表上改个名字（票 11 允许）的那天，不该有人突然交不了日常。
 *
 *  三选一的**值和文案都放这里**：页面只 v-for 这个数组，模板里一个固定说法都不写死
 *  （「白班档」这种话是服务端 400 那句话里的口径，抄进模板早晚和后端漂开）。
 */

/** 第三条：清空成「不出日常」。值必须是**空串**，见 `dutySlotPayload`。 */
const NO_DUTY = { value: '', label: '不出日常' }

/** 三选一：白班档 / 夜班档 / 不出日常。`value` 就是 PUT 要发的三种取值。 */
export const DUTY_SLOT_CHOICES = [
  { value: 'day', label: '白班档' },
  { value: 'night', label: '夜班档' },
  NO_DUTY,
]

/** 值 → 人话。从三选一里长出来，不写第二份（将来多一档只改一处）。 */
const DUTY_SLOT_LABELS = Object.fromEntries(
  DUTY_SLOT_CHOICES.map((choice) => [choice.value, choice.label])
)

/** 这条班次现在的档位，规范化成三选一里的值：`'day'` / `'night'` / `''`。
 *
 *  认不出的值（字段缺失、还没应用 0010 的库、以后多出来的档位）一律并到「不出日常」：
 *  那是唯一不替店长做主的说法 —— 猜成某一档，卫生那边就凭空多出一份日常检查。
 */
export function dutySlotValue(shift) {
  const raw = shift ? shift.duty_slot : null
  return raw === 'day' || raw === 'night' ? raw : NO_DUTY.value
}

/** 列表里那一行的人话：白班档 / 夜班档 / 不出日常。 */
export function dutySlotText(shift) {
  return DUTY_SLOT_LABELS[dutySlotValue(shift)]
}

/** 选了之后要 PUT 的 `duty_slot`。
 *
 *  **清空发的是空串**：`PUT /shifts/{id}` 里 `null`（或不给这个字段）= 这一项不动、
 *  空串 = 清空成不出日常（服务层 `_clean_duty_slot` 收两种空，调用方靠 `None` 区分
 *  「不动」）。选了「不出日常」却发 `null`，后端当没改 —— 页面已经显示成清空了，
 *  店长以为改好了，卫生那边照旧发检查单。
 *
 *  认不出的值同样按「不出日常」发：界面上只有三个选项，多出来的只可能是自己传错。
 */
export function dutySlotPayload(value) {
  return value === 'day' || value === 'night' ? value : NO_DUTY.value
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
  if (shift.days) reasons.push(`已排 ${shift.days} 天`)
  if (shift.people) reasons.push(`${shift.people} 人在轮转`)
  if (reasons.length) {
    return `删不掉：${reasons.join(' · ')}；停用就够了`
  }
  if (shift.is_active && activeCount <= 1) {
    return '删不掉：最后一个在用的班次，先加一个别的'
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

/** 两份班次列表合成一份（月历的班次列）。
 *
 *  月历那份（`/calendar` 的 `shifts`）是「启用的全要 + **这个月真有行**的停用班次」
 *  （服务层 `_shifts_for_display`）；名单那份（`/roster`）只有启用的。两份都是当前的
 *  真相，粒度不同 —— 拿名单那份整个替换，停用班次就从格子、图例、当天卡的颜色里消失，
 *  而它在这个月明明还有行（票 11 验收②：停用之后历史排班照旧显示），于是格子上的数字
 *  和点开那天的人头对不上。
 *
 *  以 `displayed` 的顺序为准，把 `extra` 里没见过的**接在后面**；按 id 去重，不改传进来
 *  的数组（页面上那份要等重读之后才变）。
 */
export function mergeShiftList(displayed, extra) {
  const merged = Array.isArray(displayed) ? displayed.slice() : []
  const seen = new Set(merged.map((shift) => shift.id))
  for (const shift of Array.isArray(extra) ? extra : []) {
    if (!shift || seen.has(shift.id)) continue
    seen.add(shift.id)
    merged.push(shift)
  }
  return merged
}
