/** 登录偏好（记住登录 + 上次账号）的本地持久化。
 *
 * 只保存「下次是否自动登录」与上次登录的账号名；密码一律交给浏览器密码管理器，
 * 系统自身不落盘任何密码。admin 与卫生员工两套登录共用，用 namespace 隔离键名。
 */

const KEY_PREFIX = 'luyun.login.'

/** 未记录过偏好时默认勾选：门店机器打开即进，是日常用法；取消勾选会写入 '0' 并保持。 */
export const DEFAULT_REMEMBER = true

function resolveStorage(storage) {
  if (storage) return storage
  try {
    // 隐私模式下访问 window.localStorage 本身就会抛错，降级为「不记住偏好」。
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function readRaw(storage, key) {
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

function writeRaw(storage, key, value) {
  try {
    storage.setItem(key, value)
  } catch {
    // 配额满或隐私模式写不进去：不影响本次登录，只是下次不再记住。
  }
}

function fieldKey(namespace, field) {
  return `${KEY_PREFIX}${namespace}.${field}`
}

/**
 * 读取登录偏好。
 * @param {string} namespace 登录入口标识（'admin' | 'staff'）
 * @param {Storage} [storage] 可注入的存储实现，便于测试
 * @returns {{ remember: boolean, account: string }}
 */
export function loadLoginPrefs(namespace, storage) {
  const store = resolveStorage(storage)
  if (!store) return { remember: DEFAULT_REMEMBER, account: '' }
  const rememberRaw = readRaw(store, fieldKey(namespace, 'remember'))
  const accountRaw = readRaw(store, fieldKey(namespace, 'account'))
  return {
    // 只认 '0' / '1'：其他脏值（含 null）都回落到默认勾选。
    remember: rememberRaw === null ? DEFAULT_REMEMBER : rememberRaw !== '0',
    account: typeof accountRaw === 'string' ? accountRaw : '',
  }
}

/**
 * 写入登录偏好（登录成功后调用）。
 * @param {string} namespace 登录入口标识（'admin' | 'staff'）
 * @param {{ remember: boolean, account?: string }} prefs
 * @param {Storage} [storage] 可注入的存储实现，便于测试
 */
export function saveLoginPrefs(namespace, { remember, account } = {}, storage) {
  const store = resolveStorage(storage)
  if (!store) return
  writeRaw(store, fieldKey(namespace, 'remember'), remember ? '1' : '0')
  if (account) writeRaw(store, fieldKey(namespace, 'account'), account)
}
