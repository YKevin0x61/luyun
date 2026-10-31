/** 「那一天要顶的档」清单（票 02）：周表的弹层（票 03）与名单面板那一节（票 04）都只渲染
 *  这里的输出，两处不再各算一遍。输入只吃 `GET /week` 里的三项 —— `employees`（含每人的
 *  `cells[date]` 与 `zone_defaults[]`）、`shifts`（含 `duty_slot`）、`zones`（含
 *  `day_shift` / `night_shift`）—— 加上被点的那一格的日子 `date`。不读 `roster`、不读
 *  hygiene 接口、不碰 DOM、不定义任何文案（「休 / 请假」「没人」由渲染层给）。
 *
 *  为什么按「人 × 固定区配置」算，而不是按「区扫空」：
 *  卫生那边是「区 × 班次档」扫逾期，但排班这一侧读不到检查项，也不该读 —— 往「区」里扫，
 *  只能扫出「这个区此刻没有班次」，而店里真正的源是**休息 / 请假的人**：他没来，他那个
 *  固定区那个档才空。所以起点是那天 `scheduled && shift_id === null` 的人，再展开他的
 *  `zone_defaults`（每个班次一条固定区），一人一行。
 *
 *  为什么半休的人算「这个档还有谁」：
 *  半休那格**班次还在**（`shift_id !== null`，旁边多一个「半」标记），他那天真的会来上半天。
 *  判据因此就是「那天有班次、同区、同档位」，半休自然落进来；不用另立一个字段，也不会把
 *  一个只上半天的人当成没人。
 *
 *  为什么停用班次照列：
 *  班次停用只表示**不能再往上排**了，但它这一周的行还在、那个档可能真的没人。列出来并给出
 *  `can_pen=false` + 原因，店长才知道该去班次表启用它，而不是以为系统漏了。而且笔本身只认
 *  启用中的班次（`penShift` 在 `activeShifts` 里找，硬装会默默退化成第一个启用班次 —— 那会
 *  写到别人身上），所以停用班次的行**不能**装笔。已删的工作区同理：行照列（配置还在），
 *  但没有区可装。
 *
 *  排序：按班次 `sort_order`，同班次按区 `id`；同一个人配了两个班次就是两行。同一个档有
 *  两个人休也是两行，不合并 —— 一行对应一个人，才知道要跟谁说（2026-11-01 裁定）。
 */

export const BLOCKED_SHIFT_INACTIVE = 'shift_inactive'
export const BLOCKED_ZONE_DELETED = 'zone_deleted'

/** 班次表的 `duty_slot` → 工作区上「这个区跑不跑这一档」的开关名。
 *
 *  `duty_slot` 只认 `day` / `night`（`store._clean_duty_slot` 的同一套词）；别的值（含
 *  `null` 的「中班」）判断不了工作区跑不跑，整行不列。
 */
const ZONE_SLOT_FLAG = { day: 'day_shift', night: 'night_shift' }

function indexById(list) {
  const index = new Map()
  for (const item of list || []) {
    if (item && item.id !== null && item.id !== undefined) index.set(Number(item.id), item)
  }
  return index
}

function sameId(left, right) {
  if (left === null || left === undefined || right === null || right === undefined) return false
  return Number(left) === Number(right)
}

/** 某一天要顶的档。
 *
 *  `input = { employees, shifts, zones, date }`：
 *   · `employees[].cells[date] = { scheduled, shift_id, zone_id, leave }`；
 *   · `employees[].zone_defaults = [{ shift_id, zone_id, zone_name }]`（区被删时 `zone_name`
 *     为 null）；
 *   · `shifts[] = [{ id, name, sort_order, is_active, duty_slot }]`；
 *   · `zones[] = [{ id, name, day_shift, night_shift }]`。
 *
 *  返回按 spec 的一行一条；那天没人休 / 请假时返回 `[]`（文案由调用方给）。
 */
export function coverSlotsFor(input) {
  const { employees, shifts, zones, date } = input || {}
  const shiftById = indexById(shifts)
  const zoneById = indexById(zones)
  const people = employees || []

  // 「这个档现在还有谁」：那天有班次（含半休）、同区、同档位的人名。按周表给的行序取，
  // 不重排（店长认的是表上的顺序）。
  const othersFor = (slot, zoneId) => {
    const names = []
    for (const employee of people) {
      const cell = employee && employee.cells ? employee.cells[date] : null
      if (!cell || cell.shift_id === null || cell.shift_id === undefined) continue
      const shift = shiftById.get(Number(cell.shift_id))
      if (!shift || shift.duty_slot !== slot) continue
      if (!sameId(cell.zone_id, zoneId)) continue
      names.push(employee.name)
    }
    return names
  }

  const entries = []
  for (const employee of people) {
    const cell = employee && employee.cells ? employee.cells[date] : null
    // 源只有「休 / 请假」：那天排了、但没有班次。`leave` 只决定行上的标记。
    if (!cell || cell.scheduled !== true) continue
    if (cell.shift_id !== null && cell.shift_id !== undefined) continue

    for (const item of employee.zone_defaults || []) {
      const shift = shiftById.get(Number(item.shift_id))
      // 班次被硬删：连档位都不知道，判不了。
      if (!shift) continue
      const slot = shift.duty_slot
      const flag = ZONE_SLOT_FLAG[slot]
      // 没有 duty_slot（「中班」）或认不出的档位：不属于任何日常档，不列。
      if (!flag) continue
      // 固定区没配：没有区可顶。
      if (item.zone_id === null || item.zone_id === undefined) continue
      const zone = zoneById.get(Number(item.zone_id))
      // 区被删：照列，但不能装笔（理由在下面判断）。
      // 区在但不跑这一档：不列 —— 真数据里就有人配了「馅档 × 早班」，而馅档只跑夜市。
      if (zone && !zone[flag]) continue
      // 笔只认启用中的班次、也只装得进存在的区：两者缺一就不能装笔，并说清是哪一条。
      // 两条同时不满足时取「区已删」—— 区都没了，启用班次也顶不上。
      const blocked = !zone
        ? BLOCKED_ZONE_DELETED
        : (shift.is_active === false ? BLOCKED_SHIFT_INACTIVE : null)
      // 排序键（`sort_order` + 区 id）；两个键都相同的行按入列先后排 —— `Array#sort`
      // 是稳定排序，数据脏、同班次同区出现两条时不会来回抖。
      entries.push({
        row: {
          employee_id: employee.id,
          employee_name: employee.name,
          leave: !!cell.leave,
          shift_id: Number(shift.id),
          shift_name: shift.name,
          slot,
          zone_id: Number(item.zone_id),
          zone_name: zone ? zone.name : (item.zone_name ?? null),
          others: othersFor(slot, item.zone_id),
          can_pen: blocked === null,
          blocked_reason: blocked,
        },
        sortOrder: Number(shift.sort_order) || 0,
        zoneId: Number(item.zone_id),
      })
    }
  }
  // 排序：按班次 `sort_order`、同班次按区 `id`。同一个人配了两个班次就是两行 —— 他的两行
  // 各按自己班次的序走；只有他一个人休时自然相邻（店主按表上的档位找人，不按人名聚类）。
  entries.sort((left, right) => (
    left.sortOrder - right.sortOrder
    || left.zoneId - right.zoneId
  ))
  return entries.map((entry) => entry.row)
}
