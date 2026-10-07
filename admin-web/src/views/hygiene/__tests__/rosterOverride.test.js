import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const roster = readFileSync(join(here, '../HygieneRosterView.vue'), 'utf8')

/**
 * 2026-10 花名册改版：**改派整段退出这一页**（design §1.6 / §12.6）。
 *
 * 原来这一页能写排班的单日覆盖（「改今天 / 撤销回规则」+ 班次与工作区两个下拉），
 * 改版后这件事只有排班月历一个入口 —— 花名册只管档案与批准。所以这一组从"断言它写了"
 * **反过来**：页面上不再有排班的任何读写，也不再消费花名册返回里的 `zones`。
 */
describe('花名册不再碰排班（2026-10 改版）', () => {
  it('不读排班的任何一条接口，也不写单日覆盖', () => {
    expect(roster).not.toMatch(/\/api\/scheduling\//)
    expect(roster).not.toMatch(/overrides/)
    // 那套"档位 → 工作区候选"的派生（`duty_slot`、`zonesForShift`）整组随之下线。
    expect(roster).not.toMatch(/DUTY_SLOT_SHIFTS/)
    expect(roster).not.toMatch(/zonesForShift|zoneChoicesForShift|defaultShiftIdFor|onShiftChange/)
    expect(roster).not.toMatch(/loadSchedule|scheduleShifts|scheduleError|dutyToday/)
  })

  it('只读花名册这一条读接口，一次（不再有 RTT 链）', () => {
    const gets = roster.match(/api\.get\(/g) || []
    expect(gets).toHaveLength(1)
    expect(roster).toMatch(/api\.get\('\/api\/hygiene\/admin\/roster'\)/)
    // 返回里的 `zones` 不消费：接口保留该键由后端定，这一页不看它。
    expect(roster).not.toMatch(/\bzones\b/)
  })

  it('实时只订 roster：不再收 assignment（没有当天的编辑动作了）', () => {
    expect(roster).toMatch(/resources: \['roster'\]/)
    expect(roster).not.toMatch(/resources: \['roster', 'assignment'\]/)
  })

  it('列表行与抽屉里都没有当天的班次、工作区，也没有那两个按钮', () => {
    // 列表行里搜不到这四个词；抽屉里搜得到前两个（身份证号 / 底薪）、搜不到后两个。
    expect(roster).not.toContain('当天班次')
    expect(roster).not.toContain('当天区域')
    expect(roster).not.toMatch(/改今天|撤销回规则/)
    expect(roster).not.toMatch(/换班次/)
    // 行内没有任何动作按钮（批准也要进抽屉）：整行就是一个 `<button>`。
    expect(roster).toMatch(/class="hy-person roster-row"/)
    expect(roster).toMatch(/@click="openDrawer\(row\)"/)
  })
})
