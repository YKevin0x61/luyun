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
  // 记的是员工：**员工会话还在时反向绝不自动升**，哪怕店长的会话就挂在这台设备上
  ['both', IDENTITY_STAFF, IDENTITY_STAFF, false, [IDENTITY_ADMIN, IDENTITY_STAFF], IDENTITY_STAFF],
  // ……但员工会话已经不在（登出 / 过期 / 被清）时，记忆值失去了载体：员工档一格都打不开
  // （守卫会把每一页踢回登录页），再拿它当「我是谁」就是撒谎。退回唯一可用的那一档 ——
  // 与切换器 `current` 的兜底同一条口径（`WorkbenchIdentitySwitcher.test.js` 的
  // 「只有管理端会话：只显示超级管理员」）。落盘值保持 staff：下一个人以员工身份回来
  // （员工会话重新有效）时，这一档照样直接生效，不会撒谎说"管理端登录已过期"。
  ['admin', IDENTITY_STAFF, IDENTITY_ADMIN, false, [IDENTITY_ADMIN], IDENTITY_STAFF],
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

  it('两条不变式逐个核：员工会话在时不升档；定出来的档必须真的可用', () => {
    const rank = { [IDENTITY_STAFF]: 0, [IDENTITY_ADMIN]: 1 }
    for (const [sessions, saved] of DECISIONS) {
      const staffValid = sessions === 'both' || sessions === 'staff'
      const { identity, available } = resolveWorkbenchIdentity({
        adminValid: sessions === 'both' || sessions === 'admin',
        staffValid,
        saved,
      })
      if (identity === null) continue
      // (b) 认出来的这一档必须落在可用档位里：切换器按 available 选、导航面按 identity
      // 渲染，两者一旦分歧，员工档的界面就会挂在一台只有管理端会话的设备上。
      expect(available).toContain(identity)
      // (a) 只降不升：员工会话还在时，定出来的一档绝不比记忆值更高。
      if (staffValid) {
        expect(rank[identity]).toBeLessThanOrEqual(rank[saved] ?? rank[IDENTITY_ADMIN])
      }
    }
  })

  it('员工登出后超级管理员登录（记忆员工 + 只剩管理端会话）→ 按超级管理员显示', () => {
    // 「普通员工退出登录 → 超管在同一台设备登录」的原样复现：登出只作废探针结论，
    // 本机记忆还是 staff，而员工会话已经清掉了 —— 这时不能再拿员工档当身份，
    // 否则工作台首页与底栏都按员工渲染（首页整屏「我的班」，底栏没有「人事」），
    // 顶栏却写着「超级管理员」，而且没有员工会话、员工页一格都进不去。
    const result = resolveWorkbenchIdentity({
      adminValid: true,
      staffValid: false,
      saved: IDENTITY_STAFF,
    })

    expect(result.identity).toBe(IDENTITY_ADMIN)
    expect(result.available).toEqual([IDENTITY_ADMIN])
    // 落盘值不动：别把用户记下的这一档写坏（员工下次回来照样直接生效）。
    expect(result.remembered).toBe(IDENTITY_STAFF)
    expect(result.downgraded).toBe(false)
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
