/** Upload progress state machine for backup preview / import (XHR upload). */

export const PROGRESS_FLASH_MS = 1500

export function createProgressState() {
  return { active: false, phase: 'uploading', percent: 0 }
}

export function progressLabel(p) {
  if (p.phase === 'uploading') return `上传中 ${p.percent}%`
  if (p.phase === 'processing') return '处理中…'
  if (p.phase === 'success') return '✓ 已上传'
  if (p.phase === 'error') return '✗ 上传失败'
  return ''
}

/**
 * Pair of preview/import progress helpers sharing one timer map.
 * @param {{ setTimeout?: typeof setTimeout, clearTimeout?: typeof clearTimeout }} [clock]
 */
export function createProgressController(clock = {}) {
  const setTimer = clock.setTimeout || setTimeout
  const clearTimer = clock.clearTimeout || clearTimeout
  const timers = { preview: null, import: null }

  function startProgress(p, timerKey) {
    if (timers[timerKey]) {
      clearTimer(timers[timerKey])
      timers[timerKey] = null
    }
    p.active = true
    p.phase = 'uploading'
    p.percent = 0
  }

  function makeProgressHandler(p) {
    return ({ percent, done }) => {
      if (done) {
        p.phase = 'processing'
        p.percent = 100
      } else if (p.phase === 'uploading') {
        p.percent = percent
      }
    }
  }

  function finishProgress(p, timerKey, ok) {
    p.phase = ok ? 'success' : 'error'
    p.percent = 100
    if (timers[timerKey]) clearTimer(timers[timerKey])
    timers[timerKey] = setTimer(() => {
      p.active = false
      timers[timerKey] = null
    }, PROGRESS_FLASH_MS)
  }

  return { startProgress, makeProgressHandler, finishProgress, timers }
}

export function formatBytes(n) {
  if (!n) return '0 B'
  const KB = 1024
  const MB = KB * 1024
  return n >= MB ? `${(n / MB).toFixed(2)} MB` : `${(n / KB).toFixed(1)} KB`
}

export function formatTs(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 19)
}
