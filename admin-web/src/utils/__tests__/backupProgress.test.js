import { describe, expect, it, vi } from 'vitest'
import {
  PROGRESS_FLASH_MS,
  createProgressController,
  createProgressState,
  formatBytes,
  formatTs,
  progressLabel,
} from '../backupProgress.js'

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
