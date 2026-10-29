// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import UpdateStageProgress from '../UpdateStageProgress.vue'

/**
 * 「下载发行包」这一步要能看出**进度**与**速率**（弱网门店最常卡在这里）。
 *
 * 数据来自作业状态文件的 `download`（后端按 500ms 节流写入），这里只验证渲染：
 * 百分比、已下载/总量、速率、重试次数，以及没有 Content-Length 时的不确定态。
 */
const DOWNLOAD = {
  asset: 'luyun-release-bundle.tar.gz',
  attempt: 1,
  done_bytes: 2 * 1024 * 1024,
  total_bytes: 8 * 1024 * 1024,
  bytes_per_second: 1024 * 1024,
  updated_at: '2026-10-30T01:00:00+08:00',
}

function mountStage(overrides = {}) {
  return mount(UpdateStageProgress, {
    props: {
      job: { stage: 'fetching_bundle', download: DOWNLOAD, ...overrides },
    },
  })
}

describe('UpdateStageProgress 下载发行包进度', () => {
  it('显示进度条、百分比、已下载 / 总量与速率', () => {
    const wrapper = mountStage()
    // 原生 <progress> 自带 progressbar 语义；距离由 value/max 表达。
    const bar = wrapper.find('progress.download__bar')
    expect(bar.exists()).toBe(true)
    expect(bar.attributes('value')).toBe('25')
    expect(bar.attributes('max')).toBe('100')
    expect(bar.attributes('aria-label')).toContain('25%')

    expect(wrapper.find('.download__percent').text()).toBe('25%')
    expect(wrapper.find('.download__stats').text()).toContain('2.00 MB / 8.00 MB')
    expect(wrapper.find('.download__stats').text()).toContain('1.00 MB/s')
  })

  it('没有总量时只报已下载与速率，进度条走不确定态', () => {
    const wrapper = mountStage({ download: { ...DOWNLOAD, total_bytes: null } })
    const bar = wrapper.find('progress.download__bar')
    // 没有 value 属性 = 浏览器的不确定进度条。
    expect(bar.attributes('value')).toBeUndefined()
    expect(bar.classes()).toContain('is-indeterminate')
    expect(wrapper.find('.download__percent').exists()).toBe(false)
    expect(wrapper.find('.download__stats').text()).toContain('已下载 2.00 MB')
    expect(wrapper.find('.download__stats').text()).toContain('1.00 MB/s')
  })

  it('重试过才显示「第 N 次尝试」', () => {
    expect(mountStage().find('.download__attempt').exists()).toBe(false)
    const retried = mountStage({ download: { ...DOWNLOAD, attempt: 4 } })
    expect(retried.find('.download__attempt').text()).toBe('第 4 次尝试')
  })

  it('离开下载阶段就没这条进度（不留一条停在 100% 的旧数据）', () => {
    const wrapper = mountStage({ stage: 'installing' })
    expect(wrapper.find('.download').exists()).toBe(false)
  })

  it('阶段仍高亮「下载发行包」，进度块与其同屏', () => {
    const wrapper = mountStage()
    const active = wrapper.findAll('.stage.is-active')
    expect(active).toHaveLength(1)
    expect(active[0].text()).toContain('下载发行包')
    expect(wrapper.find('.download').exists()).toBe(true)
  })
})
