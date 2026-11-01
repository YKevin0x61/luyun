import { defineStore } from 'pinia'
import { api } from '../api/client'

/** 待批的请假 / 换班还有几条 —— 手机上「人事」那一格的角标，以及「有待批就直接落待办页」
 *  这条便捷规则，都照这个数走（2026-11-02 店长要求「审批入口更便捷」）。
 *
 *  为什么单独开一个 store：这个数是**跨页**的。底栏在每一页都挂着，而那些页面并不都读
 *  `/api/scheduling/inbox`（月历读、今天页读、卫生页不读）。放页面里就得每页各拉一次；
 *  放这里一个地方拉，谁问都给同一份，nudge 来了也只刷这一处。
 *
 *  `count === null` 是「还没读出来」，不是「0 条」：读不到时角标不出现、「人事」也不改
 *  落点 —— 不能因为没读到就把店长默认送到一页空白待办。
 *
 *  谁有资格读：管理端会话，以及花名册上开了「人事管理」（`schedule`）的员工。其余员工
 *  请求会被服务端 403，所以调用方要先按能力键判一次（见 `WorkbenchTabBar.vue`）。
 */
export const useSchedulingPendingStore = defineStore('schedulingPending', {
  state: () => ({
    count: null,
    /** 最近一次读到的原始申请行（待办页与底栏口径一致，不各自再解析一遍）。 */
    requests: [],
    inflight: false,
  }),
  getters: {
    /** 有待批吗 —— `null`（没读到）不算。 */
    hasPending(state) {
      return typeof state.count === 'number' && state.count > 0
    },
  },
  actions: {
    async refresh() {
      if (this.inflight) return
      this.inflight = true
      try {
        const data = await api.get('/api/scheduling/inbox')
        const rows = data && Array.isArray(data.requests) ? data.requests : []
        this.requests = rows
        this.count = rows.length
      } catch (err) {
        // 读不到（没权限 / 网络）就当没数：不标角标、不改落点，也不把上一份旧数留着骗人。
        this.requests = []
        this.count = null
      } finally {
        this.inflight = false
      }
    },
  },
})
