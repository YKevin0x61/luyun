/**
 * 考勤表「净时长」的正负着色（真机走查：+2.5 / -1 / 0 全是黑字，找欠班的人只能逐个读）。
 *
 * 页面读源码式契约：三处显示净时长（待补排行 / 卡片头 / 当月净时长）都按正负挂类，
 * 颜色只走语义令牌 —— 这一页在 strict 设计护栏里，裸色值会红（designGuard.test.js）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../AttendanceSheetView.vue'), 'utf8')

describe('考勤表 · 净时长正负着色', () => {
  it('三处读数都按正负挂类（+ 绿 / − 红 / 0 灰）', () => {
    expect(view.match(/netTone\(person\.net_hours\)/g)).toHaveLength(3)
    expect(view).toMatch(/class="aRankNet" :class="netTone\(person\.net_hours\)"/)
    expect(view).toMatch(/class="aCardNet" :class="netTone\(person\.net_hours\)"/)
    expect(view).toMatch(/class="aNet" :class="netTone\(person\.net_hours\)"/)
    // 档位在脚本里判：正 / 负 / 0 三种，别把 0 也染成绿或红。
    expect(view).toMatch(/if \(value > 0\) return 'is-positive'/)
    expect(view).toMatch(/if \(value < 0\) return 'is-negative'/)
    expect(view).toMatch(/return 'is-zero'/)
  })

  it('颜色只走语义令牌，且三种档位对白底都够对比', () => {
    // 三组选择器写在同一处，避免只改了排行、卡片头还是黑的。
    for (const cls of ['aRankNet', 'aCardNet', 'aNet']) {
      expect(view).toContain('.' + cls + '.is-positive')
      expect(view).toContain('.' + cls + '.is-negative')
      expect(view).toContain('.' + cls + '.is-zero')
    }
    expect(view).toMatch(/\.is-positive \{ color: var\(--ok\); \}/)
    expect(view).toMatch(/\.is-negative \{ color: var\(--danger-bright\); \}/)
    expect(view).toMatch(/\.is-zero \{ color: var\(--ink-faint\); \}/)
  })
})
