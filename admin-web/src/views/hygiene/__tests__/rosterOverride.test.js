import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const roster = readFileSync(join(here, '../HygieneRosterView.vue'), 'utf8')

describe('台账上的「改派」写的是排班的单日覆盖（票 10）', () => {
  it('writes today as a scheduling override, and can take it back', () => {
    // 卫生那条改派接口（POST /api/hygiene/admin/roster/{id}/assignment）票 10 起固定 403：
    // 今天上哪个班、在哪个区由**排班结果**决定，卫生不 import 排班。所以台账直接写排班的
    // 单日覆盖，撤销走同一条路径的 DELETE。
    expect(roster).toMatch(
      /api\.put\(`\/api\/scheduling\/overrides\/\$\{row\.id\}\/\$\{today\.value\}`/,
    )
    expect(roster).toMatch(
      /api\.delete\(`\/api\/scheduling\/overrides\/\$\{row\.id\}\/\$\{today\.value\}`\)/,
    )
    expect(roster).not.toMatch(
      /api\.post\(`\/api\/hygiene\/admin\/roster\/\$\{row\.id\}\/assignment`/,
    )
    // 覆盖是**整天的快照**：`zone_id: null` = 跟这个班次的固定区（不再替人挑一个区）；
    // 「那天休」走 `is_rest`，班次和责任区都得留空（带细节的休服务端会拒）。
    expect(roster).toMatch(/zone_id: draft\.zone_id === '' \? null : Number\(draft\.zone_id\)/)
    expect(roster).toMatch(/is_rest: true, shift_id: null, zone_id: null/)
    expect(roster).toMatch(/<option value="rest">休（这天不上班）<\/option>/)
    // 成功之后重读花名册：台账上「现在 白班 / 现在 案板」两列来自排班结果，不重读还是旧的。
    expect(roster).toMatch(
      /await api\.put\(`\/api\/scheduling\/overrides\/[\s\S]{0,200}?await loadRoster\(\)/,
    )
  })

  it('takes today from the scheduling business day, never from the browser clock', () => {
    // 营业日 06:00 切、按东八区算：只有服务端那一份对（跟 SchedulingCalendarView 同一口径）。
    expect(roster).toMatch(/\/api\/scheduling\/calendar/)
    expect(roster).toMatch(/today\.value = \(calendar && calendar\.today\) \|\| ''/)
    // 本机时间只用来挑「问哪个月」（`/calendar` 的必填参数，`today` 跟它无关），
    // 日期本身绝不自己拼：没有本地日期字符串，也没有手写的 06:00 切点。
    expect(roster).toMatch(/function currentMonthValue\(\)/)
    expect(roster).not.toMatch(/toISOString\(\)\.slice/)
    expect(roster).not.toMatch(/setHours\(|getHours\(\)/)
    // 「改今天」那条路上的日期只有 `today.value` 一份。
    const overridePaths = roster.match(/overrides\/\$\{row\.id\}\/\$\{[^}]+\}/g) || []
    expect(overridePaths.length).toBeGreaterThan(0)
    expect(overridePaths.every((path) => path.endsWith('/${today.value}'))).toBe(true)
  })

  it('offers the scheduling shifts, and duty_slot picks the zone day/night switch', () => {
    expect(roster).toMatch(/api\.get\('\/api\/scheduling\/shifts'\)/)
    expect(roster).toMatch(/v-for="shift in scheduleShifts"/)
    // 选的是排班那份班次表（只出还在用的：停用的服务端会拒），不再是写死的白班/夜班。
    expect(roster).not.toMatch(/v-for="shift in HYGIENE_SHIFTS"/)
    // 责任区候选：`duty_slot`（day/night）→ 卫生的「白班/夜班」→ `zonesForShift` 那套
    // `day_shift` / `night_shift` 开关；没标档位的班次不筛（列出全部）。
    expect(roster).toMatch(/const DUTY_SLOT_SHIFTS = \{ day: '白班', night: '夜班' \}/)
    expect(roster).toMatch(/const slot = dutyShiftOf\(shiftId\)/)
    expect(roster).toMatch(/return slot \? zonesForShift\(slot\) : zones\.value/)
    expect(roster).toMatch(/v-for="zone in zoneChoicesForShift\(drafts\[row\.id\]\.shift\)"/)
    // 选「休」的人不给挑区。
    expect(roster).toMatch(
      /:disabled="busyId === row\.id \|\| drafts\[row\.id\]\.shift === 'rest'"/,
    )
    // 没选班次（今天没排到、那条班次没标档位）就不给提交：别拿一个空班次去写覆盖。
    expect(roster).toMatch(
      /:disabled="busyId === row\.id \|\| !today \|\| !drafts\[row\.id\]\.shift"/,
    )
  })

  it('shows the server sentence when the write is refused, and keeps the roster readable', () => {
    // 400 的中文原话直接摆出来（已经过去的日子改不了、这天还没排到、班次停用…），别吞。
    expect(roster).toMatch(/errorText\.value = err\.message \|\| '改今天失败'/)
    expect(roster).toMatch(/errorText\.value = err\.message \|\| '撤销失败'/)
    // 排班读不出来只说这一件事，不把整张员工表换成「加载失败」：批准/停用/改名跟它无关。
    expect(roster).toMatch(
      /scheduleError\.value = err\.message \|\| '排班读不出来，今天暂时改不了'/,
    )
    expect(roster).toMatch(/v-if="scheduleError" class="roster-assign-hint roster-assign-error"/)
    // 读不到营业日就不给按。
    expect(roster).toMatch(/:disabled="busyId === row\.id \|\| !today"/)
  })

  it('预填先问今天的当天名单，问不到才退回按档位配对', () => {
    // 花名册那一行只给**卫生的档位**（白班/夜班），而排班同一档可能有好几条班次
    //（白班档既有「早班」也有「白班」）—— 按档位配对会配错人，管理员一提交就把人
    // 换到另一条班次上去了。当天名单（`/api/scheduling/day`）才是权威。
    expect(roster).toMatch(/api\.get\('\/api\/scheduling\/day', \{ date: today\.value \}\)/)
    expect(roster).toMatch(/function dutyMapFromDay/)
    expect(roster).toMatch(/const exact = dutyToday\.value\[row\.id\]/)
    expect(roster).toMatch(/defaultShiftIdFor\(row\)/)
  })
})
