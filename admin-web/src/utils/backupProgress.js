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

/**
 * 导出任务（服务端后台打包）的阶段文案。
 *
 * 与上传进度不同：导出没有字节级进度，服务端只报「阶段 + 可选的 done/total」
 * （目前只有照片按张报数）。阶段码的中文映射留在前端，后端只给机器可读的 stage。
 */
export function exportStageLabel(stage, done = 0, total = 0) {
  switch (stage) {
    case 'collecting':
      return '正在收集数据…'
    case 'app_data':
      return '正在导出业务数据…'
    case 'recipes':
      return '正在导出配方数据…'
    case 'photos_scan':
      return '正在核对照片…'
    case 'photos':
      return total ? `正在打包照片 ${done}/${total}…` : '正在打包照片…'
    case 'archiving':
      return '正在归档…'
    case 'encrypting':
      return '正在加密…'
    case 'saving':
      return '正在写入本机副本…'
    case 'downloading':
      return '正在下载…'
    case 'done':
      return '✓ 已生成'
    default:
      return '正在导出…'
  }
}

/**
 * 进度条百分比。只有照片阶段有真实的 done/total，其余阶段给一个粗粒度推进值——
 * 不假装精确，配合进度条的 indeterminate 动画表示「在动，但说不准多久」。
 */
export function exportStagePercent(stage, done = 0, total = 0) {
  if (stage === 'photos') {
    if (!total) return 30
    return Math.min(90, 30 + Math.round((done / total) * 60))
  }
  const byStage = {
    collecting: 8,
    app_data: 25,
    recipes: 45,
    photos_scan: 50,
    archiving: 92,
    encrypting: 96,
    saving: 98,
    downloading: 99,
    done: 100,
  }
  return byStage[stage] ?? 5
}

/** 进度条是否该走不确定动画：没有真实 done/total 的阶段都算。 */
export function exportStageIndeterminate(stage, total = 0) {
  return !(stage === 'photos' && total > 0)
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
