// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import StatCards from '../StatCards.vue'

function mountStatCards(summary) {
  return mount(StatCards, {
    props: { summary },
    global: { plugins: [createPinia()] },
  })
}

function kitchenHealthCard(wrapper) {
  return wrapper.findAll('.stat-card').find((card) => card.text().includes('出餐健康'))
}

// kds_backlog.kds_active=false：KDS 没人回写 dish_status，待出/超时是失真数字，
// 卡片不能继续红色告警，要降级成「未启用」一句短话 + 「KDS 未回写 · 待出 N 条」。
// 文案必须短：22px 的 value 放「KDS 未启用」会在 160px 卡里折成两行、把整张卡顶高（真机截图定的）。
describe('出餐健康卡的数据源健康降级', () => {
  it('kds_active=false 时不报红，显示未启用与待出条数', () => {
    const wrapper = mountStatCards({
      kds_backlog: {
        total_pending: 1179,
        overdue_count: 1178,
        busiest_station: { station_id: 'dimsum', pending: 900 },
        kds_active: false,
        kds_active_ratio: 0.0036,
      },
    })
    const card = kitchenHealthCard(wrapper)
    expect(card.text()).toContain('未启用')
    expect(card.text()).toContain('KDS 未回写 · 待出 1179 条')
    expect(card.find('.value').classes()).not.toContain('red')
    expect(card.find('.value').classes()).toContain('value-text')
  })

  it('kds_active=true 时保持原有红色告警展示', () => {
    const wrapper = mountStatCards({
      kds_backlog: {
        total_pending: 12,
        overdue_count: 3,
        busiest_station: { station_id: 'dimsum', pending: 7 },
        kds_active: true,
        kds_active_ratio: 0.25,
      },
    })
    const card = kitchenHealthCard(wrapper)
    expect(card.text()).toContain('待出 12 · 超时 3')
    expect(card.find('.value').classes()).toContain('red')
    expect(card.text()).not.toContain('未启用')
    expect(card.find('.value').classes()).not.toContain('value-text')
  })

  // 旧接口没有 kds_active 字段（或 summary 还没拉到）时按活跃处理，不能误降级。
  it('缺少 kds_active 字段时保持原有展示（向后兼容）', () => {
    const wrapper = mountStatCards({
      kds_backlog: { total_pending: 5, overdue_count: 0, busiest_station: null },
    })
    const card = kitchenHealthCard(wrapper)
    expect(card.text()).toContain('待出 5 · 超时 0')
    expect(card.text()).not.toContain('KDS 未启用')
  })
})