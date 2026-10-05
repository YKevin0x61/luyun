// 主题令牌的对比度护栏（审查条目 A7 / D11 / D15）。
//
// 为什么放在这里而不是渲染里量：`.scratch/ui-audit/` 那套脚本要一个真实浏览器与一个
// 跑着的实例（审查组用的是一次性脚本，不进仓库）。单测里能做的是**最容易被改回去的
// 那一步** —— 直接把 `theme.css` 的令牌读出来、按 WCAG 公式算对比度。它挡不住
// "有人把某个组件里的颜色写死"，但挡得住"变量被改回旧值/调错"这类回归，而那正是
// A7 的问题本体（一个变量影响全站几百处文本）。
//
// 阈值按 WCAG AA 正常文本 4.5:1。
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const THEME_CSS = readFileSync('src/styles/theme.css', 'utf8')

/** 从 `:root` 块里取一个自定义属性的值（只认 `#rrggbb`，取不到就抛 —— 静默跳过
 *  等于这条护栏不存在）。 */
function rootToken(name) {
  const block = THEME_CSS.slice(THEME_CSS.indexOf(':root {'))
  const match = block.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!match) throw new Error(`theme.css 的 :root 里没有 --${name}（或不是 #rrggbb）`)
  return match[1]
}

function channels(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
}

function relativeLuminance(hex) {
  const [r, g, b] = channels(hex).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a, b) {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const AA = 4.5

describe('theme.css 的次要文字色（A7）：--text-dim 对三档底色都要过 AA', () => {
  it('对 --card2（卡片底）≥ 4.5', () => {
    expect(contrast(rootToken('text-dim'), rootToken('card2'))).toBeGreaterThanOrEqual(AA)
  })

  it('对 --card（侧栏 / 页底）≥ 4.5', () => {
    expect(contrast(rootToken('text-dim'), rootToken('card'))).toBeGreaterThanOrEqual(AA)
  })

  it('对 --bg（最暗那层）≥ 4.5', () => {
    expect(contrast(rootToken('text-dim'), rootToken('bg'))).toBeGreaterThanOrEqual(AA)
  })

  it('旧的 #6b7280 确实不达标：这条护栏不是白写的', () => {
    // 审查实测 3.34 / 3.67；这里把"旧值为什么不达标"也钉住，免得有人按旧值改回去
    // 还以为是"视觉语言保持不变"。
    expect(contrast('#6b7280', rootToken('card2'))).toBeLessThan(AA)
    expect(contrast('#6b7280', rootToken('card'))).toBeLessThan(AA)
  })
})

describe('theme.css 的铜牌色（A7 的 KPI 那一半）', () => {
  it('--rank-bronze 对 --card2 ≥ 4.5（旧的 #b45309 只有 3.21）', () => {
    expect(contrast(rootToken('rank-bronze'), rootToken('card2'))).toBeGreaterThanOrEqual(AA)
    expect(contrast('#b45309', rootToken('card2'))).toBeLessThan(AA)
  })
})
