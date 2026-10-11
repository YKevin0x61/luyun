// 设计护栏（t36 / brief §7 · 附录 B.5）：把规范变成**会红**的断言。
//
// 三条原则（写给下一个改这个文件的人）：
//  1. **存量靠棘轮、增量靠严格**：`tokenDebt.json` 把今天的违规逐文件钉成上界；
//     没列进快照的文件按 strict 处理（必须全 0）。所以「新写的代码违规会红」，
//     而存量不阻塞任何人 —— 逐票收敛，不做一次性大迁移。
//  2. **只看测得到的**：令牌/色值/内联样式/断点/触控下限都能在文本层量到；
//     渲染层的「像不像」不在这里（那要靠真机重拍）。
//  3. **判据要能说清为什么**：每条断言失败时的信息里都带"哪条规范 + 怎么修"。
//
// 注意一处易错点：令牌层的选择器是 `html[data-workbench]`（t47 从 `data-theme`
// 改过名，因为配方阅读面占用了 `data-theme`）。写 `data-theme="workbench"` 是旧名。
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const DEBT = JSON.parse(readFileSync('tokenDebt.json', 'utf8'))
const THEME = readFileSync('src/styles/theme.workbench.css', 'utf8')
const BP_OK = new Set(['430', '560', '720', '900'])
const SKIP = /(__tests__|node_modules|dist)/

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { if (!SKIP.test(p)) walk(p, out) }
    else if (/\.(vue|js|css)$/.test(name) && !SKIP.test(p)) out.push(p)
  }
  return out
}

const DEF = new Set()
for (const f of ['src/styles/theme.workbench.css', 'src/styles/theme.css', 'public/hygiene-admin.css']) {
  try { for (const m of readFileSync(f, 'utf8').matchAll(/(--[a-z0-9-]+)\s*:/g)) DEF.add(m[1]) } catch { /* 缺文件跳过 */ }
}

const count = (s, re) => (s.match(re) || []).length
const SOURCES = walk('src').sort().map((p) => [relative('.', p), readFileSync(p, 'utf8')])

describe('设计护栏 · 存量棘轮（只减不增）', () => {
  it('每个文件的 --hy-* / 裸色值 / rgba / 内联 style / 越界断点 都不超过快照', () => {
    const over = []
    for (const [path, src] of SOURCES) {
      const pinned = DEBT.files[path]
      const bp = [...src.matchAll(/@media[^{]*max-width:\s*([0-9]+)px/g)].map((m) => m[1]).filter((v) => !BP_OK.has(v))
      const now = { hy: count(src, /var\(\s*--hy-/g), hex: count(src, /#[0-9a-fA-F]{3,8}\b/g),
                    rgba: count(src, /\brgba?\(/g), 'style=': count(src, /style="/g), bp: bp.length }
      const limit = pinned || { hy: 0, hex: 0, rgba: 0, 'style=': 0, bp: 0 }   // 未列出 = strict
      for (const [k, v] of Object.entries(now)) {
        const max = limit[k] ?? 0
        if (v > max) over.push(`${path} 的 ${k}：${v} > 快照 ${max}${pinned ? '' : '（未列进 tokenDebt.json，按 strict 必须为 0）'}`)
      }
    }
    expect(over, '存量只能减不能增；确实降了就重出快照：node src/styles/__tests__/makeTokenDebt.mjs').toEqual([])
  })

  it('新写的文件按 strict 处理：未列出 = 全 0（这条就是"增量会红"）', () => {
    const unlisted = SOURCES.filter(([p]) => !DEBT.files[p])
    expect(unlisted.length, '未列进快照的文件数（它们必须全 0）').toBeGreaterThan(0)
  })
})

describe('设计护栏 · 无未定义令牌', () => {
  it('没人新引用未定义的令牌（已知存量逐文件钉在快照的 undef 里）', () => {
    const bad = []
    for (const [path, src] of SOURCES) {
      const undef = [...new Set([...src.matchAll(/var\(\s*(--[a-z0-9-]+)/g)].map((m) => m[1]).filter((n) => !DEF.has(n)))].sort()
      const pinned = (DEBT.files[path] && DEBT.files[path].undef) || []
      const added = undef.filter((n) => !pinned.includes(n))
      if (added.length) bad.push(`${path}：${added.join(' ')}`)
    }
    expect(bad, '新引用的令牌必须在令牌层里定义（真名见 theme.workbench.css）').toEqual([])
  })

  it('t1 那份最硬的证据仍在案（`--hy-mute` 是笔误，真名 `--hy-muted`）', () => {
    // 这条**故意**把已知缺陷钉在明面上：它不是"通过了就没事"，而是"知道它有、且不许再多"。
    // 修它要动 `src/views/overtime/MeOvertimeView.vue`（本票 out of scope），所以先记账。
    const overtime = DEBT.files['src/views/overtime/MeOvertimeView.vue']
    expect(overtime, '快照里该记着这个文件').toBeTruthy()
    expect(overtime.undef, 'MeOvertimeView 的未定义令牌清单').toContain('--hy-mute')
    expect(DEF.has('--hy-muted'), '真名 `--hy-muted` 必须在令牌层里定义').toBe(true)
  })
})

describe('设计护栏 · 令牌层', () => {
  it('语义层挂在 `html[data-workbench]` 上（不是旧的 data-theme）', () => {
    expect(THEME).toMatch(/html\[data-workbench\]/)
    expect(THEME, '旧名不该再出现（会与配方阅读面抢同一个属性）').not.toMatch(/html\[data-theme="workbench"\]/)
  })

  it('该块里定义 ≥20 个语义名，且规范点名的派生色/线条名都在', () => {
    const block = THEME.slice(THEME.indexOf('html[data-workbench]'))
    const names = [...block.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1])
    expect(new Set(names).size).toBeGreaterThanOrEqual(20)
    for (const must of ['--danger', '--danger-bright', '--danger-soft', '--line-control', '--line-strong',
                        '--surface-1', '--surface-2', '--surface-3']) {
      expect(names, `规范点名的 ${must} 必须在令牌层里定义`).toContain(must)
    }
  })

  it('`--danger` 不当文字色（危险色只做底色/边框，文字用它的 bright 档）', () => {
    const bad = SOURCES.filter(([, s]) => /(^|[;{\s])color:\s*var\(--danger\)\s*[;}]/m.test(s)).map(([p]) => p)
    const pinned = Object.entries(DEBT.files).filter(([, r]) => r.danger_text > 0).map(([p]) => p)
    expect(bad.filter((p) => !pinned.includes(p))).toEqual([])
  })

  it('触控下限：不许出现 <44px 的 min-height 裸值（新写的）', () => {
    const bad = []
    for (const [path, src] of SOURCES) {
      const small = [...src.matchAll(/min-height:\s*([0-9]+)px/g)].map((m) => Number(m[1])).filter((v) => v > 0 && v < 44)
      const pinned = (DEBT.files[path] && DEBT.files[path].small_tap) || 0
      if (small.length > pinned) bad.push(`${path}：${small.length} > ${pinned}`)
    }
    expect(bad).toEqual([])
  })
})
