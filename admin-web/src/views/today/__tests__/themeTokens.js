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
