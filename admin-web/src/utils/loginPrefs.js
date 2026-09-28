/** 登录偏好（记住登录 + 上次账号）的本地持久化。
 *
 * 只保存「下次是否自动登录」与上次登录的账号名；密码一律交给浏览器密码管理器，
 * 系统自身不落盘任何密码。admin 与卫生员工两套登录共用，用 namespace 隔离键名。
 */

const KEY_PREFIX = 'luyun.login.'

/** 未记录过偏好时默认勾选：门店机器打开即进，是日常用法；取消勾选会写入 '0' 并保持。 */
export const DEFAULT_REMEMBER = true

/** 没记住过时默认开员工栏：员工手机打开 `/login` 就该直接看到手机号那一栏。 */
export const DEFAULT_LOGIN_TAB = 'staff'

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

/**
 * 读回登录面板上次停在哪一栏（'admin' | 'staff'）。
 *
 * 键名沿用同一族（`luyun.login.panel.tab`），不另造一套 localStorage 方案；
 * 脏值（手改过、别的版本写过）一律回落到默认栏。
 *
 * @param {Storage} [storage] 可注入的存储实现，便于测试
 * @returns {'admin' | 'staff'}
 */
export function loadLoginTab(storage) {
  const store = resolveStorage(storage)
  if (!store) return DEFAULT_LOGIN_TAB
  const raw = readRaw(store, fieldKey('panel', 'tab'))
  return raw === 'admin' ? 'admin' : DEFAULT_LOGIN_TAB
}

/**
 * 记住这次选的栏（用户显式点 Tab 时调用；`?next=` 强制的那次不算选择）。
 *
 * @param {'admin' | 'staff'} tab
 * @param {Storage} [storage] 可注入的存储实现，便于测试
 */
export function saveLoginTab(tab, storage) {
  const store = resolveStorage(storage)
  if (!store) return
  writeRaw(store, fieldKey('panel', 'tab'), tab === 'admin' ? 'admin' : 'staff')
}
