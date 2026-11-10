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

describe('整月六态一眼可辨（/workbench/staff/month）', () => {
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

describe('加班与补钟（/workbench/staff/overtime）', () => {
  const src = read('../../overtime/MeOvertimeView.vue')

  it('净时长 + 两枚状态徽章：净时长 / 待批（ADR 0124 起再加「已抵存假」）', () => {
    expect(src).toContain('net_half_hours')
    expect(src).toMatch(/class="badge[^"]*">\s*净时长/)
    expect(src).toMatch(/class="badge[^"]*">\s*待批/)
    expect(src).toMatch(/class="badge[^"]*">\s*已抵存假/)
  })

  it('那句解释性文案已经下线（由徽章各自说清）', () => {
    expect(src).not.toContain('只有「已批准」那一行是算数的')
  })

  it('用存假抵补钟（ADR 0124）：可抵天数来自服务端、只能整天、归到补钟那个月', () => {
    expect(src).toContain('deduction_capacity')
    expect(src).toContain('/api/overtime/me/deductions')
    expect(src).toContain('用存假抵补钟')
    expect(src).toContain('可抵')
    // 8.5 小时 = 1 天，与加班费公式同一个分母；页面上不出现第二个天数换算。
    expect(src).toMatch(/HOURS_PER_LEAVE_DAY\s*=\s*8\.5/)
    // 提交时归月 = 当前正在看的那个月（补钟登记月），不是「今天」。
    expect(src).toMatch(/month:\s*summary\.value\?\.month/)
  })

  it('页面无金额（ADR 0098 / 0103 的边界）：模板里不出现金额/底薪/¥', () => {
    const tpl = src.slice(src.indexOf('<template>'), src.indexOf('</template>'))
    for (const token of ['金额', '底薪', '¥', '元/小时']) {
      expect(tpl, `模板里出现了「${token}」`).not.toContain(token)
    }
  })
})

describe('员工端「我的时间」三页走查修复（2026-11-09 A 组）', () => {
  const overtime = read('../../overtime/MeOvertimeView.vue')
  const month = read('../../today/TodayMonthView.vue')
  const today = read('../../today/TodayView.vue')

  it('负零不再出现：0 小时写成 0，不写成「−0」', () => {
    expect(overtime).toMatch(/function hoursLabel\(/)
    expect(overtime).toMatch(/text === '−0' \? '0' : text/)
    expect(overtime).toMatch(/\{\{ hoursLabel\(summary\.approved\.net_half_hours\) \}\}/)
    expect(overtime).toMatch(/\{\{ hoursLabel\(summary\.pending\.net_half_hours\) \}\}/)
    // 抵扣卡的「本月已批补钟 0 小时」也走同一个包装。
    expect(overtime).toMatch(/hoursLabel\(c\.makeup_half_hours\)/)
  })

  it('批准前先确认：店长在这里撤不回，误点一次就落账', () => {
    expect(overtime).toContain("import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'")
    expect(overtime).toMatch(/askApprove\('entry', entry\)/)
    expect(overtime).toMatch(/askApprove\('deduction', item\)/)
    expect(overtime).toContain('批准这一笔？')
    expect(overtime).toContain('你自己撤不回')
    expect(overtime).toContain('confirmApprove')
  })

  it('提交按钮灰着时写清为什么（事由为空是唯一原因）', () => {
    expect(overtime).toMatch(/class="oRule oFormHint"/)
    expect(overtime).toContain('填了事由就能提交。')
    expect(overtime).toContain('选好日期、填了事由就能提交。')
  })

  it('整月存假的「明细」是 ≥44px 的触控行，不再是一条 19px 的小字', () => {
    expect(month).toMatch(/\.leaveMore summary\s*\{[^}]*display:\s*flex/s)
    expect(month).toMatch(/\.leaveMore summary\s*\{[^}]*min-height:\s*var\(--tap\)/s)
  })

  it('今日成绩空态给出下一步（去卫生交一项），且不新增卫生数据请求', () => {
    expect(today).toContain('去卫生交一项 ›')
    expect(today).toMatch(/to="\/workbench\/staff\/clean"/)
    expect(today).not.toMatch(/staffRequest\('\/api\/hygiene\/staff\/daily-work'\)/)
  })
})
