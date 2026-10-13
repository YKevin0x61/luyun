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
//
// ── 两份「不许出现 X」的源码断言与「用源码断行为」的界线（ADR 0106 Consequences） ──
// 本文件里 `z` 这类断言（以及 `shellMobileChrome.test.js` 里的 safe-area / 滚动容器那几条）
// 是**有意为之的源码断言**：它们盯的是「一个数字/一个名字有没有被写进源码」这种**能被文本
// 判定、且判定即结论**的事实 —— 写没写 `z-index: 30` 不需要跑起来看。这跟「拿源码文本去
// 断**行为**」（例如「这一页滚得动」「这个弹层盖得住」）是两回事：后者必须靠浏览器里量
// （`baseline/checks-05/` 那套探针）。两类混起来写，下一个维护者就会以为文本断言能替代
// 真机核对，或者反过来把这类断言当反模式清掉。
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const DEBT = JSON.parse(readFileSync('tokenDebt.json', 'utf8'))
const THEME = readFileSync('src/styles/theme.workbench.css', 'utf8')
const THEME_ADMIN = readFileSync('src/styles/theme.css', 'utf8')
const BP_OK = new Set(['430', '560', '720', '900'])
const SKIP = /(__tests__|node_modules|dist)/

// ══════════════════════════════════════════════════════════════════════════════
// 层级档位清单（票 05 / ADR 0106 决策 6）——**归类依据就写在这里，别处只会引用这一份**
//
// 为什么需要这张表：层级曾经只存在于人脑里（`src/` 里 45 处、`hygiene-admin.css` 里 9 处
// 直接写数字），同一屏里谁能压住谁要靠读注释推。归类的判据是**「这一层和内容的关系」**，
// 不是「它现在的数字是多少」：
//
//   · 页内底栏（tabbar）——固定在视口底那条**属于内容区**的栏。它必须在壳 chrome 之下：
//     手机页头自己建了层叠上下文，它的下拉遮罩再高也出不了那一层，底栏一抬到同档就会反过来
//     盖住遮罩（菜单打开时底栏不被压暗、还能点穿）。
//   · 页 chrome（shell）——**滚动时留在原地、但不接管输入**。它们和页面内容在同一棵树里，
//     只要求「在内容之上」，不要求「在文档流之外」。
//   · 遮罩（scrim）——**盖住页面、等用户先处理它**的半透明层，以及贴在它上面的浮层
//     （页头下拉面板、时间浮层、页内浮层的遮罩）。它必须在壳 chrome 之上：壳的顶栏与
//     底栏要能被它盖住，否则手机上点得到底下的导航（`135b037` 就是被反着踩的）。
//   · 弹层（dialog）——用户主动打开的**阻塞面**：确认框、抽屉、拍摄 / 对照弹层、
//     「先下载标准图」那张管理面板。都在遮罩之上。
//   · 提示（toast）——**非阻塞**的通知与浮层：更新提示条、照片上传队列条；以及
//     「一层还要再压一层」的那类（窄屏筛选抽屉压住自己的遮罩）。
//   · 队列（queue）——**顶档**：全站提示、整屏只留一件事的查看器（图片灯箱）、
//     叠在抽屉之上的确认框、以及页面里那条 `fixed` 的动作条（排班笔栏）。
//
// 三条硬约束（改这张表之前先看这里）：
//   1. **六档的值必须严格递增**，且最低一档 > 0（要高出 `auto` 那层）。下面「档位清单」会断。
//   2. **`--z-tabbar` 是唯一一处「按 ADR 0106 决策 6 的字面」多出来的名字**：那五档
//      （shell / scrim / dialog / toast / queue）表达不了「比壳 chrome 更低、又比内容更高」。
//      它不新开档位语义，只是底栏那一处的**次序**没法用五档写出来又不能改（见上面第一条）。
//      真要合并，得先改 ADR 0106 决策 6 / ADR 0104 的两层结构。
//   3. **后台页也要有同一份**：`theme.css` 的 `:root` 与工作台令牌层同值。`LogsView` /
//      `DataTable` / `SetupView` / `PwaUpdateBanner` 这些两边都用的组件也写语义名，
//      少了后台那份，它们会解析成 `z-index: auto`（弹窗静默掉到内容下面）。
//
// 换写法不许改次序：票 05 逐处核对过「同一层叠上下文里的相对次序」，改动只发生在
// **同一个 context 内、彼此不可能同屏**的层之间（例如钉住的表头列 2/3 → `--z-shell`，
// 与页面粘住的页头 30 不同屏）。真机核对见 `.scratch/workbench-content-area/baseline/checks-05/`。
//
// **票 11 的更正（2026-10-14）**：上面那个前提有两个反例 —— 钉住的表头列 2/3 恰恰**同屏**
// （表头 sticky 浮起来时正好压在表体行上），拍摄弹层 60 与上传队列条 55 也同屏。所以从这一票起
// 加一条硬规矩：**同屏的层之间必须严格分先后，不许同档靠 DOM 次序**；档位表里没有的位置，
// 用 `calc(var(--z-x) + N)` 从令牌派生（`N` = 改动前那个数字与档位的差），并在该处注释里写明原值。
// 今天共有五处派生：拍摄/对照弹层 60、两个后台抽屉 60、确认框 100、页内时间浮层 120、更新提示条 201。
// 真机核对见 `.scratch/workbench-content-area/baseline/checks-11/`。
// ══════════════════════════════════════════════════════════════════════════════
const LAYERS = [
  ['--z-tabbar', 15, '页内底栏：`.wb-tabbar` / 共享表的 `.hy-tabbar`（在壳 chrome 之下）'],
  ['--z-shell', 20, '页 chrome：壳的顶栏 / 手机页头 / 底栏、页内粘住的页头、钉住的表头列、面板内的抬升'],
  ['--z-scrim', 40, '遮罩：页头下拉的遮罩与面板、页内浮层的遮罩、时间浮层、首次下载提示'],
  ['--z-dialog', 55, '弹层：确认框、抽屉、拍摄 / 对照弹层、标准图管理面板、上传队列条'],
  ['--z-toast', 100, '提示：非阻塞的通知与浮层。**票 11 起这一档暂时没有点名使用者** —— 原先归它的两个抽屉（`.dt-sidebar` / `.logs-sidebar`）要压住自己的遮罩、又要被确认框压住，落在 55 与 100 之间，改从 `--z-dialog` 派生 60；档位本身保留（ADR 0106 决策 6 点名过它），语义没变'],
  ['--z-queue', 200, '队列：跳转链接、图片灯箱、抽屉之上的确认框、排班笔栏、设置页遮罩'],
]
const LAYER_NAMES = LAYERS.map(([name]) => name)
// 裸 `z-index` **数字** —— 令牌引用（`var(--z-…)`）与 `auto` 都不算。**不含负数**：
// 全仓唯一一处负数在共享样式表的 `.hygiene-admin::before`（纸面颗粒层 `-1`），它必须待在
// 内容之下；这两份令牌层里没有负数档，硬塞任何名字都会把它抬到内容上面（颗粒会糊在字上）。
// 那一条由下面「纸面颗粒层」那条用例单独盯着（它要求它**仍在**，方向与这条相反）。
// 注释里的写法约定：提到数字时加反引号（`` `z-index: 30` ``）—— 反引号后面的字符不是数字，
// 于是「注释里追述旧值」不会被这条算进去（`z-index:30` 这种**声明**式写法一律会被算到）。
const BARE_Z = /z-index:[ \t]*(?<!-)\d+/g
// 上面那条负向后行断言 `(?<!-)` 是给 `z-index: -1` 留的口子：`[ \t]*` 是贪婪的，
// 只写 `\d+` 会在 `-1` 的 `1` 上匹配成功。

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { if (!SKIP.test(p)) walk(p, out) }
    else if (/\.(vue|js|css)$/.test(name) && !SKIP.test(p)) out.push(p)
  }
  return out
}

const DEF = new Set()
// 三份令牌层都要读：`theme.workbench.css`（工作台）/ `theme.css`（后台，票 05 起也定义五档
// 层级）/ `hygiene-admin.css`（共享样式表里的存量）。少一份，那套主题里的合法引用会被误报。
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
                    rgba: count(src, /\brgba?\(/g), 'style=': count(src, /style="/g), bp: bp.length,
                    z: count(stripComments(src), BARE_Z) }
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

// ══════════════════════════════════════════════════════════════════════════════
// 设计护栏 · 层级（票 05 / ADR 0106 决策 6）
//
// 本节的两条「不许出现 X」都是**源码断言**（文本判定即结论），不是拿文本去断行为：
// 「这里有没有写一个裸数字」与「这些层在屏幕上谁压住谁」是两件事，后者在
// `.scratch/workbench-content-area/checks-05/` 那套浏览器探针里量。界线的完整说法见文件头。
// ══════════════════════════════════════════════════════════════════════════════
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '')
// 取某条选择器的规则体。**不用正则跨选择器列表**：那样会在 `.a, .hygiene-admin .clocks-card {`
// 这种同名前缀上匹配到别人的块（`.hygiene-admin .clocks-card` 就有一条媒体查询里的同名前缀）。
// 这里按索引走：找到选择器 → 往后找第一个 `{` → 往前退到上一条声明的末尾 → 取到配对的 `}`。
const ruleOf = (css, selector) => {
  const at = css.indexOf(selector)
  if (at === -1) return null
  const open = css.indexOf('{', at)
  if (open === -1) return null
  const close = css.indexOf('}', open)
  if (close === -1) return null
  return css.slice(open + 1, close)
}
const layerValue = (src, name) => {
  const m = src.match(new RegExp(`${name}\\s*:\\s*(-?\\d+)`))
  return m ? Number(m[1]) : null
}

describe('设计护栏 · 层级档位（票 05）', () => {
  it('档位表里的每一档在两套主题里都有定义、值与清单一致，且严格递增、最低一档 > 0', () => {
    const values = {}
    for (const [name, value] of LAYERS) {
      for (const [label, src] of [['theme.workbench.css', THEME], ['theme.css', THEME_ADMIN]]) {
        expect(layerValue(src, name), `${label} 缺 ${name}（两套主题都要回答这些档）`).toBe(value)
      }
      values[name] = value
    }
    const nums = LAYER_NAMES.map((n) => values[n])
    expect(nums, `档位必须严格递增：${LAYER_NAMES.join(' < ')}`).toEqual([...nums].sort((a, b) => a - b))
    expect(new Set(nums).size, '档位不许有重复值（同一档就是同一档，别再分先后）').toBe(nums.length)
    expect(nums[0], '最低一档必须 > 0：它得高出 auto 那一层').toBeGreaterThan(0)
    // ADR 0106 决策 6 点名的五个名字一个都不许少（`--z-tabbar` 是那一处写不进五档的次序）
    for (const must of ['--z-shell', '--z-scrim', '--z-dialog', '--z-toast', '--z-queue']) {
      expect(LAYER_NAMES, `ADR 0106 决策 6 点名的 ${must} 必须在档位表里`).toContain(must)
    }
  })

  it('`src/` 里不再有裸 `z-index` 数字，全部走档位表里的语义名', () => {
    const bad = []
    for (const [path, src] of SOURCES) {
      for (const m of stripComments(src).matchAll(BARE_Z)) {
        bad.push(`${path}：${m[0]}`)
      }
    }
    expect(bad, `层级只准写 ${LAYER_NAMES.map((n) => `var(${n})`).join(' / ')}；`
      + '归类依据见本文件顶部的档位清单').toEqual([])
  })

  it('棘轮：每份文件里裸 z-index 数字的条数都不超过快照（这两项的计数今天已归零）', () => {
    const over = []
    for (const [path, src] of SOURCES) {
      const n = count(stripComments(src), BARE_Z)
      const pinned = (DEBT.files[path] && DEBT.files[path].z) || 0
      if (n > pinned) over.push(`${path}：${n} > 快照 ${pinned}`)
    }
    expect(over, '只减不增：新增一处裸 z-index 就会红。重出快照：'
      + 'node src/styles/__tests__/makeTokenDebt.mjs（本票只手改了 z 这一列）').toEqual([])
  })
})

describe('设计护栏 · 共享样式表 hygiene-admin.css 的层级（票 05）', () => {
  // 两份物理副本（构建期 `public/` 复制到 `dist/`，根 `public/` 是 main.py 的回落目标）；
  // 逐字节相等由 `src/utils/__tests__/hygieneAdminCss.test.js` 盯着，这里只管层级。
  const COPIES = ['public/hygiene-admin.css', '../public/hygiene-admin.css']

  it('不再有裸 `z-index` 数字（唯一的例外是纸面颗粒层那条 `-1`，见下面一条）', () => {
    const bad = []
    for (const rel of COPIES) {
      const src = stripComments(readFileSync(rel, 'utf8'))
      for (const m of src.matchAll(BARE_Z)) bad.push(`${rel}：${m[0]}`)
    }
    expect(bad, `共享样式表的层级也走档位表：${LAYER_NAMES.map((n) => `var(${n})`).join(' / ')}`)
      .toEqual([])
  })

  it('纸面颗粒层那条 `-1` 仍在：档位表里没有负数档，硬塞名字会把颗粒抬到字上', () => {
    for (const rel of COPIES) {
      const src = stripComments(readFileSync(rel, 'utf8'))
      const rule = src.match(/\.hygiene-admin::before,[\s\S]*?\}/)
      expect(rule, `${rel} 里颗粒层规则还在`).toBeTruthy()
      expect(rule[0]).toMatch(/z-index:\s*-1/)
    }
  })

  it('挡在壳层之上的那些层（页内 sticky / 弹层 / 遮罩）都在名单里', () => {
    const src = stripComments(readFileSync('public/hygiene-admin.css', 'utf8'))
    // 值 = 该规则里 `z-index:` 后面**一字不差**的表达式（票 11 起允许从令牌派生 ——
    // 拍摄/对照弹层必须严格高于同一档的队列条，六档里没有那个位置）。
    const expect_ = {
      '.hygiene-admin .hy-skip': 'var(--z-queue)',
      '.hygiene-admin .hy-header': 'var(--z-shell)',
      '.hygiene-admin .clocks-card': 'var(--z-shell)',
      '.hygiene-admin .luyun-time-picker__popover': 'var(--z-scrim)',
      '.hygiene-work .hy-work-header': 'var(--z-shell)',
      '.hygiene-work .staff-preview': 'calc(var(--z-dialog) + 5)',
    }
    for (const [selector, expr] of Object.entries(expect_)) {
      const rule = ruleOf(src, selector)
      expect(rule, `${selector} 这条规则还在`).toBeTruthy()
      expect(rule, `${selector} 该走 ${expr}`).toContain(`z-index: ${expr}`)
    }
  })

  it('两条页内底栏走 `--z-tabbar`（比壳 chrome 低一档的那条次序）', () => {
    const src = stripComments(readFileSync('public/hygiene-admin.css', 'utf8'))
    for (const sel of ['.hygiene-admin .hy-tabbar', '.hygiene-work .hy-tabbar']) {
      const rule = ruleOf(src, sel)
      expect(rule, `${sel} 这条规则还在`).toBeTruthy()
      expect(rule, `${sel} 该走 --z-tabbar`).toContain('z-index: var(--z-tabbar)')
    }
  })
})
