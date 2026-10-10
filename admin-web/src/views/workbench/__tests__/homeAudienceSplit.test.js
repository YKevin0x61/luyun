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

describe('员工档只渲染：我的班 + 待办 + 等我回应', () => {
  const staff = src.slice(STAFF_AT, STAFF_END)

  it('三块齐全', () => {
    expect(staff).toContain('我的班')
    expect(staff).toContain('我的待办')
    expect(staff).toContain('等我回应')
  })

  it('一个管理信息都不出现（权限面的判据）', () => {
    for (const leaked of ['今天谁上班', '待批请假', '待验收', '逾期整改', '人事提醒']) {
      expect(staff, `员工档里出现了管理信息「${leaked}」`).not.toContain(leaked)
    }
  })

  it('写动作与批量动作一概不渲染（员工档只有导航）', () => {
    expect(staff.match(/<button/g) || []).toHaveLength(0)
    expect(staff.match(/@click/g) || []).toHaveLength(0)
    expect(staff).not.toMatch(/全选|批量|删除|停用|作废/)
  })
})
