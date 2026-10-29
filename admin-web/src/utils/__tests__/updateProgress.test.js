import { describe, expect, it } from 'vitest'
import {
  UPDATE_STEPS,
  formatRate,
  stepSymbol,
  updateDownloadView,
  updateStageSteps,
} from '../updateProgress'

const KEYS = UPDATE_STEPS.map((step) => step.key)

function states(job) {
  return updateStageSteps(job).items.map((item) => item.state)
}

describe('updateStageSteps', () => {
  it('空闲时全部步骤未开始，且有明确的一句话', () => {
    const view = updateStageSteps({ stage: 'idle' })
    expect(view.outcome).toBe('idle')
    expect(view.items.map((item) => item.key)).toEqual(KEYS)
    expect(states({ stage: 'idle' })).toEqual(KEYS.map(() => 'pending'))
    expect(view.summary).toContain('暂无进行中的更新作业')
  })

  it('进行中：当前阶段之前是完成、当前阶段高亮、之后未开始', () => {
    const view = updateStageSteps({ stage: 'installing', snapshot_ts: '20260919-0359' })
    expect(view.outcome).toBe('running')
    expect(states({ stage: 'installing', snapshot_ts: '20260919-0359' })).toEqual([
      'done', 'done', 'active', 'pending', 'pending', 'pending', 'pending',
    ])
    expect(view.summary).toContain('安装发行包')
    expect(view.summary).toContain('3/7')
    // 备份步骤真的完成了才标时间戳
    expect(view.items[0].at).toBe('20260919-0359')
  })

  it('步骤顺序与后端阶段序列一致：应用数据库迁移在同步依赖之后、切换并重启之前', () => {
    expect(KEYS).toEqual([
      'backing_up',
      'fetching_bundle',
      'installing',
      'syncing_deps',
      'applying_migrations',
      'restarting',
      'health',
    ])
  })

  it('应用数据库迁移：进度条停在「应用数据库迁移」，不回落成「暂无进行中的更新作业」', () => {
    const view = updateStageSteps({ stage: 'applying_migrations' })
    expect(view.outcome).toBe('running')
    expect(view.currentLabel).toBe('应用数据库迁移')
    expect(view.summary).toContain('第 5/7 步')
    expect(states({ stage: 'applying_migrations' })).toEqual([
      'done', 'done', 'done', 'done', 'active', 'pending', 'pending',
    ])
    // 未知阶段会回落成 idle —— 这条断言钉住「阶段表漏了 applying_migrations」的缺陷。
    expect(view.summary).not.toContain('暂无进行中的更新作业')
  })

  it('刚排队时第一步还是进行中，不预先标成完成', () => {
    const view = updateStageSteps({ stage: 'queued' })
    expect(view.items[0].state).toBe('active')
    expect(view.items[0].at).toBe('')
    expect(view.items[1].state).toBe('pending')
  })

  it('兼容 ADR 0010 的 legacy 阶段名（fetching / installing_assets）', () => {
    expect(states({ stage: 'fetching' })).toEqual([
      'done', 'active', 'pending', 'pending', 'pending', 'pending', 'pending',
    ])
    expect(states({ stage: 'installing_assets' })).toEqual([
      'done', 'done', 'active', 'pending', 'pending', 'pending', 'pending',
    ])
  })

  it('成功 = 七个步骤全部完成，并带出快照 / 重启 / 健康确认时间', () => {
    const view = updateStageSteps({
      stage: 'succeeded',
      snapshot_ts: '20260919-0359',
      restart_requested_at: '2026-09-19T03:59:50+08:00',
      health_confirmed_at: '2026-09-19T04:00:20+08:00',
    })
    expect(view.outcome).toBe('succeeded')
    expect(states({ stage: 'succeeded' })).toEqual(KEYS.map(() => 'done'))
    expect(view.items[5].at).toBe('2026-09-19T03:59:50+08:00')
    expect(view.items[6].at).toBe('2026-09-19T04:00:20+08:00')
  })

  it('已切换但未健康：只有健康确认这一步是失败态', () => {
    const view = updateStageSteps({
      stage: 'succeeded_but_unhealthy',
      snapshot_ts: '20260919-0359',
      restart_requested_at: '2026-09-19T03:59:50+08:00',
    })
    expect(view.outcome).toBe('unhealthy')
    expect(states({ stage: 'succeeded_but_unhealthy' })).toEqual([
      'done', 'done', 'done', 'done', 'done', 'done', 'failed',
    ])
    expect(view.summary).toContain('健康确认未通过')
  })

  it('失败：只用真正落下的时间戳判断完成过哪些步骤', () => {
    // 只有快照时间戳 → 只知道备份完成，其余不猜
    expect(states({ stage: 'failed', snapshot_ts: '20260919-0359' })).toEqual([
      'done', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending',
    ])
    // 已经请求重启 → 之前的下载/安装/依赖/迁移都完成过
    const restarted = states({
      stage: 'failed',
      snapshot_ts: '20260919-0359',
      restart_requested_at: '2026-09-19T03:59:50+08:00',
    })
    expect(restarted).toEqual([
      'done', 'done', 'done', 'done', 'done', 'done', 'pending',
    ])
    const view = updateStageSteps({ stage: 'failed' })
    expect(view.summary).toContain('失败')
    expect(states({ stage: 'failed' })).toEqual(KEYS.map(() => 'pending'))
  })

  it('未知阶段退化成空闲，不抛异常', () => {
    expect(updateStageSteps(null).outcome).toBe('idle')
    expect(updateStageSteps({ stage: 'whatever' }).outcome).toBe('idle')
  })
})

describe('stepSymbol', () => {
  it('四种状态各有可读的符号，不依赖颜色', () => {
    expect(stepSymbol('done')).toBe('✓')
    expect(stepSymbol('active')).toBe('●')
    expect(stepSymbol('failed')).toBe('✗')
    expect(stepSymbol('pending')).toBe('○')
  })
})

describe('formatRate', () => {
  it('字节/秒按人类可读的速率显示', () => {
    expect(formatRate(0)).toBe('0 B/s')
    expect(formatRate(2048)).toBe('2.0 KB/s')
    expect(formatRate(1.5 * 1024 * 1024)).toBe('1.50 MB/s')
  })

  it('非数值不硬编一个假速率', () => {
    expect(formatRate(undefined)).toBe('')
    expect(formatRate(null)).toBe('')
    expect(formatRate('abc')).toBe('')
    expect(formatRate(-1)).toBe('')
  })
})

describe('updateDownloadView', () => {
  const download = {
    asset: 'luyun-release-bundle.tar.gz',
    attempt: 1,
    done_bytes: 2 * 1024 * 1024,
    total_bytes: 8 * 1024 * 1024,
    bytes_per_second: 1024 * 1024,
    updated_at: '2026-10-30T01:00:00+08:00',
  }

  it('只在下载发行包阶段出现，其他阶段一律 null', () => {
    expect(updateDownloadView({ stage: 'fetching_bundle', download })).not.toBeNull()
    expect(updateDownloadView({ stage: 'installing', download })).toBeNull()
    expect(updateDownloadView({ stage: 'fetching_bundle' })).toBeNull()
    expect(updateDownloadView({ stage: 'fetching_bundle', download: {} })).toBeNull()
    expect(updateDownloadView(null)).toBeNull()
  })

  it('有总量时给出百分比与「已下载 / 总量」', () => {
    const view = updateDownloadView({ stage: 'fetching_bundle', download })
    expect(view.percent).toBe(25)
    expect(view.doneText).toBe('2.00 MB')
    expect(view.totalText).toBe('8.00 MB')
    expect(view.rateText).toBe('1.00 MB/s')
    expect(view.indeterminate).toBe(false)
    expect(view.ariaText).toContain('25%')
    expect(view.ariaText).toContain('1.00 MB/s')
  })

  it('服务端没给 Content-Length 时只报已下载与速率，不编百分比', () => {
    const view = updateDownloadView({
      stage: 'fetching_bundle',
      download: { ...download, total_bytes: null },
    })
    expect(view.percent).toBeNull()
    expect(view.totalText).toBe('')
    expect(view.indeterminate).toBe(true)
    expect(view.doneText).toBe('2.00 MB')
    expect(view.rateText).toBe('1.00 MB/s')
    // 进度条走不确定动画，但读屏仍要说出「已下载多少」。
    expect(view.ariaText).toContain('已下载 2.00 MB')
  })

  it('重试时标出第几次尝试，第一次不显示', () => {
    expect(updateDownloadView({ stage: 'fetching_bundle', download }).attempt).toBeNull()
    const retried = updateDownloadView({
      stage: 'fetching_bundle',
      download: { ...download, attempt: 3 },
    })
    expect(retried.attempt).toBe(3)
  })

  it('百分比夹在 0–100 之间，不因异常字节数溢出', () => {
    const over = updateDownloadView({
      stage: 'fetching_bundle',
      download: { ...download, done_bytes: 99 * 1024 * 1024 },
    })
    expect(over.percent).toBe(100)
    const negative = updateDownloadView({
      stage: 'fetching_bundle',
      download: { ...download, done_bytes: -1 },
    })
    expect(negative).toBeNull()
  })
})
