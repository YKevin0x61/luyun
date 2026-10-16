// 员工端两个页面测试（今天 / 整月）共用的小工具：把页面源码里写的 `var(--x)`
// 解析成令牌层里的**值**。
//
// 为什么需要它（C 批返工的根因）：颜色撞车的时候，撞的往往不是**名字**而是**值** ——
// 「休半天」的 `--info` 与「请假」的 `--hy-aqua` 名字不同，值都是 `#5fd6e6`，于是
// 图例上两个色块看着是一回事。只断言「两个令牌名不同」在返工前就是绿的，等于没测。
// 判据要落在值上，而值只能从令牌层读 —— 又不能在测试里写死色值（写死了令牌一改，
// 测试就会假红，成了第二份真值）。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))

// 令牌自 t44 起分两处：语义层 + `--hy-*` 本体在 `src/styles/theme.workbench.css`，
// 兼容层与布局刻度留在 `public/hygiene-admin.css`。守卫读**两份合并**，否则「令牌搬家」
// 会被误报成「令牌未定义」（`schedulingCalendar.test.js` 同一条纪律）。
const tokens = [
  readFileSync(join(here, '../../../styles/theme.workbench.css'), 'utf8'),
  readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8'),
].join('\n')

/** 按正则从一段源码里取出一个 `var(--x)`，回令牌层查它的值。
 *  `pattern` 必须把令牌名捕成第 1 组。取不到、或令牌层里没有定义，都当场抛 ——
 *  静默返回空串会让「规则被删了」变成一条绿断言（这页原来就吃过这种亏）。 */
export function resolveVar(source, pattern) {
  const match = source.match(pattern)
  if (!match) throw new Error(`源码里没有匹配 ${pattern} 的 var(--*)`)
  const name = match[1]
  // 令牌名后面紧跟冒号才算定义：`--info` 不会命中 `--info-soft`。
  const defined = tokens.match(new RegExp(`${name}\\s*:\\s*([^;]+);`))
  if (!defined) throw new Error(`令牌层里没有定义 ${name}`)
  return { name, value: defined[1].trim() }
}

/** 把一组「态 → 该态取色的规则」一次解析成 `{ 态: {name, value} }`。
 *  `spec` 是 `[态名, 正则]` 的数组，正则同 `resolveVar`（令牌名捕成第 1 组）。
 *  某态在这页没有对应规则时当场抛 —— 这正是「规则被删了」该有的样子。 */
export function resolveStates(source, spec) {
  return Object.fromEntries(spec.map(([state, pattern]) => [state, resolveVar(source, pattern)]))
}

/** 两两不等的判据：任何两个态的**值**相同就返回一条点名说明，全不同返回 `[]`。
 *
 *  为什么是「整组两两」而不是某一对：C 批连着两轮都栽在同一个坑上 —— 只断
 *  「half ≠ leave」时，第一轮把 half 从 `--info` 改成 `--warn`（与 leave 分开了，
 *  却撞上 `--hy-amber` 的 moved），第二轮又把 `--warn` 换成 `--accent-alt`（与 moved
 *  分开了）……单对断言两轮都是绿的，因为它从没看过第三个态。判据必须覆盖**整组**，
 *  且落在**值**上（名字不同、值相同恰恰是这两轮的真身）。
 *  不在这里写死任何色值：期望值是「彼此不同」，与令牌层当前取值无关。 */
export function sameValueCollisions(resolved) {
  const byValue = new Map()
  for (const [state, { value }] of Object.entries(resolved)) {
    if (!byValue.has(value)) byValue.set(value, [])
    byValue.get(value).push(state)
  }
  return [...byValue.entries()]
    .filter(([, states]) => states.length > 1)
    .map(([value, states]) => `${states.join(' / ')} 同为 ${value}`)
}
