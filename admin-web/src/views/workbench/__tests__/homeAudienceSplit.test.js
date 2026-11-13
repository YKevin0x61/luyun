// @vitest-environment node
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * 工作台首页的两个档位（③-3c-4 / t55）—— 钉住「员工只看得到员工那三块」。
 *
 * 背景：t54 交回这一项时的担心是**权限面问题**（员工访问 `/workbench` 会不会看到
 * 待批请假 / 逾期整改这类管理信息）。核实结论：**文件本身已有身份分支** ——
 * `load()` 里 `identity === IDENTITY_ADMIN ? loadManager() : loadStaff()`，
 * 模板里 `summary.view === IDENTITY_ADMIN` 与员工档 `v-else` 两套渲染。
 * 所以这不是权限缺口，而是「结构已对、判据没被钉住」。本文件把结构钉住。
 */
const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '../WorkbenchHomeView.vue'), 'utf8')

const ADMIN_AT = src.indexOf('summary.view === IDENTITY_ADMIN')
// 员工档的锚点用**它自己的段注释**（`<!-- ── 员工：我的班次 / 工作区 / 待办 ──`）——
// 用文案反查会踩到文件头的说明注释（`我的班` 在注释里也出现过，t55 实测踩过两次）。
const STAFF_SECTION = src.indexOf('员工：我的班次')
const STAFF_AT = src.indexOf('<template v-else>', STAFF_SECTION)
const STAFF_END = src.indexOf('</template>', src.indexOf('等我回应'))

describe('首页按身份分两档（不是权限缺口）', () => {
  it('加载与渲染都按身份分流', () => {
    expect(src).toMatch(/if \(identity\.value === IDENTITY_ADMIN\) await loadManager\(\)/)
    expect(ADMIN_AT, '没有管理档分支').toBeGreaterThan(-1)
    expect(STAFF_SECTION, '没有员工段注释').toBeGreaterThan(ADMIN_AT)
    expect(STAFF_AT, '没有员工档分支').toBeGreaterThan(STAFF_SECTION)
  })

  it('管理档的四块还在（别在改员工档时误删）', () => {
    const admin = src.slice(ADMIN_AT, STAFF_AT)
    for (const label of ['今天谁上班', '待批请假', '待验收', '逾期整改']) {
      expect(admin, `管理档缺少「${label}」`).toContain(label)
    }
  })
})

// 管理员专属「店里的事」条（2026-11-09 走查新增；2026-11-10 增加卫生复核那一行）：
// 员工身份 + 能力档（schedule / 卫生复核任一项）才渲染，`teamSummary` 与 `hygieneSummary`
// 在能力不足时都返回 null。普通员工的结构扫描要先把它剥掉。
const MANAGER_TEAM = /<section v-if="teamSummary \|\| hygieneSummary" class="wbh-team"[\s\S]*?<\/section>/

describe('员工档只渲染：我的班 + 待办 + 等我回应', () => {
  const staff = src.slice(STAFF_AT, STAFF_END)

  it('三块齐全', () => {
    expect(staff).toContain('我的班')
    expect(staff).toContain('我的待办')
    expect(staff).toContain('等我回应')
  })

  it('管理员「店里的事」条有 schedule 能力闸门', () => {
    expect(staff, '员工档缺少管理员条（有排班权限的管理员看不到待批/上班人数/备货）').toMatch(MANAGER_TEAM)
    expect(src, '管理员条没有按 manage 能力闸门短路').toMatch(/if \(!manage\.value\) return null/)
    expect(src, 'manage 没有绑定 schedule 能力').toMatch(/staffCaps\.includes\('schedule'\)/)
  })

  it('普通员工档一个管理信息都不出现（权限面的判据）', () => {
    // 管理员条已由 teamSummary 的 !manage 短路保证不渲染；这里扫「去掉店长条之后」的员工档，
    // 防止有人把管理 KPI 片段挪到普通员工也会渲染的位置。
    const plainStaff = staff.replace(MANAGER_TEAM, '')
    for (const leaked of ['今天谁上班', '待批请假', '待验收', '逾期整改', '人事提醒']) {
      expect(plainStaff, `员工档里出现了管理信息「${leaked}」`).not.toContain(leaked)
    }
  })

  it('写动作与批量动作一概不渲染（员工档只有导航）', () => {
    expect(staff.match(/<button/g) || []).toHaveLength(0)
    expect(staff.match(/@click/g) || []).toHaveLength(0)
    expect(staff).not.toMatch(/全选|批量|删除|停用|作废/)
  })
})

// 卫生复核那一行（2026-11-10 用户裁定）：管理员（员工会话）读不到卫生 admin 那三块
// （页面墙 401），于是用员工端已开放的只读接口补「待我复核 / 本周被驳回」。
// 这一条把三件事钉住：只对卫生复核能力的人渲染、只走员工端只读接口、失败不挡首页。
describe('管理员条里的卫生复核小条（2026-11-10 用户裁定）', () => {
  it('卫生小条只对卫生复核能力的人渲染，并指向员工端卫生页', () => {
    expect(src).toMatch(/const HYGIENE_REVIEW_CAPS = \['daily_review', 'deep_review', 'fix'\]/)
    expect(src).toMatch(/const canHygieneReview = computed\(\(\) => HYGIENE_REVIEW_CAPS\.some/)
    expect(src).toMatch(/<router-link\s+v-if="hygieneSummary"/)
    expect(
      src,
      '卫生小条没有指向员工端卫生页',
    ).toMatch(/v-if="hygieneSummary"[\s\S]{0,400}to="\/workbench\/staff\/clean"/)
    // 0 值中性灯这条口径得和排班三格一样接在 toneOn 上。
    expect(src).toMatch(/toneOn\(hygieneSummary\.review\)/)
  })

  it('只走员工端只读接口，且失败静默（不挡首页）', () => {
    expect(src).toContain('/api/hygiene/staff/daily-work?review=1')
    expect(src).toContain('/api/hygiene/staff/boards')
    // 两条都包在 safe(...) 里：读不到只让那一行画「—」，不改整页结构。
    expect(src).toMatch(/safe\(staffRequest\('\/api\/hygiene\/staff\/daily-work\?review=1'\)/)
    expect(src).toMatch(/safe\(staffRequest\('\/api\/hygiene\/staff\/boards'\)/)
    // 请求只在卫生能力闸门里发出去。
    expect(src).toMatch(/const hygiene = canHygieneReview\.value\s*\?/)
  })

  it('普通员工档去掉管理员条之后，不出现卫生复核文案', () => {
    const staff = src.slice(STAFF_AT, STAFF_END)
    const plainStaff = staff.replace(MANAGER_TEAM, '')
    for (const leaked of ['待我复核', '本周被驳回', '卫生复核']) {
      expect(plainStaff, `员工档里出现了管理信息「${leaked}」`).not.toContain(leaked)
    }
  })
})

// 2026-11-13 走查：员工档的顺序与一个非法嵌套链接。
describe('走查的版面约束', () => {
  const staff = src.slice(STAFF_AT, STAFF_END)

  it('「今天要交」（带钟点）排在「员工餐」（无截止）之前', () => {
    const hygiene = staff.indexOf('<StaffHygieneCard')
    const meal = staff.indexOf('class="wbh-meal"')
    expect(hygiene, '员工档里没有卫生卡').toBeGreaterThan(-1)
    expect(meal, '员工档里没有员工餐那一行').toBeGreaterThan(-1)
    expect(hygiene, '卫生卡被排到员工餐后面去了').toBeLessThan(meal)
  })

  it('「今天谁上班」整块是链接，里面不再套第二个链接（非法 a > a）', () => {
    const admin = src.slice(ADMIN_AT, STAFF_AT)
    expect(admin).toContain('<span class="wbh-more">月历 ›</span>')
    expect(admin).not.toMatch(/<router-link class="wbh-more"[^>]*>月历/)
  })
})
