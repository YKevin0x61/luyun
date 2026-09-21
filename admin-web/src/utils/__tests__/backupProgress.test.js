import { describe, expect, it, vi } from 'vitest'
import {
  PROGRESS_FLASH_MS,
  createProgressController,
  createProgressState,
  exportStageIndeterminate,
  exportStageLabel,
  exportStagePercent,
  formatBytes,
  formatTs,
  progressLabel,
} from '../backupProgress.js'

describe('exportStageLabel / exportStagePercent', () => {
  it('maps server stages to Chinese labels', () => {
    expect(exportStageLabel('collecting')).toBe('正在收集数据…')
    expect(exportStageLabel('app_data')).toBe('正在导出业务数据…')
    expect(exportStageLabel('archiving')).toBe('正在归档…')
    expect(exportStageLabel('encrypting')).toBe('正在加密…')
    expect(exportStageLabel('downloading')).toBe('正在下载…')
    // 未知阶段也得有话说，不能显示空白
    expect(exportStageLabel('')).toBe('正在导出…')
  })

  it('shows photo counts only when the server reports a total', () => {
    expect(exportStageLabel('photos', 12, 40)).toBe('正在打包照片 12/40…')
    expect(exportStageLabel('photos', 0, 0)).toBe('正在打包照片…')
  })

  it('renders real byte progress for pg_dump / 归档 / 加密', () => {
    // pg_dump 事先没有总量：只报"已经写了多少"
    expect(exportStageLabel('app_data', 12 * 1024 * 1024, 0, 'bytes')).toBe(
      '正在导出业务数据… 已写 12.0 MB',
    )
    // 归档与加密的总量在服务端打包前就算得出来：给真实百分比
    expect(exportStageLabel('archiving', 50, 100, 'bytes')).toBe('正在归档 50%')
    expect(exportStageLabel('encrypting', 1, 4, 'bytes')).toBe('正在加密 25%')
  })

  it('advances the bar by real progress inside each stage span', () => {
    expect(exportStagePercent('photos', 0, 40)).toBe(50)
    expect(exportStagePercent('photos', 40, 40)).toBe(75)
    expect(exportStagePercent('archiving', 50, 100, 'bytes')).toBe(84)
    expect(exportStagePercent('encrypting', 0, 100, 'bytes')).toBe(92)
    expect(exportStagePercent('done')).toBe(100)
    // app_data 按已写字节渐近推进：单调、有界，不随时间空转
    const small = exportStagePercent('app_data', 8 * 1024 * 1024, 0, 'bytes')
    const large = exportStagePercent('app_data', 200 * 1024 * 1024, 0, 'bytes')
    expect(small).toBeGreaterThan(3)
    expect(large).toBeGreaterThan(small)
    expect(large).toBeLessThanOrEqual(45)
  })

  it('only stages with a real total get a determinate bar', () => {
    expect(exportStageIndeterminate('photos', 40)).toBe(false)
    expect(exportStageIndeterminate('archiving', 100, 'bytes')).toBe(false)
    expect(exportStageIndeterminate('encrypting', 100, 'bytes')).toBe(false)
    expect(exportStageIndeterminate('photos', 0)).toBe(true)
    // pg_dump 有已写字节，条子会动，不该闪
    expect(exportStageIndeterminate('app_data', 0, 'bytes')).toBe(false)
  })
})

describe('progressLabel', () => {
  it('covers phases', () => {
    expect(progressLabel({ phase: 'uploading', percent: 40 })).toBe('上传中 40%')
    expect(progressLabel({ phase: 'processing' })).toBe('处理中…')
    expect(progressLabel({ phase: 'success' })).toBe('✓ 已上传')
    expect(progressLabel({ phase: 'error' })).toBe('✗ 上传失败')
  })
})

describe('createProgressController', () => {
  it('tracks upload then processing then flash hide', () => {
    vi.useFakeTimers()
    const { startProgress, makeProgressHandler, finishProgress } = createProgressController()
    const p = createProgressState()
    startProgress(p, 'preview')
    expect(p).toMatchObject({ active: true, phase: 'uploading', percent: 0 })

    const handler = makeProgressHandler(p)
    handler({ percent: 50, done: false })
    expect(p.percent).toBe(50)
    handler({ percent: 100, done: true })
    expect(p.phase).toBe('processing')

    finishProgress(p, 'preview', true)
    expect(p.phase).toBe('success')
    vi.advanceTimersByTime(PROGRESS_FLASH_MS)
    expect(p.active).toBe(false)
    vi.useRealTimers()
  })
})

describe('formatBytes / formatTs', () => {
  it('formats sizes', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(2 * 1024 * 1024)).toBe('2.00 MB')
  })

  it('formats timestamps', () => {
    expect(formatTs('2026-07-27T08:00:00+08:00')).toBe('2026-07-27 08:00:00')
    expect(formatTs('')).toBe('')
  })
})
