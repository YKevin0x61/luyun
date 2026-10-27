import { computed, ref } from 'vue'
import { api } from '../api/client'

/** 备货计划（ADR 0117）的读路径与几个写动作。
 *
 *  进页面就要出清单（不再有「先点刷新」的空态）：`load()` 一进来就跑，
 *  后端按「现在 → 打烊」现算，勾选与手改落在当日计划里。
 */
export function usePrepPlan() {
  const state = ref('idle') // idle | loading | ready | error
  const errorText = ref('')
  const planDay = ref('')
  const windowStart = ref('')
  const windowEnd = ref('')
  const closed = ref(false)
  const entries = ref([])
  const missingRule = ref([])
  const isStaff = ref(false)
  const needsStationPick = ref(false)
  const staffStation = ref('')
  const stationOptions = ref([])
  const activeStation = ref('')
  const expandedId = ref(null)
  const busyEntryId = ref(null)

  const remainingEntries = computed(() => entries.value.filter((row) => !row.done_at))
  const doneEntries = computed(() => entries.value.filter((row) => row.done_at))

  /** 备货计划按**岗位**分组（案板 / 熟笼 / 西饼…）：`station` 是 POS 的档口维度，
   *  后厨怎么分工看的是备货品清单里的 `position`。 */
  function stationKey(entry) {
    return entry.position || 'uncategorized'
  }

  function stationLabel(key) {
    if (key === 'uncategorized') return '未分类'
    const match = stationOptions.value.find((row) => row.id === key)
    return match ? match.label : key
  }

  /** 档口页签：只列真的有品的档口，按 `KITCHEN_STATIONS` 的顺序排。 */
  const stationTabs = computed(() => {
    const counts = new Map()
    for (const entry of entries.value) {
      const key = stationKey(entry)
      const row = counts.get(key) || { id: key, total: 0, remaining: 0 }
      row.total += 1
      if (!entry.done_at) row.remaining += 1
      counts.set(key, row)
    }
    // 顺序由后端给的岗位清单决定（后厨动线：熟笼 → 馅档 → 案板 → 西饼 → 肠粉 → 明档 → 煎炸）
    const order = stationOptions.value.map((row) => row.id)
    const rank = (id) => {
      const index = order.indexOf(id)
      return index === -1 ? order.length : index
    }
    return [...counts.values()]
      .map((row) => ({ ...row, label: stationLabel(row.id) }))
      .sort((a, b) => {
        // 「未分类」永远排最后：它是收容所，不是第一个该看的组
        if (a.id === 'uncategorized') return 1
        if (b.id === 'uncategorized') return -1
        return rank(a.id) - rank(b.id)
      })
  })

  const activeEntries = computed(() =>
    entries.value.filter((entry) => stationKey(entry) === activeStation.value),
  )

  const todoInStation = computed(() => activeEntries.value.filter((entry) => !entry.done_at))
  const doneInStation = computed(() => activeEntries.value.filter((entry) => entry.done_at))

  /** 档口内两段：半成品 / 原料（顺序由后端给：按建议量降序）。
   *  展示用「半成品 / 原料」；落库仍是 kind = made / bought（历史枚举，不为改文案迁库）。 */
  const groups = computed(() =>
    [
      { kind: 'made', label: '半成品' },
      { kind: 'bought', label: '原料' },
    ]
      .map((group) => ({
        ...group,
        entries: todoInStation.value.filter((entry) => entry.kind === group.kind),
      }))
      .filter((group) => group.entries.length),
  )

  const remainingCount = computed(() => remainingEntries.value.length)

  /** 员工选岗位用：后端随清单下发的岗位清单（备货品里实际出现的岗位）。 */
  const allStations = computed(() => stationOptions.value)

  function applyPayload(data) {
    planDay.value = data.plan_day || ''
    windowStart.value = data.window?.start || ''
    windowEnd.value = data.window?.end || ''
    closed.value = Boolean(data.window?.closed)
    entries.value = data.entries || []
    stationOptions.value = Array.isArray(data.stations) ? data.stations : []
    missingRule.value = data.missing_rule || []
    isStaff.value = Boolean(data.staff)
    needsStationPick.value = Boolean(data.needs_station_pick)
    staffStation.value = data.staff_station || ''
    if (isStaff.value && staffStation.value) activeStation.value = staffStation.value
    if (!activeStation.value || !stationTabs.value.some((tab) => tab.id === activeStation.value)) {
      const firstTodo = stationTabs.value.find((tab) => tab.remaining > 0)
      activeStation.value = (firstTodo || stationTabs.value[0] || {}).id || ''
    }
  }

  async function load() {
    state.value = 'loading'
    errorText.value = ''
    try {
      // 先问清单再决定要不要拉管理端档口：员工会话调 `/api/stations` 是 401，
      // 而后端已经把「全部后厨档口」随清单下发，员工那一档不需要那条接口。
      const data = await api.get('/api/prep-plan/today')
      applyPayload(data)
      state.value = 'ready'
    } catch (err) {
      state.value = 'error'
      errorText.value = err.message || '清单读不出来'
    }
  }

  function replaceEntry(patch) {
    entries.value = entries.value.map((entry) =>
      entry.id === patch.id ? { ...entry, ...patch } : entry,
    )
  }

  async function toggleDone(entry) {
    busyEntryId.value = entry.id
    errorText.value = ''
    try {
      const done = !entry.done_at
      await api.patch(`/api/prep-plan/today/entries/${entry.id}`, { done })
      replaceEntry({ id: entry.id, done_at: done ? new Date().toISOString() : null })
    } catch (err) {
      errorText.value = err.message || '标记失败'
    } finally {
      busyEntryId.value = null
    }
  }

  async function setQty(entry, qty) {
    const value = Number(qty)
    if (!Number.isFinite(value) || value < 0) return
    busyEntryId.value = entry.id
    errorText.value = ''
    try {
      await api.patch(`/api/prep-plan/today/entries/${entry.id}`, { final_qty: value })
      replaceEntry({ id: entry.id, final_qty: value, qty_source: 'manual' })
    } catch (err) {
      errorText.value = err.message || '改数量失败'
    } finally {
      busyEntryId.value = null
    }
  }

  async function pickStation(stationId) {
    errorText.value = ''
    try {
      await api.post('/api/prep-plan/staff-station', { station: stationId })
      staffStation.value = stationId
      needsStationPick.value = false
      await load()
    } catch (err) {
      errorText.value = err.message || '选档口失败'
    }
  }

  async function setKind(entry, kind) {
    busyEntryId.value = entry.id
    errorText.value = ''
    try {
      await api.patch(`/api/prep-plan/items/${entry.prep_item_id}`, { kind })
      await load()
    } catch (err) {
      errorText.value = err.message || '改归属失败（只有管理端能改）'
    } finally {
      busyEntryId.value = null
    }
  }

  /** 员工换档口：把选择面板放回来，选完 pickStation 会重新拉一份清单。 */
  function requestStationChange() {
    needsStationPick.value = true
  }

  function toggleExpand(entry) {
    expandedId.value = expandedId.value === entry.id ? null : entry.id
  }

  return {
    state,
    errorText,
    planDay,
    windowStart,
    windowEnd,
    closed,
    entries,
    missingRule,
    isStaff,
    needsStationPick,
    staffStation,
    activeStation,
    expandedId,
    busyEntryId,
    stationTabs,
    allStations,
    groups,
    remainingCount,
    doneCount: computed(() => doneEntries.value.length),
    doneInStation,
    load,
    toggleDone,
    setQty,
    pickStation,
    setKind,
    requestStationChange,
    toggleExpand,
  }
}
