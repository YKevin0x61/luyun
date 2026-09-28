import { describe, expect, it } from 'vitest'
import {
  DEFAULT_LOGIN_TAB,
  DEFAULT_REMEMBER,
  loadLoginPrefs,
  loadLoginTab,
  saveLoginPrefs,
  saveLoginTab,
} from '../loginPrefs.js'

/** 内存版 Storage，并可选地模拟隐私模式（读写都抛错）。 */
function memoryStorage({ throwing = false } = {}) {
  const map = new Map()
  const boom = () => {
    throw new Error('storage disabled')
  }
  return {
    getItem: (key) => (throwing ? boom() : (map.has(key) ? map.get(key) : null)),
    setItem: (key, value) => (throwing ? boom() : map.set(key, String(value))),
    dump: () => Object.fromEntries(map),
  }
}

describe('loadLoginPrefs', () => {
  it('默认勾选记住登录，账号为空', () => {
    expect(DEFAULT_REMEMBER).toBe(true)
    expect(loadLoginPrefs('admin', memoryStorage())).toEqual({ remember: true, account: '' })
  })

  it('读回已保存的偏好与账号', () => {
    const store = memoryStorage()
    saveLoginPrefs('admin', { remember: false, account: 'boss' }, store)
    expect(loadLoginPrefs('admin', store)).toEqual({ remember: false, account: 'boss' })
  })

  it('脏值回落到默认勾选', () => {
    const store = memoryStorage()
    store.setItem('luyun.login.admin.remember', 'yes-please')
    expect(loadLoginPrefs('admin', store).remember).toBe(true)
  })

  it('admin 与 staff 的偏好互不影响', () => {
    const store = memoryStorage()
    saveLoginPrefs('staff', { remember: false, account: '13800138000' }, store)
    expect(loadLoginPrefs('staff', store)).toEqual({ remember: false, account: '13800138000' })
    expect(loadLoginPrefs('admin', store)).toEqual({ remember: true, account: '' })
  })

  it('存储不可用时退化为默认值而不抛错', () => {
    const store = memoryStorage({ throwing: true })
    expect(loadLoginPrefs('admin', store)).toEqual({ remember: true, account: '' })
    expect(() => saveLoginPrefs('admin', { remember: false, account: 'x' }, store)).not.toThrow()
  })
})

describe('saveLoginPrefs', () => {
  it('账号为空时不覆盖已记住的账号', () => {
    const store = memoryStorage()
    saveLoginPrefs('staff', { remember: true, account: '13800138000' }, store)
    saveLoginPrefs('staff', { remember: true, account: '' }, store)
    expect(loadLoginPrefs('staff', store)).toEqual({ remember: true, account: '13800138000' })
  })

  it('取消勾选会落盘为不记住', () => {
    const store = memoryStorage()
    saveLoginPrefs('admin', { remember: false, account: 'boss' }, store)
    expect(store.dump()['luyun.login.admin.remember']).toBe('0')
  })
})

describe('登录面板上次选的那一栏（票 03）', () => {
  it('没记住过时默认员工栏：员工手机一打开就落在员工栏', () => {
    expect(DEFAULT_LOGIN_TAB).toBe('staff')
    expect(loadLoginTab(memoryStorage())).toBe('staff')
  })

  it('沿用同一套键名前缀与命名空间风格，不另造一套', () => {
    const store = memoryStorage()
    saveLoginTab('admin', store)
    // 键名跟 loadLoginPrefs/saveLoginPrefs 同一族：`luyun.login.<namespace>.<field>`。
    expect(store.dump()['luyun.login.panel.tab']).toBe('admin')
    expect(loadLoginTab(store)).toBe('admin')
    saveLoginTab('staff', store)
    expect(loadLoginTab(store)).toBe('staff')
  })

  it('脏值回落到默认栏', () => {
    const store = memoryStorage()
    store.setItem('luyun.login.panel.tab', 'boss')
    expect(loadLoginTab(store)).toBe('staff')
  })

  it('存储不可用时退化为默认值而不抛错', () => {
    const store = memoryStorage({ throwing: true })
    expect(loadLoginTab(store)).toBe('staff')
    expect(() => saveLoginTab('admin', store)).not.toThrow()
  })

  it('栏位偏好与「记住登录」两套字段互不影响', () => {
    const store = memoryStorage()
    saveLoginTab('admin', store)
    saveLoginPrefs('staff', { remember: false, account: '13800138000' }, store)
    expect(loadLoginTab(store)).toBe('admin')
    expect(loadLoginPrefs('staff', store)).toEqual({ remember: false, account: '13800138000' })
  })
})
