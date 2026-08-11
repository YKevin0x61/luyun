/**
 * KDS 调试日志开关
 * 默认关闭；可在设置页通过本地存储开启
 */

const DEBUG_STORAGE_KEY = 'kds_debug_enabled'

export function isDebugEnabled() {
  try {
    const stored = uni.getStorageSync(DEBUG_STORAGE_KEY)
    if (stored === true || stored === 'true') return true
    if (stored === false || stored === 'false') return false
  } catch (_) {
    // ignore
  }
  return false
}

export function setDebugEnabled(enabled) {
  try {
    uni.setStorageSync(DEBUG_STORAGE_KEY, Boolean(enabled))
  } catch (_) {
    // ignore
  }
}

export function debugLog(...args) {
  if (isDebugEnabled()) {
    console.log(...args)
  }
}
