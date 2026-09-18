import { describe, expect, it } from 'vitest'
import { UPDATE_STEPS, stepSymbol, updateStageSteps } from '../updateProgress'

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
      'done', 'done', 'active', 'pending', 'pending', 'pending',
    ])
    expect(view.summary).toContain('安装发行包')
    expect(view.summary).toContain('3/6')
    // 备份步骤真的完成了才标时间戳
    expect(view.items[0].at).toBe('20260919-0359')
  })

  it('刚排队时第一步还是进行中，不预先标成完成', () => {
    const view = updateStageSteps({ stage: 'queued' })
    expect(view.items[0].state).toBe('active')
    expect(view.items[0].at).toBe('')
    expect(view.items[1].state).toBe('pending')
  })

  it('兼容 ADR 0010 的 legacy 阶段名（fetching / installing_assets）', () => {
    expect(states({ stage: 'fetching' })).toEqual([
      'done', 'active', 'pending', 'pending', 'pending', 'pending',
    ])
    expect(states({ stage: 'installing_assets' })).toEqual([
      'done', 'done', 'active', 'pending', 'pending', 'pending',
    ])
  })

  it('成功 = 六个步骤全部完成，并带出快照 / 重启 / 健康确认时间', () => {
    const view = updateStageSteps({
      stage: 'succeeded',
      snapshot_ts: '20260919-0359',
      restart_requested_at: '2026-09-19T03:59:50+08:00',
      health_confirmed_at: '2026-09-19T04:00:20+08:00',
    })
    expect(view.outcome).toBe('succeeded')
    expect(states({ stage: 'succeeded' })).toEqual(KEYS.map(() => 'done'))
    expect(view.items[4].at).toBe('2026-09-19T03:59:50+08:00')
    expect(view.items[5].at).toBe('2026-09-19T04:00:20+08:00')
  })

  it('已切换但未健康：只有健康确认这一步是失败态', () => {
    const view = updateStageSteps({
      stage: 'succeeded_but_unhealthy',
      snapshot_ts: '20260919-0359',
      restart_requested_at: '2026-09-19T03:59:50+08:00',
    })
    expect(view.outcome).toBe('unhealthy')
    expect(states({ stage: 'succeeded_but_unhealthy' })).toEqual([
      'done', 'done', 'done', 'done', 'done', 'failed',
    ])
    expect(view.summary).toContain('健康确认未通过')
  })

  it('失败：只用真正落下的时间戳判断完成过哪些步骤', () => {
    // 只有快照时间戳 → 只知道备份完成，其余不猜
    expect(states({ stage: 'failed', snapshot_ts: '20260919-0359' })).toEqual([
      'done', 'pending', 'pending', 'pending', 'pending', 'pending',
    ])
    // 已经请求重启 → 之前的下载/安装/依赖都完成过
    const restarted = states({
      stage: 'failed',
      snapshot_ts: '20260919-0359',
      restart_requested_at: '2026-09-19T03:59:50+08:00',
    })
    expect(restarted).toEqual(['done', 'done', 'done', 'done', 'done', 'pending'])
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
