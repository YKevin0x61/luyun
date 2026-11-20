// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import KdsBacklogPanel from '../KdsBacklogPanel.vue'

function mountPanel(backlog) {
  return mount(KdsBacklogPanel, {
    props: { backlog },
    global: { plugins: [createPinia()] },
  })
}

const STATIONS = [
  { station_id: 'dimsum', pending: 900, overdue: 890, oldest_wait_minutes: 120, load_level: 'high' },
  { station_id: 'wok', pending: 12, overdue: 0, oldest_wait_minutes: 20, load_level: 'medium' },
]

// kds_active=false：dish_status 几乎没人回写，六个档口全红高负载是失真告警。
// 档口明细保留，但要给橙色提示、去掉红/黄负载标签与红条高亮。
describe('出餐压力卡的数据源健康降级', () => {
  it('kds_active=false 时只给橙色提示，不出现红/黄负载标签与红条', () => {
    const wrapper = mountPanel({ stations: STATIONS, kds_active: false, kds_active_ratio: 0.0036 })
    expect(wrapper.text()).toContain('KDS 未启用，出餐状态没人回写')
    expect(wrapper.text()).not.toContain('高负载')
    expect(wrapper.text()).not.toContain('繁忙')
    expect(wrapper.findAll('.bar-fill.red')).toHaveLength(0)
    expect(wrapper.findAll('.bar-fill.yellow')).toHaveLength(0)
    // 档口明细保留
    expect(wrapper.findAll('.kds-backlog-row')).toHaveLength(2)
  })

  it('kds_active=true 时保持原有红/黄负载标签', () => {
    const wrapper = mountPanel({ stations: STATIONS, kds_active: true })
    expect(wrapper.text()).toContain('高负载')
    expect(wrapper.text()).toContain('繁忙')
    expect(wrapper.find('.kds-backlog-tag.red').exists()).toBe(true)
    expect(wrapper.find('.bar-fill.red').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('KDS 未启用，出餐状态没人回写')
  })
})