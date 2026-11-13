import { defineStore } from 'pinia'
import { api } from '../api/client'

/** 任务待验收还有几条 —— 手机上底栏「任务」那一格的角标（票 07）。
 *
 *  为什么单独开一个 store（与 `schedulingPending` 同一个理由）：底栏挂在每一页上，
 *  那些页面并不都读任务板；放这里一处拉，谁问都给同一份，nudge 来了也只刷这一处。
 *
 *  `count === null` 是「还没读出来」，不是「0 条」：读不到时不标角标。
 *  只有管理端会话读（任务板接口对员工会话是 403；员工端任务数在首页自己的卡上）。
 */
export const useTaskPendingStore = defineStore('taskPending', {
  state: () => ({
    count: null,
    inflight: false,
  }),
  getters: {
    /** 有待验收吗 —— `null`（没读到）不算。 */
    hasPending(state) {
      return typeof state.count === 'number' && state.count > 0
    },
  },
  actions: {
    async refresh() {
      if (this.inflight) return
      this.inflight = true
      try {
        const data = await api.get('/api/tasks/board', { counts_only: 1 })
        const value = data && data.counts ? data.counts.pending_review : null
        this.count = typeof value === 'number' ? value : null
      } catch {
        // 没权限 / 没迁移 / 网络：不标角标，也不留上一份旧数骗人。
        this.count = null
      } finally {
        this.inflight = false
      }
    },
  },
})
