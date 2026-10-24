import { ref } from 'vue'
import { api } from '../api/client'

/** 补货 tab（ADR 0118）：在库量不够再卖 1 小时的品，点一下就「又做了一批」。 */
export function usePrepRestock() {
  const state = ref('idle')
  const errorText = ref('')
  const rows = ref([])
  const closed = ref(false)
  const needsStationPick = ref(false)
  const staffStation = ref('')
  const stations = ref([])
  const busyId = ref(null)

  async function load() {
    state.value = 'loading'
    errorText.value = ''
    try {
      const data = await api.get('/api/prep-plan/restock')
      rows.value = data.rows || []
      closed.value = Boolean(data.closed)
      needsStationPick.value = Boolean(data.needs_station_pick)
      staffStation.value = data.staff_station || ''
      if (!stations.value.length) {
        try {
          const today = await api.get('/api/prep-plan/today')
          stations.value = today.stations || []
        } catch {
          stations.value = []
        }
      }
      state.value = 'ready'
    } catch (err) {
      state.value = 'error'
      errorText.value = err.message || '补货提醒读不出来'
    }
  }

  async function produce(row, qty) {
    if (row.entry_id == null) return
    busyId.value = row.entry_id
    errorText.value = ''
    try {
      // 勾 = 又做了一批，产量累加进在库量
      await api.patch(`/api/prep-plan/today/entries/${row.entry_id}`, {
        done: true,
        produced_qty: Number(qty) || 0,
      })
      await load()
    } catch (err) {
      errorText.value = err.message || '记产量失败'
    } finally {
      busyId.value = null
    }
  }

  async function waste(row, qty, reason) {
    busyId.value = row.entry_id
    errorText.value = ''
    try {
      await api.post('/api/prep-plan/waste', {
        prep_item_id: row.prep_item_id,
        quantity: Number(qty) || 0,
        reason,
      })
      await load()
    } catch (err) {
      errorText.value = err.message || '报废登记失败'
    } finally {
      busyId.value = null
    }
  }

  async function pickStation(stationId) {
    errorText.value = ''
    try {
      await api.post('/api/prep-plan/staff-station', { station: stationId })
      needsStationPick.value = false
      await load()
    } catch (err) {
      errorText.value = err.message || '选档口失败'
    }
  }

  /** 员工换档口：把选择面板放回来。 */
  function requestStationChange() {
    needsStationPick.value = true
  }

  return {
    requestStationChange,
    state,
    errorText,
    rows,
    closed,
    needsStationPick,
    staffStation,
    stations,
    busyId,
    load,
    produce,
    waste,
    pickStation,
  }
}
