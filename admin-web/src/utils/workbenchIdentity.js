/** 工作台身份（票 04）：**当前身份的读取、记忆与切换**，以及降级判定（只降不升）。
 *
 *  它只回答「此刻以谁的身份在看」—— 决定视图与导航面。**不决定权限**：页面能不能打开
 *  还是路由守卫（`meta.audience`）与服务端页面墙说了算，切换器一个字都不改那两条链。
 *
 *  两档（领域词表口径，见 spec 的技术口径）：
 *  - `super`  —— 管理端那个**共享账号**。界面上写「超级管理员」：词表里的「管理员」是
 *    花名册上某个真人的卫生权限档位，同名会打架，所以这一档**不叫「管理员」**。
 *  - `staff` —— 员工会话（花名册上的某个真人），界面上带出他自己的姓名。
 *
 *  **只降不升**（共用电脑上的底线，spec 故事 6）：记忆值是「员工」时，**只要员工会话还在**，
 *  哪怕这台设备上还挂着店长的会话，也绝不自作主张升上去；记忆值是「超级管理员」而那个
 *  会话失效了、员工会话还在，才降到员工并提示一次。用户手动点的那一下不算自动，两档都能点。
 *  员工会话**已经不在**（登出 / 过期）是另一回事：那一档一格都打不开，记忆值失去载体，
 *  退回真实可用的那一档 —— 见 `resolveWorkbenchIdentity` 里那一支的注释。
 *
 *  记忆按**设备**存（`localStorage`，键名沿用 `luyun.login.*` 那一族，跟
 *  `loginPrefs.js` 同一套读写与降级写法）。没记忆值时按「哪个会话有效」自动定：两套都
 *  有效定超级管理员，只有员工会话定员工。
 *
 *  **记忆值就是下限**：记的是员工、员工会话也在，就一直显示员工（哪怕这台设备上还挂着
 *  店长的会话）；记的是超级管理员则顶多降到员工。于是「只降不升」在这张判定表里是可以
 *  逐行核对的一条不变式，而不是散在分支里的约定。另一条不变式同样可核对：**定出来的
 *  那一档（认得出时）一定在 `available` 里** —— 切换器按 `available` 选、导航面按
 *  `identity` 渲染，两者一分歧，界面上就会出现"顶栏写着超级管理员、内容与底栏却是员工档"。
 */

/** 管理端共享账号那一档。值不是 'admin'：这一档在界面上叫「超级管理员」。 */
export const IDENTITY_ADMIN = 'super'
/** 员工那一档。 */
export const IDENTITY_STAFF = 'staff'

/** 本机记忆的键：`loginPrefs.js` 的 `${KEY_PREFIX}${namespace}.${field}` 同族。 */
const STORAGE_KEY = 'luyun.login.workbench.identity'

function resolveStorage(storage) {
  if (storage) return storage
  try {
    // 隐私模式下访问 window.localStorage 本身就会抛错，降级为「记不住选择」。
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

/**
 * 读回这台设备记住的那一档。
 *
 * 只认两个值：脏值（手改过、别的版本写过）一律当作**没选过** —— 猜错了会把员工
 * 送进店长视角，宁可回落到自动判定。
 *
 * @param {Storage} [storage] 可注入的存储实现，便于测试
 * @returns {'super' | 'staff' | null}
 */
export function loadWorkbenchIdentity(storage) {
  const store = resolveStorage(storage)
  if (!store) return null
  let raw = null
  try {
    raw = store.getItem(STORAGE_KEY)
  } catch {
    return null
  }
  return raw === IDENTITY_ADMIN || raw === IDENTITY_STAFF ? raw : null
}

/**
 * 记住这一档（用户点了切换、或自动判定出一个有效身份时调用）。
 *
 * @param {'super' | 'staff'} identity
 * @param {Storage} [storage] 可注入的存储实现，便于测试
 */
export function saveWorkbenchIdentity(identity, storage) {
  if (identity !== IDENTITY_ADMIN && identity !== IDENTITY_STAFF) return
  const store = resolveStorage(storage)
  if (!store) return
  try {
    store.setItem(STORAGE_KEY, identity)
  } catch {
    // 配额满或隐私模式写不进去：不影响本次使用，只是下次不再记住。
  }
}

/**
 * 给定「两套会话各自有没有效」与「这台设备记住了什么」，定出此刻的身份。
 *
 * 纯函数：不碰存储、不发请求 —— 探针与落盘分别由调用方（store / 外壳）负责，
 * 于是这张判定表可以逐行对着票面口径核对。
 *
 * 三样东西各是一条口径，别混：
 * - `identity`  —— 此刻显示哪一档。**记忆值就是它的下限**：记着员工、员工会话也在，就
 *   一直是员工（哪怕这台设备上还挂着店长的会话）；记着超级管理员则顶多降到员工。反过来，
 *   记忆值那一档的会话已经不在时下限失效，退回 `available` 里那一档。**认得出时
 *   `identity` 一定在 `available` 里**（两条渲染路径共用这一条不变式）。
 * - `available` —— 此刻哪些档**点得动**：由有效会话决定（与 `identity` 无关）。
 *   记忆值低于有效会话时（店里那台共用电脑上选了员工），两档都还在，员工可以自己点回去。
 * - `remembered` —— 落盘要写的值。自动判定出的那一档当场记下（首次进来就记住）；而
 *   「记着超级管理员 → 降级成员工」时它改成员工，**下一次打开不会又升回去**，要升得
 *   用户自己点。认不出身份时保留原值：别把用户的选择写成空。
 *
 * @param {{ adminValid?: boolean, staffValid?: boolean, saved?: 'super'|'staff'|null }} input
 * @returns {{
 *   identity: 'super'|'staff'|null,
 *   available: Array<'super'|'staff'>,
 *   downgraded: boolean,
 *   remembered: 'super'|'staff'|null,
 * }}
 *   `downgraded` 只在「记忆的是超级管理员、那个会话失效、员工会话还在」时为真 ——
 *   调用方据此提示一次，别的情况不提示。
 */
export function resolveWorkbenchIdentity({ adminValid = false, staffValid = false, saved = null } = {}) {
  const remembered = saved === IDENTITY_ADMIN || saved === IDENTITY_STAFF ? saved : null

  const available = []
  if (adminValid) available.push(IDENTITY_ADMIN)
  if (staffValid) available.push(IDENTITY_STAFF)

  // 这一趟定出来的档 + 落盘要写的值（没定出档时保留记忆值，别把用户的选择写成空）。
  let identity = null
  let nextRemembered = remembered

  if (remembered === IDENTITY_STAFF) {
    // 记忆值是「员工」：**员工会话还在时绝不自动升** —— 哪怕店长的会话挂在这台设备上，
    // 反向自动升级正是在这一支被挡掉的（共用电脑上的底线，spec 故事 6）。
    //
    // 员工会话**已经不在了**（登出 / 过期 / 被服务端清）则是另一回事：员工档此刻一格都
    // 打不开（守卫会把每一页踢回登录页），「记忆值就是下限」失去了载体 —— 硬撑一档会让
    // 界面自相矛盾：切换器按 `available` 显示「超级管理员」，导航面按 `identity` 渲染员工档
    // （首页整屏「我的班」、底栏没有「人事」）。这时退回唯一可用的那一档；两套都没有才
    // 认不出身份。**这不是"自动升"**：升的判据是"同一个人的两档会话同时有效"，
    // 而这里员工那一档根本没会话。落盘值不动（见下面 `remembered`）。
    if (staffValid) {
      identity = IDENTITY_STAFF
    } else if (adminValid) {
      identity = IDENTITY_ADMIN
    }
  } else if (remembered === IDENTITY_ADMIN) {
    // 记忆值是「超级管理员」：有效就用；失效而员工会话在 → 降级（要提示）并改记员工，
    // 于是它成了新的下限，店长会话哪天自己回来也不会把这一档顶回去。
    if (adminValid) {
      identity = IDENTITY_ADMIN
    } else if (staffValid) {
      identity = IDENTITY_STAFF
      nextRemembered = IDENTITY_STAFF
    }
  } else {
    // 没选过（或脏值）：按哪个会话有效自动定，两套都在优先超级管理员。
    // 自动定出来的这一档**当场记下**：下次进来就是「记住的选择」，不用重选一次
    // （票面「默认档记住在本机」）。它只是下限，用户随时能手动换成另一档。
    identity = adminValid ? IDENTITY_ADMIN : (staffValid ? IDENTITY_STAFF : null)
    nextRemembered = identity === null ? null : identity
  }

  return {
    identity,
    available,
    downgraded: remembered === IDENTITY_ADMIN && !adminValid && staffValid,
    remembered: identity === null ? remembered : nextRemembered,
  }
}
