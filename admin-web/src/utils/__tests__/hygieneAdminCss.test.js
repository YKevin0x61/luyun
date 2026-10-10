import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CANONICAL = join(here, '../../../public/hygiene-admin.css')
const FALLBACK = join(here, '../../../../public/hygiene-admin.css')
const BUILT = join(here, '../../../dist/hygiene-admin.css')
const CSS_FILES = {
  'admin-web/public/hygiene-admin.css': CANONICAL,
  'public/hygiene-admin.css': FALLBACK,
}

describe('卫生样式只有一份来源（防漂移）', () => {
  it('keeps the repo-root fallback byte-identical to the canonical copy', () => {
    // 根 public/ 那份是 main.py 在 admin-web/dist 缺失时的回落目标。两份曾经分叉成
    // 「深色验收台」与「浅色 porcelain」两套相反主题，而契约测试只断言 8 个字面量，
    // 于是漂移一路绿着过——一旦真回落就是浅底浅字、拍照页几乎不可读。
    expect(readFileSync(FALLBACK, 'utf8')).toBe(readFileSync(CANONICAL, 'utf8'))
  })

  it('keeps an existing build in sync with the source', () => {
    // 第三份：构建产物。main.py 优先服务 admin-web/dist，改了源文件却没重新构建时，
    // 线上拿到的还是旧主题——只比两份源文件的测试抓不到这种情况。
    // 干净 checkout（CI 的 Python job、未构建的开发机）没有 dist，跳过。
    if (!existsSync(BUILT)) return
    expect(readFileSync(BUILT, 'utf8')).toBe(readFileSync(CANONICAL, 'utf8'))
  })
})

// ── recipe.css 的第二份来历（t48）────────────────────────────────────────────
// 根 `public/recipe.css` 与 `public/hygiene-admin.css` 同源：**main.py 在 `admin-web/dist` 缺失时
// 回落到它们**，所以根 public/ 不是垃圾文件，删掉 = 回落路径没有样式。
// 它曾经与 canonical 分叉（根 `d51d4bf3…` vs canonical `51b32e65…`）而**没有任何守卫**盯着 ——
// t45 的打印复位只落在 canonical 上时，**回落路径上的打印态会静默失效**（照印深色底 + 颗粒层）。
// 这里照 hygiene-admin.css 那把尺子，把三份（根回落 / canonical / dist）都钉住。
const RECIPE_CANONICAL = join(here, '../../../public/recipe.css')
const RECIPE_FALLBACK = join(here, '../../../../public/recipe.css')
const RECIPE_BUILT = join(here, '../../../dist/recipe.css')

describe('配方样式只有一份来源（防漂移 · t48）', () => {
  it('keeps the repo-root recipe.css fallback byte-identical to the canonical copy', () => {
    expect(readFileSync(RECIPE_FALLBACK, 'utf8')).toBe(readFileSync(RECIPE_CANONICAL, 'utf8'))
  })

  it('keeps an existing recipe.css build in sync with the source', () => {
    if (!existsSync(RECIPE_BUILT)) return
    expect(readFileSync(RECIPE_BUILT, 'utf8')).toBe(readFileSync(RECIPE_CANONICAL, 'utf8'))
  })

  it('keeps the shell-owned print reset out of recipe.css（③-1b：白底/隐藏 chrome 归壳）', () => {
    const css = readFileSync(RECIPE_CANONICAL, 'utf8')
    expect(css).not.toContain('.site-bg')        // 全仓从未定义的死规则
    expect(css).not.toMatch(/z-index:\s*40\b/)  // 页内 sticky 压壳顶栏的那条
  })
})

const THEME = join(here, '../../../src/styles/theme.workbench.css')

describe('工作台令牌层（③-1a 上移到 html 级；t47 换名 data-workbench 避开配方页的 data-theme）', () => {
  const theme = readFileSync(THEME, 'utf8')

  it('keeps the token layer on html[data-workbench]（不是子树 class，也不用配方页占着的 data-theme）', () => {
    // 现场 8 页（/workbench/floor/*）的页面根**不带** .hygiene-admin/.hygiene-staff —— 令牌若只挂在
    // 类上，417 处 var(--hy-*) / 别名引用会静默失效（掉色，不报错）。所以第 2 段必须是 html 级选择器。
    expect(theme).toContain('html[data-workbench] {')
    const block = theme.slice(theme.indexOf('html[data-workbench] {'))
    expect(block).toContain('--hy-pool: #0f6f78')
    expect(block).toContain('--hy-bg: #0a1719')
    expect(block).toContain('--surface-0:#0a1719')
    // 兼容别名也要在同一个块里（现场页与 recipe 视图此刻还在用这些名字）
    expect(block).toMatch(/--bg:\s*var\(--hy-bg\)/)
    expect(block).toMatch(/--accent:\s*var\(--hy-mint\)/)
    // 派生色写死 rgba，不用 color-mix（老 WebView 静默透明）。
    // 判据只看**声明**：注释里写着「不用 color-mix()」这句话本身不能把守卫弄红（t46 实测踩过）。
    const declsOnly = block.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(declsOnly).not.toContain('color-mix')
  })

  it('leaves the two shell classes with layout only（不再带色）', () => {
    for (const [label, path] of Object.entries(CSS_FILES)) {
      const css = readFileSync(path, 'utf8')
      const start = css.indexOf('.hygiene-admin,')
      const end = css.indexOf('\n}', start)
      const block = css.slice(start, end)
      expect(block.length, label).toBeGreaterThan(0)
      expect(block, `${label} 的壳类块不该再有 --hy-* 定义`).not.toMatch(/--hy-[a-z0-9-]+\s*:/)
      expect(block, `${label} 的壳类块不该再有色别名`).not.toMatch(/--(bg|text|text-dim|border|card|card2|accent|green|red|yellow|cyan|blue|pink)\s*:/)
    }
  })
})

describe.each(Object.entries(CSS_FILES))('%s hygiene admin chrome', (_label, path) => {
  const css = readFileSync(path, 'utf8')

  it('keeps chrome scoped to the hygiene admin shell（令牌自 ③-1a 起在 html 级，见下一个 describe）', () => {
    expect(css).toContain('.hygiene-admin,')
    expect(css).toContain('.hygiene-staff {')
    expect(css).toContain('.hygiene-admin .hy-tabbar')
    expect(css).toContain('.hygiene-work .hy-tabbar')
    expect(css).toContain('.hygiene-admin .hy-tab.router-link-active')
    expect(css).toContain('.hygiene-staff .hy-staff-card')
    expect(css).toContain('prefers-reduced-motion')
  })

  it('keeps picker geometry and v14 datepicker class names intact', () => {
    // The time wheel centers its items through the base stylesheet's column
    // padding, which is derived from --luyun-time-visible-rows; overriding it
    // puts the band on the wrong item.
    expect(css).not.toContain('.hygiene-admin .luyun-time-picker__column')
    expect(css).not.toMatch(/dp__/)
    expect(css).not.toMatch(/\.luyun-date-picker \.dp__input\b/)
    if (css.includes('.hygiene-admin .luyun-time-picker__popover')) {
      // 承载逾期点的卡片带 backdrop-filter，会形成层叠上下文；这张卡必须抬起来，
      // 否则弹窗会被后面的卡片盖住。
      expect(css).toMatch(/\.hygiene-admin \.clocks-card \{[\s\S]*?z-index: 30/)
    }
  })
})
