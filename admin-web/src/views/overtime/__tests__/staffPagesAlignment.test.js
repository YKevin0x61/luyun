// @vitest-environment node
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * 员工端三页对齐（③-3c-3 / t54）—— 把「一眼可辨」「徽章替代解释」「无金额」钉成断言。
 *
 * 为什么用读文件而不是挂组件：这三条判的是**产品形态**（六态各自有独立样式、徽章替代了一句
 * 解释、页面上不许出现金额），读源码文本就足够可靠，也不会把测试绑死在组件内部结构上。
 * 挂载类的行为断言在各自的页测试里（如 `views/today/__tests__/`）。
 */
const here = dirname(fileURLToPath(import.meta.url))
const read = (p) => readFileSync(join(here, p), 'utf8')

describe('整月六态一眼可辨（/workbench/me/month）', () => {
  const src = read('../../today/TodayMonthView.vue')

  it('图例六态齐全：上班 / 休 / 请假 / 班次已调整 / 还没排 / 申请中', () => {
    for (const label of ['上班', '休', '请假', '班次已调整', '还没排', '申请中']) {
      expect(src, `图例缺少「${label}」`).toContain(label)
    }
  })

  it('六态各用独立的圆点类（不是复用同一个类靠位置区分）', () => {
    const dots = [...src.matchAll(/class="dot ([a-z]+)"/g)].map((m) => m[1])
    expect(new Set(dots).size, `图例圆点类：${dots.join(', ')}`).toBeGreaterThanOrEqual(6)
  })

  it('每个圆点类都有自己的颜色声明（一眼可辨的判据）', () => {
    for (const cls of ['shift', 'rest', 'leave', 'moved', 'none', 'pending']) {
      expect(src, `.dot.${cls} 没有独立样式`).toMatch(new RegExp(`\\.dot\\.${cls}\\s*\\{[^}]*background`))
    }
  })
})

describe('加班与补钟（/workbench/me/overtime）', () => {
  const src = read('../../overtime/MeOvertimeView.vue')

  it('净时长 + 两枚状态徽章：已批准 / 待批', () => {
    expect(src).toContain('net_half_hours')
    expect(src).toMatch(/class="badge[^"]*">\s*已批准/)
    expect(src).toMatch(/class="badge[^"]*">\s*待批/)
  })

  it('那句解释性文案已经下线（由徽章各自说清）', () => {
    expect(src).not.toContain('只有「已批准」那一行是算数的')
  })

  it('页面无金额（ADR 0098 / 0103 的边界）：模板里不出现金额/底薪/¥', () => {
    const tpl = src.slice(src.indexOf('<template>'), src.indexOf('</template>'))
    for (const token of ['金额', '底薪', '¥', '元/小时']) {
      expect(tpl, `模板里出现了「${token}」`).not.toContain(token)
    }
  })
})
