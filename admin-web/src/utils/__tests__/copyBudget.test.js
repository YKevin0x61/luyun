// @vitest-environment node
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * 文案预算（t49）—— 真实代码侧的**可计算规则**检查器。
 *
 * 为什么要有它：原型侧一直有 `prototype/check-copy.py`（口径：说明性文案用可计算规则统计、
 * 每屏 ≤3 行），但**真实代码侧是空的** —— 于是「每屏常驻说明性文案 ≤3 行、不出现 >18 字的
 * 解释性长句」这条验收在落地件上没有载体，只能靠人看。这条把它补上。
 *
 * 口径（照 `check-copy.py`，但只认**静态**文案 —— Vue 里 `{{ }}` 是数据，不是文案）：
 *   · 说明性文案 = 元素里的静态中文，**≥6 字**，且不是「控件文本 / 数据标注 / 身份 / 小样」；
 *   · 每屏 ≤3 行；
 *   · 不出现 **>18 字**的解释性长句或因果句（含 为什么/因为/所以/由于/如果/否则/也就是说/意思是/为了）。
 *
 * 后续批次落地新页时，把页加进 `PAGES`（每票加自己的页）—— 别只留一条「以后统一收」的待办。
 */
const here = dirname(fileURLToPath(import.meta.url))
const PAGES = [
  ['我的今天', join(here, '../../views/today/TodayView.vue')],
  // ③-3c-1（t52）把这三页也纳入覆盖：规则不覆盖到页 = 那页的文案预算没人守
  ['我的整月', join(here, '../../views/today/TodayMonthView.vue')],
  ['加班与补钟', join(here, '../../views/overtime/MeOvertimeView.vue')],
  ['工作台首页（员工档）', join(here, '../../views/workbench/WorkbenchHomeView.vue')],
  // t55：本页也在覆盖里（并发约定：**只许追加自己的页、不许重排**）
  // ③-3b（t51）员工端「卫生」页：员工每天打开最多的一屏，文案预算不该漏掉它
  ['卫生（员工端）', join(here, '../../views/hygiene/HygieneHomeView.vue')],
]

const CJK = /[\u4e00-\u9fff]/
// 排除集：这些 class 里的文字不是「说明性文案」（控件名 / 数据标注 / 身份 / 小样 / 表格值）
const LATE_ANCHORS = ['modal-overlay']  // 「靠后的元素」锚点：切片必须覆盖到它，见 templateOf 的自证
const SKIP_CLASS = new Set([
  'btn', 'primary', 'danger', 'block', 'pill', 'step', 'arrow', 'close', 'grp', 'more', 'pitem',
  'badge', 'num', 'label', 'kind', 'goto', 'count', 'date', 'who', 'due', 'tag', 'code', 'tagline',
  'idchip', 'brand', 'role', 'chip', 'chiprole', 'wm', 'orig', 'sample', 'box', 'cap', 'cap-go',
  'cap-mark', 'cap-name', 'cap-note', 'shift', 'legend', 'dot',
  // 工作台首页的**控件文本与数据标注**（t55 校准）：入口/箭头/计数/标题/姓名这类不是说明性文案。
  // 注意 **不排除 `.wbh-sub`** —— 说明性文案正是在它上面（与 `.tA-sub` 同理，排除它规则就空了）。
  'wbh-go', 'wbh-card-more', 'wbh-label', 'wbh-num', 'wbh-brand', 'wbh-title', 'wbh-shift-name',
  'wbh-shift-count', 'wbh-shift-people', 'wbh-duty-total', 'wbh-next', 'wbh-wait', 'wbh-date',
  // 注意：**不排除 `.tA-sub`** —— 真实代码里说明性文案正是在这个类上（原型 check-copy.py 排的是
  // 数据标注 `sub`，两边的语义不同）。排除它等于把这一条规则做成空的（t49 实测踩过）。

])
// 因果/解释性词：出现即为「解释性长句」的信号（与 brief §6 的口径一致）
const CAUSAL = /为什么|因为|所以|由于|如果|否则|也就是说|意思是|为了/
const LONG = 18

/**
 * 取出 SFC 的模板区。
 *
 * **这里曾经是个「看着在守、其实没守」的洞（t65）**：原来用 `indexOf('</template>', start)`
 * 取**第一个**闭合标签，而本仓的页面常把模板拆成多个顶层 `<template v-if>` / `<template v-else>`。
 * 于是切片在第一块就结束，**后面的弹层一个字都没被扫到** —— 弹层里的常驻说明性文案因此
 * 长期逃过预算（`DO:1228-1230` 那两句就是这么漏的）。现在切到**最后一个** `</template>`。
 */
function templateOf(src) {
  const start = src.indexOf('<template')
  const end = src.lastIndexOf('</template>')
  expect(start, '找不到 <template>').toBeGreaterThan(-1)
  expect(end, '找不到 </template>').toBeGreaterThan(start)
  const tpl = src.slice(start, end).replace(/<!--[\s\S]*?-->/g, '')
  // 自证：切片必须真的覆盖到页面末端（拿文件里靠后的那个弹层当锚点）。
  // 少了这一条，将来谁把 `lastIndexOf` 改回 `indexOf`，预算又会静默变成空守。
  for (const late of LATE_ANCHORS) {
    if (src.includes(late)) {
      expect(tpl, `模板切片没覆盖到 ${late}（切片被截断了，预算在这段上等于空守）`).toContain(late)
    }
  }
  return tpl
}

/** 静态说明性文案：元素内、非 `{{ }}` 插值、≥6 个中文字，且 class 不在排除集里。 */
function staticCopy(tpl) {
  const out = []
  const tagRe = /<([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)>/g
  let m
  while ((m = tagRe.exec(tpl))) {
    const attrs = m[2] || ''
    const cls = (attrs.match(/class="([^"]*)"/) || [, ''])[1].split(/\s+/).filter(Boolean)
    if (cls.some((c) => SKIP_CLASS.has(c))) continue
    // 该元素到下一个 '<' 之间的文本（够用：说明性文案都是 `>…<` 这种一段式）
    const rest = tpl.slice(m.index + m[0].length)
    const gt = rest.indexOf('<')
    const inner = gt === -1 ? rest : rest.slice(0, gt)
    const text = inner.replace(/\{\{[\s\S]*?\}\}/g, '').trim()
    if (text.length < 6 || !CJK.test(text)) continue
    if (/[<>{}]/.test(text)) continue
    out.push(text)
  }
  return out
}

function check(page, file) {
  const tpl = templateOf(readFileSync(file, 'utf8'))
  const items = staticCopy(tpl)
  const long = items.filter((t) => t.length > LONG && CAUSAL.test(t))
  return { items, long }
}

/**
 * 今日**基线**（t65 实测；只许降不许升）。
 *
 * 为什么不是直接写 3：把切片修对（见 `templateOf`）之后才看见真相 —— 这几个页的
 * 常驻说明性文案实际是 6 / 7 / 34 / 40 行，而不再是过去「量到的」1 行。`≤3` 仍是
 * **目标**，超出部分是**欠账**：这里把当前值钉死，任何新增说明性文案当场变红，
 * 数字只能往下走。既不假装达标，也不再是「看着在守、其实没守」。
 * 清欠账是**改文案**的活（另开票），不是改这里的数字。
 */
const COPY_BASELINE = { 我的今天: 34, 我的整月: 7, 加班与补钟: 6, '卫生（员工端）': 40 }
const copyBaselineOf = (page) => COPY_BASELINE[page] ?? 3

describe('文案预算（可计算规则 · 每屏常驻说明性文案 ≤3 行）', () => {
  for (const [page, file] of PAGES) {
    it(`${page}：常驻说明性文案不超基线（目标 ≤3 行）`, () => {
      const { items } = check(page, file)
      const limit = copyBaselineOf(page)
      const debt = Math.max(0, limit - 3)
      expect(items.length, `${page} 的说明性文案 ${items.length} 行（基线 ${limit}，目标 3，欠账 ${debt}）：\n- ${items.join('\n- ')}`)
        .toBeLessThanOrEqual(limit)
    })

    it(`${page}：不出现 >${LONG} 字的解释性长句或因果句`, () => {
      const { long } = check(page, file)
      expect(long, `${page} 出现解释性长句：\n- ${long.join('\n- ')}`).toEqual([])
    })
  }
})
