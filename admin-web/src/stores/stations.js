import { defineStore } from 'pinia'
import { api } from '../api/client'

// 阶段三修复：档口常量原来在 public/index.html 里以 6 处硬编码（STATIONS_MAP /
// STATION_NAMES / showQuickAdd 里的局部 stations 等）维护，容易与 config.py 的
// KITCHEN_STATIONS 漂移。这里统一从 /api/stations 拉取一次并缓存，全局共用。
export const useStationsStore = defineStore('stations', {
  state: () => ({
    list: [],
    loaded: false,
  }),
  getters: {
    byId(state) {
      const map = {}
      for (const s of state.list) map[s.id] = s
      return map
    },
    nameOf() {
      return (id) => this.byId[id]?.name || id || '未分类'
    },
    colorOf() {
      return (id) => this.byId[id]?.color || '#6b7280'
    },
  },
  actions: {
    async load(force = false) {
      if (this.loaded && !force) return
      const stations = await api.get('/api/stations')
      this.list = Array.isArray(stations) ? stations : []
      this.loaded = true
    },
  },
})
