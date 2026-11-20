import { defineStore } from 'pinia'
import { api } from '../api/client'

// 卫生的一天：日界在服务端（services/hygiene/day.py，00:00 切，ADR 0125），前端只
// 缓存、不自己算。曾经几页各用 new Date() 各算一份，于是 00:00-06:00 页面算今天、
// 存档算昨天，照片从「今天」的窗口里消失；现在浏览器只负责把服务端给的日期填进
// 默认值，时区与日界都不回到客户端。
//
// 过期判据是服务端给的 day_end（次日 00:00+08:00）：页面开着跨过零点时，下一次读
// 会重新取一次，而不是把昨天的日期一直用下去。
let pendingLoad = null

export const useHygieneDayStore = defineStore('hygieneDay', {
  state: () => ({
    today: '',
    dayEnd: '',
    weekday: null,
    weekFrom: '',
    weekTo: '',
    loaded: false,
    expiresAt: 0,
  }),
  getters: {
    fresh(state) {
      return state.loaded && state.expiresAt > Date.now()
    },
    // 页面挂载时 store 还没回来，realtime 的 pull 可能先跑：那一刻 today 还是空串，
    // 拿它算出来的范围是空的（专项卫生的日历接口会 422）。调用方先看 ready 再取数。
    ready(state) {
      return Boolean(state.today)
    },
  },
  actions: {
    async load(force = false) {
      if (!force && this.fresh) return this
      if (pendingLoad) return pendingLoad
      pendingLoad = (async () => {
        const data = await api.get('/api/hygiene/today')
        this.today = data.today
        this.dayEnd = data.day_end
        this.weekday = data.weekday
        this.weekFrom = data.week_from
        this.weekTo = data.week_to
        // 解析不了就当过期：宁可多取一次，也不把过期的「今天」留在页面上。
        this.expiresAt = Date.parse(this.dayEnd) || 0
        this.loaded = true
        return this
      })()
      try {
        return await pendingLoad
      } finally {
        pendingLoad = null
      }
    },
  },
})
