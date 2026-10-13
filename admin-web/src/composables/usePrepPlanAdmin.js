/** 备货计划页面里「只有管理端才有」的那几个入口的判据（票 08）。
 *
 *  `PrepPlanView` 从管理后台的 `/prep-plan` 搬进工作台的「后勤」组之后，**两种身份都能
 *  打开它**（页面清单里是 `audience: both`）。身份判据本身没变（ADR 0092：进工作台只是
 *  换位置与统一导航，**不扩权**）。
 *
 *  **这一页现在没有写控件了**：备货数量不登记、每次计算都按 0 库存，于是登记 / 多做一笔 /
 *  报废 / 撤销那一套前端整套删掉，管理端与员工在这一页能做的是同一件事。所以这个判据在
 *  页面上只剩一个用处：**顶上那句「只读」说给谁听** —— 管理端本来就没有能改的东西，
 *  不必再解释一遍。
 *
 *  两个刻意的取舍：
 *  - **探针没出结论时按只读算**（`identity` 是 `null`）：宁可先按员工算，也不要在那一瞬间
 *    把管理端的身份当成已知。这条现在只影响一句话的显隐，但取向不变。
 *  - **只降不升照旧**：这台设备记住的是员工档、而员工会话还在时，即便管理端会话也挂着
 *    也按员工算（`resolveWorkbenchIdentity` 的规则）。（员工会话已经不在时记忆值失去载体 ——
 *    身份按唯一可用的那一档走，见那个函数的注释。）
 *
 *  **只看身份、不看会话里有什么角色**：员工是花名册上的一档卫生权限，与备货计划无关。
 */
import { computed } from 'vue'
import { useWorkbenchIdentityStore } from '../stores/workbenchIdentity'
import { IDENTITY_ADMIN } from '../utils/workbenchIdentity'

export function usePrepPlanAdmin() {
  const identityStore = useWorkbenchIdentityStore()
  // 备货计划页套在工作台外壳里，探针多半已由外壳开场；这里再叫一次是幂等的
  // （`refresh` 在探的时候直接返回，同一页里几个组件共用这一份结果）。
  void identityStore.refresh()

  const readOnly = computed(() => identityStore.identity !== IDENTITY_ADMIN)
  return { identityStore, readOnly }
}
