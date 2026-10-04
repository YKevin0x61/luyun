import { describe, expect, it } from 'vitest'
import {
  IDENTITY_ADMIN,
  IDENTITY_STAFF,
  loadWorkbenchIdentity,
  resolveWorkbenchIdentity,
  saveWorkbenchIdentity,
} from '../workbenchIdentity.js'

// 票 04 的「工作台身份」：本机记忆（device-local）+ 只降不升的判定。
//
// 这一份只压两件与渲染无关的事：**记忆怎么存取**、**给定会话与记忆值该定哪一档**。
// 「显示成什么样」归 `WorkbenchIdentitySwitcher.test.js`（那一层按同一张决策表跑）。
// 期望值全部按票面口径写死（不重算一遍代码里的算式），否则测试只会跟着实现一起漂。

/** 内存版 Storage（照 `loginPrefs.test.js` 的写法），并可选模拟隐私模式。 */
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

describe('工作台身份的本机记忆', () => {
  it('没记过时给 null（「没选过」与「选过员工」是两回事）', () => {
    expect(loadWorkbenchIdentity(memoryStorage())).toBeNull()
  })

  it('存进去再读回来是同一位，键名走 luyun.login.* 那一族', () => {
    const store = memoryStorage()

    saveWorkbenchIdentity(IDENTITY_ADMIN, store)
    expect(loadWorkbenchIdentity(store)).toBe(IDENTITY_ADMIN)
    expect(store.dump()).toEqual({ 'luyun.login.workbench.identity': 'super' })

    saveWorkbenchIdentity(IDENTITY_STAFF, store)
    expect(loadWorkbenchIdentity(store)).toBe(IDENTITY_STAFF)
  })

  it('脏值（手改过 / 别的版本写过）当作没记过，不猜', () => {
    const store = memoryStorage()
    store.setItem('luyun.login.workbench.identity', 'admin')
    expect(loadWorkbenchIdentity(store)).toBeNull()
  })

  it('存储不可用（隐私模式 / 配额满）时不抛错，只是记不住', () => {
    const store = memoryStorage({ throwing: true })
    expect(loadWorkbenchIdentity(store)).toBeNull()
    expect(() => saveWorkbenchIdentity(IDENTITY_ADMIN, store)).not.toThrow()
  })
})

// 四类会话组合 × 记忆值 —— 票面那组用例的**判据表**：
//   会话   both（两套都在）| admin（只有管理端）| staff（只有员工）| none（都没有）
//   记忆   null（没选过）| 'super' | 'staff'
// 四列期望：定哪一档 / 是不是刚发生降级 / 哪些档点得动（= 会话有效的那几档）/ 落盘写什么。
// 「只降不升」的可检查形式就是：定出来的这一档，绝不比记忆值更高。
const DECISIONS = [
  // 没选过：按哪个会话有效自动定（两套都在 → 超级管理员）
  ['both', null, IDENTITY_ADMIN, false, [IDENTITY_ADMIN, IDENTITY_STAFF], IDENTITY_ADMIN],
  ['admin', null, IDENTITY_ADMIN, false, [IDENTITY_ADMIN], IDENTITY_ADMIN],
  ['staff', null, IDENTITY_STAFF, false, [IDENTITY_STAFF], IDENTITY_STAFF],
  ['none', null, null, false, [], null],
  // 记的是超级管理员：会话没了才降，降了要提示
  ['both', IDENTITY_ADMIN, IDENTITY_ADMIN, false, [IDENTITY_ADMIN, IDENTITY_STAFF], IDENTITY_ADMIN],
  ['admin', IDENTITY_ADMIN, IDENTITY_ADMIN, false, [IDENTITY_ADMIN], IDENTITY_ADMIN],
  ['staff', IDENTITY_ADMIN, IDENTITY_STAFF, true, [IDENTITY_STAFF], IDENTITY_STAFF],
  ['none', IDENTITY_ADMIN, null, false, [], IDENTITY_ADMIN],
  // 记的是员工：**反向绝不自动升**，哪怕店长的会话就挂在这台设备上
  ['both', IDENTITY_STAFF, IDENTITY_STAFF, false, [IDENTITY_ADMIN, IDENTITY_STAFF], IDENTITY_STAFF],
  ['admin', IDENTITY_STAFF, IDENTITY_STAFF, false, [IDENTITY_ADMIN], IDENTITY_STAFF],
  ['staff', IDENTITY_STAFF, IDENTITY_STAFF, false, [IDENTITY_STAFF], IDENTITY_STAFF],
  ['none', IDENTITY_STAFF, null, false, [], IDENTITY_STAFF],
]

describe('工作台身份的判定（只降不升）', () => {
  for (const [sessions, saved, identity, downgraded, available, remembered] of DECISIONS) {
    const name = `会话 ${sessions} + 记忆 ${saved === null ? '没选过' : saved}`
      + ` → ${identity === null ? '无身份' : identity}`
      + `${downgraded ? '（降级并提示）' : ''}`

    it(name, () => {
      const result = resolveWorkbenchIdentity({
        adminValid: sessions === 'both' || sessions === 'admin',
        staffValid: sessions === 'both' || sessions === 'staff',
        saved,
      })

      expect(result.identity).toBe(identity)
      expect(result.downgraded).toBe(downgraded)
      expect(result.available).toEqual(available)
      expect(result.remembered).toBe(remembered)
    })
  }

  it('只降不升：定出来的一档绝不比记忆值更高（12 种组合逐个核）', () => {
    const rank = { [IDENTITY_STAFF]: 0, [IDENTITY_ADMIN]: 1 }
    for (const [sessions, saved] of DECISIONS) {
      const { identity } = resolveWorkbenchIdentity({
        adminValid: sessions === 'both' || sessions === 'admin',
        staffValid: sessions === 'both' || sessions === 'staff',
        saved,
      })
      if (identity === null) continue
      expect(rank[identity]).toBeLessThanOrEqual(rank[saved] ?? rank[IDENTITY_ADMIN])
    }
  })

  it('会话状态不明（探针没回来）时按没有算，不凭空定档', () => {
    const result = resolveWorkbenchIdentity({
      adminValid: false,
      staffValid: false,
      saved: IDENTITY_STAFF,
    })
    expect(result.identity).toBeNull()
    expect(result.available).toEqual([])
  })
})
