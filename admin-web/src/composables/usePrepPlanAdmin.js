/** 备货计划页面里「只有管理端才有」的那几个入口的判据（票 08）。
 *
 *  `PrepPlanView` 从管理后台的 `/prep-plan` 搬进工作台的「后勤」组之后，**两种身份都能
 *  打开它**（页面清单里是 `audience: both`）—— 但备货计划的写操作仍然只认管理端
 *  （ADR 0092：进工作台只是换位置与统一导航，**不扩权**）。读接口走「任一身份」门、
 *  写接口照旧只认管理端，页面上能勾、能保存、能生成的控件则在员工这一档全部收起。
 *
 *  形态照票 07 的 `useRecipeAdmin.js`（同一个身份模型、同一个 fail-closed 取向），
 *  区别只在消费方怎么用：配方那边是「显示/不显示管理入口」，这边是「显示/不显示写控件」。
 *
 *  两个刻意的取舍：
 *  - **探针没出结论时按只读算**（`identity` 是 `null`）：宁可先收起写控件，也不让后厨在
 *    那一瞬间看到一颗点下去就 401 的按钮。管理端少半拍的写入口，比员工点错一次便宜。
 *  - **只降不升照旧**：这台设备记住的是员工档时，即便管理端会话还挂着也不给写入口
 *    （`resolveWorkbenchIdentity` 的规则）。要改计划，去套着工作台外壳的备货计划页顶栏
 *    切回超级管理员。
 *
 *  **只看身份、不看会话里有什么角色**：员工是花名册上的一档卫生权限，与备货计划的写权限
 *  无关（spec 的 Out of Scope 明确不做「员工勾备好了」）。
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
