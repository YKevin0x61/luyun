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

/** 字节数的短标签（12.3 MB），用于「已写多少」这类文案。 */
function shortBytes(n) {
  if (!n) return '0 B'
  const KB = 1024
  const MB = KB * 1024
  return n >= MB ? `${(n / MB).toFixed(1)} MB` : `${Math.round(n / KB)} KB`
}

function ratioPercent(done, total) {
  if (!total) return 0
  return Math.min(100, Math.max(0, Math.round((done / total) * 100)))
}

/**
 * 导出任务（服务端后台打包）的阶段文案。
 *
 * 服务端报「阶段 + done/total + unit」：`unit === 'bytes'` 时 done/total 是字节数
 * （归档、加密、pg_dump 已写量），`'count'` 是张数（照片）。归档与加密的总量在
 * 服务端打包前就能算出来，所以给的是真实百分比，不是转圈动画。
 */
export function exportStageLabel(stage, done = 0, total = 0, unit = 'count') {
  const isBytes = unit === 'bytes'
  switch (stage) {
    case 'collecting':
      return '正在收集数据…'
    case 'app_data':
      // pg_dump 事先不知道总量：只报"已经写了多少"
      return isBytes && done > 0
        ? `正在导出业务数据… 已写 ${shortBytes(done)}`
        : '正在导出业务数据…'
    case 'recipes':
      return '正在导出配方数据…'
    case 'photos_scan':
      return '正在核对照片…'
    case 'photos':
      return total ? `正在打包照片 ${done}/${total}…` : '正在打包照片…'
    case 'archiving':
      return isBytes && total ? `正在归档 ${ratioPercent(done, total)}%` : '正在归档…'
    case 'encrypting':
      return isBytes && total ? `正在加密 ${ratioPercent(done, total)}%` : '正在加密…'
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
 * 进度条百分比：按阶段切分整条 0–100%，阶段内部用真实的 done/total。
 *
 * `app_data`（pg_dump）没有总量，用「已写字节」做一条单调有界的渐近曲线——它随
 * 真实写入前进、不会随时间空转，也不会越过该阶段的上限。
 */
const STAGE_SPAN = {
  collecting: [0, 3],
  app_data: [3, 45],
  recipes: [45, 48],
  photos_scan: [48, 50],
  photos: [50, 75],
  archiving: [75, 92],
  encrypting: [92, 99],
  saving: [99, 99],
  downloading: [99, 100],
  done: [100, 100],
}

export function exportStagePercent(stage, done = 0, total = 0, unit = 'count') {
  const span = STAGE_SPAN[stage] || [0, 0]
  const [lo, hi] = span
  if (stage === 'app_data') {
    // 64MB 的尺度：写满 64MB 到该阶段的 ~63%，再往上逐渐逼近上限
    const ratio = 1 - Math.exp(-Math.max(0, done) / (64 * 1024 * 1024))
    return Math.round(lo + (hi - lo) * ratio)
  }
  if (!total) return lo
  return Math.round(lo + (hi - lo) * Math.min(1, Math.max(0, done / total)))
}

/**
 * 进度条是否该走不确定动画。只有"说得出总量"的阶段才有确定进度：照片（张数）、
 * 归档与加密（字节）；其余阶段很短，用不确定动画表示"在动"。
 */
export function exportStageIndeterminate(stage, total = 0, unit = 'count') {
  if (stage === 'app_data') return false
  if (stage === 'photos' || stage === 'archiving' || stage === 'encrypting') {
    return !(total > 0)
  }
  return true
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
