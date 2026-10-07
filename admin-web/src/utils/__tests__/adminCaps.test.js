import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  ADMIN_CAPABILITIES,
  ADMIN_CAP_DEFS,
  ADMIN_CAP_LABELS,
  ADMIN_CAP_NOTES,
  ADMIN_CAP_STAFF_DEFS,
  ADMIN_CAP_SUPERVISOR_ONLY_KEYS,
  hasCap,
  keepSupervisorOnlyCaps,
  normalizeCaps,
} from '../adminCaps.js'

const here = dirname(fileURLToPath(import.meta.url))

function readView(name) {
  return readFileSync(join(here, `../../views/hygiene/${name}.vue`), 'utf8')
}

// 这十行是**契约本身**：与 `services/identity/capabilities.py` 的 `CAPABILITIES` 逐字同序。
// 在这里再抄一遍是有意的 —— 两边任何一侧改了键名或顺序，这个用例（或后端那份
// `tests/test_hygiene_admin_caps.py`）就会红，而不是等到线上判据悄悄失效。
const CONTRACT_KEYS = [
  'daily_review',
  'deep_review',
  'fix',
  'attire',
  'standard',
  'zone',
  'roster',
  'boards',
  'clock',
  'data',
]

describe('adminCaps · 前后端契约', () => {
  it('十项键名与顺序逐字等于后端 CAPABILITIES', () => {
    expect(ADMIN_CAPABILITIES).toEqual(CONTRACT_KEYS)
    expect(ADMIN_CAP_DEFS.map((item) => item.key)).toEqual(CONTRACT_KEYS)
  })

  it('可勾的三项 = 员工端有执行点的那三项，其余七项只读（两组不重不漏）', () => {
    // 这三项与后端 `STAFF_SIDE_CAPABILITIES` 同义：只有它们在员工手机端「卫生」页生效。
    expect(ADMIN_CAP_STAFF_DEFS.map((item) => item.key))
      .toEqual(['daily_review', 'deep_review', 'fix'])
    expect(ADMIN_CAP_SUPERVISOR_ONLY_KEYS)
      .toEqual(['attire', 'standard', 'zone', 'roster', 'boards', 'clock', 'data'])
    // 两组刚好分完十项：多一个少一个都会让"可勾面 == 生效面"这个前提失效。
    expect([...ADMIN_CAP_STAFF_DEFS.map((item) => item.key), ...ADMIN_CAP_SUPERVISOR_ONLY_KEYS])
      .toEqual(CONTRACT_KEYS)
  })

  it('keepSupervisorOnlyCaps 只挑只读那七项、按契约顺序、坏数据当空', () => {
    // 保存时要原样带回的正是这一组（少一个就是静默删权限）。
    expect(keepSupervisorOnlyCaps(['data', 'fix', 'boards'])).toEqual(['boards', 'data'])
    expect(keepSupervisorOnlyCaps(['daily_review', 'deep_review'])).toEqual([])
    expect(keepSupervisorOnlyCaps('not json')).toEqual([])
    expect(keepSupervisorOnlyCaps(null)).toEqual([])
  })

  it('每一项都有中文标签与一句说明（界面要用）', () => {
    for (const key of CONTRACT_KEYS) {
      expect(ADMIN_CAP_LABELS[key]).toBeTruthy()
      expect(ADMIN_CAP_NOTES[key]).toBeTruthy()
    }
    expect(ADMIN_CAP_LABELS.daily_review).toBe('日常验收')
    expect(ADMIN_CAP_NOTES.fix).toBe('开整改单、验收或驳回整改单')
  })
})

describe('normalizeCaps · 与后端 parse_caps / dump_caps 同口径', () => {
  it('只留认识的键、去重、按声明顺序（不是勾选顺序）', () => {
    expect(normalizeCaps(['fix', 'daily_review', 'fix', 'nope', '超级管理员']))
      .toEqual(['daily_review', 'fix'])
    expect(normalizeCaps(['data', 'clock'])).toEqual(['clock', 'data'])
  })

  it('坏数据一律当空（fail-closed：宁可少给一项，也不默认放行）', () => {
    expect(normalizeCaps(undefined)).toEqual([])
    expect(normalizeCaps(null)).toEqual([])
    expect(normalizeCaps('daily_review')).toEqual([])
    expect(normalizeCaps({ daily_review: true })).toEqual([])
    expect(normalizeCaps(['{bad json'])).toEqual([])
    expect(normalizeCaps([null, 42, ''])).toEqual([])
  })

  it('认后端那一列的 JSON 文本（万一某条接口把原文发出来）', () => {
    expect(normalizeCaps('["fix","daily_review"]')).toEqual(['daily_review', 'fix'])
    expect(normalizeCaps('{"fix":true}')).toEqual([])
    expect(normalizeCaps('')).toEqual([])
  })

  it('空数组与「全是认不出的键」都归一成空', () => {
    expect(normalizeCaps([])).toEqual([])
    expect(normalizeCaps(['未知'])).toEqual([])
  })
})

describe('hasCap · 页面判据的唯一入口', () => {
  it('有这一项才算有，别的项不顶替', () => {
    const caps = ['daily_review', 'fix']
    expect(hasCap(caps, 'daily_review')).toBe(true)
    expect(hasCap(caps, 'fix')).toBe(true)
    expect(hasCap(caps, 'deep_review')).toBe(false)
    // 档位字符串不是键：旧写法（permission === '管理员'）传进来必然是 false。
    expect(hasCap(caps, '管理员')).toBe(false)
    expect(hasCap(undefined, 'fix')).toBe(false)
    expect(hasCap(caps, '')).toBe(false)
  })

  it('认不出的键不会因为传了别的形状而放行', () => {
    expect(hasCap('["fix"]', 'fix')).toBe(true)
    expect(hasCap({ fix: true }, 'fix')).toBe(false)
  })
})

describe('判据收敛：两个页面只按开关判，不按 permission 档位', () => {
  // 这两条是"档位 → 开关"这件事的**回归护栏**：档位（`普通员工` / `管理员`）现在只是显示
  // 用的人话标签，判据散回页面里就是旧病（同一档的两个人被一视同仁地放行）。
  it('员工卫生页的模板里没有任何 permission 判据', () => {
    const src = readView('HygieneHomeView')
    // 只查**模板段**：脚本里的注释会引用旧写法（`permission === '管理员'`），那是留给人看的说明。
    const template = src.slice(src.indexOf('<template>'))
    expect(template).not.toMatch(/permission/)
    expect(src).toMatch(/import \{ ADMIN_CAP_STAFF_DEFS, hasCap, normalizeCaps \} from '\.\.\/\.\.\/utils\/adminCaps'/)
    // 三件事各用自己那一项开关。
    expect(src).toMatch(/hasCap\(caps\.value, 'daily_review'\)/)
    expect(src).toMatch(/hasCap\(caps\.value, 'deep_review'\)/)
    expect(src).toMatch(/hasCap\(caps\.value, 'fix'\)/)
  })

  it('花名册只把 permission 当**只读**人话标签显示（2026-10 改版：下拉与 PATCH 都撤了）', () => {
    const src = readView('HygieneRosterView')
    // 标签值仍来自服务端派生，页面只显示、不编辑。
    expect(src).toMatch(/hygienePermissionLabel\(drawerRow\.permission\)/)
    expect(src).not.toMatch(/v-model="drafts\[row\.id\]\.permission"/)
    expect(src).not.toMatch(/permission: draft\.permission/)
    // 抽屉里没有任何下拉（「卫生权限」原来是唯一一个 `<select>`）。
    expect(src).not.toMatch(/<select/)
    expect(src).not.toMatch(/permission\s*===/)
    // 可勾的那三项按契约顺序渲染；草稿进页归一化，PATCH 时把**只读七项已有的值原样带回**
    // （它们没有可勾的界面，整组替换若漏掉就是静默删权限）。
    expect(src).toMatch(/v-for="cap in ADMIN_CAP_STAFF_DEFS"/)
    expect(src).toMatch(
      /admin_caps: normalizeCaps\(\[\.\.\.source\.admin_caps, \.\.\.keepSupervisorOnlyCaps\(row\.admin_caps\)\]\)/,
    )
    expect(src).toMatch(/admin_caps: normalizeCaps\(row\.admin_caps\)/)
    // 只读那七项**整块不渲染**：连它们的定义都不引进来，名字与说明就没有出场的机会。
    expect(src).not.toMatch(/ADMIN_CAP_SUPERVISOR_ONLY/)
  })

  it('花名册行内项数只数员工端真正生效的三项（不按 admin_caps 全长度报）', () => {
    const src = readView('HygieneRosterView')
    expect(src).toMatch(
      /ADMIN_CAP_STAFF_DEFS\.filter\(\(item\) => caps\.includes\(item\.key\)\)\.length/,
    )
    expect(src).toMatch(/普通员工 · 无/)
    expect(src).toMatch(/管理员 · \$\{usable\} 项/)
  })
})
