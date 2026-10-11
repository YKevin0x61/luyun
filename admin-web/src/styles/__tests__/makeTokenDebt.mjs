// 生成 tokenDebt.json —— 设计护栏的**存量棘轮**（t36 / brief §7 · 附录 B.5）。
//
// 口径（写死在这里，别在别处重算）：
//   * 扫描面：`src/**/*.{vue,js,css}`（不含 `__tests__/` 自己、不含 dist/node_modules）
//   * hy   = 该文件里 `var(--hy-…` 的出现次数（要逐票收敛的历史包袱）
//   * hex  = `#rrggbb` / `#rgb` 字面量（CSS 里的裸色值）
//   * rgba = `rgba(` / `rgb(` 字面量
//   * style= = 内联 `style="…"` 的出现次数
//   * bp   = `@media` 里 max-width 不在白名单 {430,560,720,900} 的次数（记下具体值）
//   * undef= `var(--x)` 里 `--x` 在令牌层/theme.css 都没定义的变量名（去重）
// **只减不增**：测试拿这份快照当上界。跑 `node src/styles/__tests__/makeTokenDebt.mjs` 重出。
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = 'src'
const BP_OK = new Set(['430', '560', '720', '900'])
const SKIP = /(__tests__|node_modules|dist)/

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) { if (!SKIP.test(p)) walk(p, out) }
    else if (/\.(vue|js|css)$/.test(name) && !SKIP.test(p)) out.push(p)
  }
  return out
}

// 已定义的令牌名：工作台令牌层 + theme.css + public 两份（只读，取 var(--x) 的定义点）
const DEF = new Set()
for (const f of ['src/styles/theme.workbench.css', 'src/styles/theme.css', 'public/hygiene-admin.css']) {
  try {
    const s = readFileSync(f, 'utf8')
    for (const m of s.matchAll(/(--[a-z0-9-]+)\s*:/g)) DEF.add(m[1])
  } catch { /* 文件不在就跳过 */ }
}

const count = (s, re) => (s.match(re) || []).length

const files = {}
for (const p of walk(ROOT).sort()) {
  const s = readFileSync(p, 'utf8')
  const bps = [...s.matchAll(/@media[^{]*max-width:\s*([0-9]+)px/g)].map((m) => m[1]).filter((v) => !BP_OK.has(v))
  const undef = [...new Set([...s.matchAll(/var\(\s*(--[a-z0-9-]+)/g)].map((m) => m[1]).filter((n) => !DEF.has(n)))].sort()
  const rec = {
    hy: count(s, /var\(\s*--hy-/g),
    hex: count(s, /#[0-9a-fA-F]{3,8}\b/g),
    rgba: count(s, /\brgba?\(/g),
    'style=': count(s, /style="/g),
    bp: bps.length,
    // 触控下限的存量（<44px 的 min-height 裸值）与「--danger 当文字色」的存量：
    // 单测里这两条也要棘轮，否则它们一上来就红，谁也推进不了。
    small_tap: (s.match(/min-height:\s*([0-9]+)px/g) || []).map((x) => Number(x.match(/([0-9]+)/)[1])).filter((v) => v > 0 && v < 44).length,
    danger_text: (s.match(/(^|[;{\s])color:\s*var\(--danger\)\s*[;}]/gm) || []).length,
  }
  if (bps.length) rec.bp_values = [...new Set(bps)].sort()
  if (undef.length) rec.undef = undef
  // 全 0 的**新**文件不进快照 —— 它们在护栏里按「未列出 = strict」处理
  if (Object.values(rec).some((v) => typeof v === 'number' && v > 0) || undef.length) files[relative('.', p)] = rec
}

const out = {
  _note: '存量棘轮（t36）。只减不增：测试拿这里当上界；没列进来的文件按 strict 处理（必须全 0）。重出：node src/styles/__tests__/makeTokenDebt.mjs',
  _scan: 'src/**/*.{vue,js,css}（跳过 __tests__/dist/node_modules）',
  _blocked: 'blocker：等用户说继续时由 captain 挂回（票面注记）',
  files,
}
writeFileSync('tokenDebt.json', JSON.stringify(out, null, 2) + '\n', 'utf8')
const tot = Object.values(files).reduce((a, r) => ({
  hy: a.hy + r.hy, hex: a.hex + r.hex, rgba: a.rgba + r.rgba, style: a.style + (r['style='] || 0), bp: a.bp + r.bp,
}), { hy: 0, hex: 0, rgba: 0, style: 0, bp: 0 })
console.log('文件数', Object.keys(files).length, '｜合计', JSON.stringify(tot))
console.log('未定义令牌的文件：')
for (const [p, r] of Object.entries(files)) if (r.undef) console.log('  ', p, r.undef.join(' '))
