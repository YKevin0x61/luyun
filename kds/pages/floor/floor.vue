<template>
  <view class="floor-page" :class="{ 'floor-page--split': splitLayout }">
    <view v-if="disconnected" class="disconnect-banner">
      <text class="disconnect-banner-text">实时连接已断开，正在重连…</text>
    </view>
    <view class="page-header">
      <text class="back-link" @click="goBack">← 返回</text>
      <view class="header-titles">
        <text class="page-title">楼面控制台</text>
      </view>
      <view class="jump-box">
        <input
          class="jump-input"
          v-model="jumpQuery"
          type="text"
          placeholder="桌号"
          confirm-type="search"
          @confirm="submitJump"
        />
        <view class="jump-go" @click="submitJump">
          <text>去</text>
        </view>
      </view>
      <view class="refresh-btn" @click="refresh">
        <text>刷新</text>
      </view>
    </view>

    <view class="floor-body">
      <scroll-view v-show="showList" class="table-list" scroll-y>
        <view v-if="busy && tables.length === 0" class="empty">加载中…</view>
        <view v-else-if="tables.length === 0" class="empty">当前没有需要盯的堂食桌</view>
        <view v-else class="table-grid">
          <view
            v-for="table in tables"
            :key="table.table_number"
            class="table-card"
            :class="[
              'table-card--' + table.emphasis,
              { 'table-card--active': table.table_number === selectedTableNumber }
            ]"
            @click="openTable(table.table_number)"
          >
            <view class="table-card-head">
              <text class="table-name">{{ table.table_number }} 桌</text>
              <text v-if="table.stats.hasRush" class="rush-flag">急</text>
            </view>
            <view class="count-row">
              <text class="count-item">等叫 {{ table.stats.holdCount }}</text>
              <text class="count-item">在做 {{ table.stats.pendingWorkCount }}</text>
              <text class="count-item count-item--ready">待上菜 {{ table.stats.readyCount }}</text>
            </view>
          </view>
        </view>
      </scroll-view>

      <view v-if="showPane" class="table-pane">
        <view v-if="!selectedTable" class="empty pane-empty">点左侧桌卡</view>
        <scroll-view v-else class="pane-scroll" scroll-y>
          <view class="pane-header">
            <text class="pane-title">{{ selectedTable.table_number }} 桌</text>
            <view class="count-row">
              <text class="count-item">等叫 {{ selectedTable.stats.holdCount }}</text>
              <text class="count-item">在做 {{ selectedTable.stats.pendingWorkCount }}</text>
              <text class="count-item count-item--ready">待上菜 {{ selectedTable.stats.readyCount }}</text>
              <text v-if="selectedTable.stats.hasRush" class="rush-flag">急</text>
            </view>
          </view>
          <view
            v-for="group in selectedTable.groups"
            :key="selectedTable.table_number + '-' + group.dishName"
            class="dish-group"
          >
            <view class="dish-head">
              <text class="dish-name">{{ group.dishName }}</text>
              <text class="dish-station">{{ group.stationLabel }}</text>
            </view>
            <view
              v-for="line in group.lines"
              :key="line.order_id"
              class="line-row"
              :class="['line-row--' + phaseClass(line.phase), { 'line-row--locked': !isActionable(line) }]"
              @click="toggleLine(selectedTable.table_number, group.dishName, line.order_id, line)"
            >
              <text class="line-text">{{ floorLineChipText(line) }}</text>
              <text v-if="isSelected(selectedTable.table_number, group.dishName, line.order_id)" class="line-mark">✓</text>
            </view>
            <view class="actions">
              <view
                v-if="selectedHoldIds(selectedTable, group).length"
                class="action-btn hold"
                @click="runHold(selectedTable, group)"
              >
                <text>等叫 {{ selectedHoldIds(selectedTable, group).length }}</text>
              </view>
              <view
                v-if="selectedFireIds(selectedTable, group).length"
                class="action-btn fire"
                @click="runFire(selectedTable, group)"
              >
                <text>叫起 {{ selectedFireIds(selectedTable, group).length }}</text>
              </view>
              <view
                v-if="selectedRushIds(selectedTable, group).length"
                class="action-btn rush"
                @click="runRush(selectedTable, group)"
              >
                <text>加急 {{ selectedRushIds(selectedTable, group).length }}</text>
              </view>
            </view>
          </view>
        </scroll-view>
      </view>
    </view>
  </view>
</template>

<script>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { ordersAPI } from '../../api/orders.js'
import { useNudgePull } from '../../composables/useNudgePull.js'
import { useRealtimeStore } from '../../stores/realtime.js'
import { useStationsStore } from '../../stores/stations.js'
import { TimeCalculator } from '../../utils/timeCalculator.js'
import {
  canFire,
  canHold,
  canRush,
  decorateFloorTables,
  FLOOR_JUMP_MISS_TOAST,
  floorConflictsToastTitle,
  floorLineChipText,
  isActionable,
  isFloorSplitLayout,
  matchFloorTable,
  nextSelectedOrderIds,
  nextSelectedTableNumber,
  tableLeftToastTitle
} from '../../utils/floorConsole.js'

const SUBSCRIPTION_ID = 'kds-floor-orders'

function groupKey(tableNumber, dishName) {
  return `${tableNumber}::${dishName}`
}

export default {
  name: 'FloorConsolePage',
  setup() {
    const realtimeStore = useRealtimeStore()
    const stationsStore = useStationsStore()
    const busy = ref(false)
    const tables = ref([])
    const selectedByGroup = reactive({})
    const selectedTableNumber = ref('')
    const jumpQuery = ref('')
    const splitLayout = ref(false)
    let refreshQueued = false

    const disconnected = computed(() => realtimeStore.connectionStatus !== 'connected')
    const selectedTable = computed(() => {
      return tables.value.find((table) => table.table_number === selectedTableNumber.value) || null
    })
    const showList = computed(() => splitLayout.value || !selectedTableNumber.value)
    const showPane = computed(() => splitLayout.value || Boolean(selectedTableNumber.value))

    const clearSelection = () => {
      for (const key of Object.keys(selectedByGroup)) delete selectedByGroup[key]
    }

    const syncSelection = (nextTables) => {
      const nextKeys = new Set()
      for (const table of nextTables) {
        for (const group of table.groups) {
          const key = groupKey(table.table_number, group.dishName)
          nextKeys.add(key)
          const groupSeen = Object.prototype.hasOwnProperty.call(selectedByGroup, key)
          selectedByGroup[key] = nextSelectedOrderIds(selectedByGroup[key], group.lines, {
            groupSeen
          })
        }
      }
      for (const key of Object.keys(selectedByGroup)) {
        if (!nextKeys.has(key)) delete selectedByGroup[key]
      }
    }

    const withStationLabels = (decorated) => {
      return decorated.map((table) => ({
        ...table,
        groups: table.groups.map((group) => ({
          ...group,
          stationLabel:
            stationsStore.getStationById(group.lines[0]?.station)?.name || group.lines[0]?.station || ''
        }))
      }))
    }

    const refresh = async () => {
      if (busy.value) {
        refreshQueued = true
        return
      }
      busy.value = true
      try {
        do {
          refreshQueued = false
          await stationsStore.initializeStations()
          const data = await ordersAPI.getFloorConsole()
          const previous = selectedTableNumber.value
          const next = withStationLabels(decorateFloorTables(data?.tables || []))
          tables.value = next
          const kept = nextSelectedTableNumber(previous, next)
          if (previous && !kept) {
            selectedTableNumber.value = ''
            clearSelection()
            uni.showToast({ title: tableLeftToastTitle(previous), icon: 'none' })
          } else {
            selectedTableNumber.value = kept
          }
          syncSelection(next)
        } while (refreshQueued)
      } catch (error) {
        console.error('楼面刷新失败', error)
        uni.showToast({ title: '刷新失败', icon: 'error' })
      } finally {
        busy.value = false
      }
    }

    const todayDateStr = TimeCalculator.formatTime(new Date(), 'YYYY-MM-DD')
    useNudgePull({
      id: SUBSCRIPTION_ID,
      topics: ['orders'],
      filters: { date: todayDateStr },
      pull: refresh,
      fallback: 'reconcile'
    })

    const isSelected = (tableNumber, dishName, orderId) => {
      return (selectedByGroup[groupKey(tableNumber, dishName)] || []).includes(orderId)
    }

    const toggleLine = (tableNumber, dishName, orderId, line) => {
      if (line && !isActionable(line)) return
      const key = groupKey(tableNumber, dishName)
      const current = selectedByGroup[key] || []
      selectedByGroup[key] = current.includes(orderId)
        ? current.filter((id) => id !== orderId)
        : [...current, orderId]
    }

    const selectedOf = (table, group, pred) => {
      const selected = new Set(selectedByGroup[groupKey(table.table_number, group.dishName)] || [])
      return group.lines.filter((line) => selected.has(line.order_id) && pred(line)).map((line) => line.order_id)
    }

    const selectedHoldIds = (table, group) => selectedOf(table, group, canHold)
    const selectedFireIds = (table, group) => selectedOf(table, group, canFire)
    const selectedRushIds = (table, group) => selectedOf(table, group, canRush)

    const toastConflicts = (result) => {
      const title = floorConflictsToastTitle(result?.conflicts)
      if (!title) return
      uni.showToast({
        title,
        icon: 'none',
        duration: 2500
      })
    }

    const runAction = async (ids, apiFn, emptyTitle) => {
      if (!ids.length) {
        uni.showToast({ title: emptyTitle, icon: 'none' })
        return
      }
      try {
        const result = await apiFn(ids)
        toastConflicts(result)
        await refresh()
      } catch (error) {
        const detail = error?.response?.data?.detail
        const conflictTitle = floorConflictsToastTitle(
          typeof detail === 'object' ? detail?.conflicts : null
        )
        const reason = conflictTitle
          || (typeof detail === 'object' && detail?.message)
          || (typeof detail === 'string' && detail)
          || error?.message
          || '操作失败'
        uni.showToast({ title: String(reason), icon: 'none' })
      }
    }

    const runHold = (table, group) => runAction(selectedHoldIds(table, group), (ids) => ordersAPI.holdOrders(ids), '没有可等叫的份')
    const runFire = (table, group) => runAction(selectedFireIds(table, group), (ids) => ordersAPI.fireOrders(ids), '没有可叫起的份')
    const runRush = (table, group) => runAction(selectedRushIds(table, group), (ids) => ordersAPI.rushOrders(ids), '没有可加急的份')

    const phaseClass = (phase) => {
      if (phase === '等叫') return 'hold'
      if (phase === '在蒸') return 'steam'
      if (phase === '已制作待上菜') return 'ready'
      if (phase === '已取消') return 'cancel'
      return 'pending'
    }

    const openTable = (tableNumber) => {
      if (selectedTableNumber.value === tableNumber) return
      clearSelection()
      selectedTableNumber.value = tableNumber
      const table = tables.value.find((item) => item.table_number === tableNumber)
      if (table) syncSelection([table])
    }

    const closePane = () => {
      selectedTableNumber.value = ''
      clearSelection()
    }

    const submitJump = () => {
      const hit = matchFloorTable(tables.value, jumpQuery.value)
      if (!hit) {
        uni.showToast({ title: FLOOR_JUMP_MISS_TOAST, icon: 'none' })
        return
      }
      openTable(hit.table_number)
    }

    const goHome = () => {
      uni.reLaunch({ url: '/pages/index/index' })
    }

    const goBack = () => {
      if (!splitLayout.value && selectedTableNumber.value) {
        closePane()
        return
      }
      goHome()
    }

    const updateLayout = () => {
      const info = uni.getSystemInfoSync()
      splitLayout.value = isFloorSplitLayout({
        width: info.windowWidth,
        height: info.windowHeight
      })
    }

    onShow(() => {
      refresh()
    })
    onMounted(() => {
      realtimeStore.init()
      updateLayout()
      if (typeof uni.onWindowResize === 'function') {
        uni.onWindowResize(updateLayout)
      }
    })
    onUnmounted(() => {
      if (typeof uni.offWindowResize === 'function') {
        uni.offWindowResize(updateLayout)
      }
    })

    return {
      busy,
      tables,
      disconnected,
      splitLayout,
      jumpQuery,
      selectedTableNumber,
      selectedTable,
      showList,
      showPane,
      refresh,
      goBack,
      openTable,
      submitJump,
      isSelected,
      toggleLine,
      floorLineChipText,
      isActionable,
      selectedHoldIds,
      selectedFireIds,
      selectedRushIds,
      runHold,
      runFire,
      runRush,
      phaseClass
    }
  }
}
</script>

<style scoped>
.floor-page {
  min-height: 100vh;
  background: #eef1f4;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}
.disconnect-banner {
  background: #8c8c8c;
  padding: 8px 16px;
}
.disconnect-banner-text {
  color: #fff;
  font-size: 13px;
}
.page-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}
.back-link,
.refresh-btn {
  color: #1890ff;
  font-size: 14px;
  flex-shrink: 0;
}
.header-titles {
  flex-shrink: 0;
}
.page-title {
  display: block;
  font-size: 18px;
  font-weight: 700;
}
.jump-box {
  flex: 1;
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 6px;
}
.jump-input {
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 10px;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  background: #fff;
  font-size: 14px;
}
.jump-go {
  flex-shrink: 0;
  height: 36px;
  padding: 0 12px;
  border-radius: 8px;
  background: #1890ff;
  color: #fff;
  display: flex;
  align-items: center;
  font-size: 14px;
}
.floor-body {
  flex: 1;
  min-height: 0;
  display: flex;
}
.table-list {
  flex: 1;
  height: 100%;
  padding: 12px;
  box-sizing: border-box;
}
.table-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.floor-page--split .table-list {
  flex: 0 0 280px;
  width: 280px;
  border-right: 1px solid #e5e7eb;
  background: #f7f8fa;
}
.floor-page--split .table-grid {
  grid-template-columns: 1fr;
}
.table-card {
  background: #fff;
  border-radius: 10px;
  padding: 12px;
  border: 2px solid transparent;
  box-sizing: border-box;
}
.table-card--ready {
  border-color: #52c41a;
  background: #f6ffed;
}
.table-card--active {
  box-shadow: 0 0 0 2px #1890ff;
}
.table-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.table-name {
  font-size: 20px;
  font-weight: 700;
}
.rush-flag {
  color: #fff;
  background: #cf1322;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  font-weight: 700;
}
.count-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.count-item {
  font-size: 12px;
  color: #595959;
}
.count-item--ready {
  color: #237804;
  font-weight: 600;
}
.empty {
  padding: 48px 16px;
  text-align: center;
  color: #888;
}
.table-pane {
  flex: 1;
  min-width: 0;
  background: #fff;
  display: flex;
  flex-direction: column;
}
.pane-scroll {
  flex: 1;
  height: 100%;
  padding: 12px 16px 24px;
  box-sizing: border-box;
}
.pane-header {
  margin-bottom: 12px;
}
.pane-title {
  display: block;
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 8px;
}
.dish-group {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid #f0f0f0;
}
.dish-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}
.dish-name {
  font-size: 16px;
  font-weight: 600;
}
.dish-station {
  font-size: 12px;
  color: #888;
}
.line-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 44px;
  padding: 8px 12px;
  margin-bottom: 6px;
  border-radius: 8px;
  box-sizing: border-box;
}
.line-row--hold { background: #fff1b8; }
.line-row--steam { background: #bae7ff; }
.line-row--ready { background: #d9f7be; }
.line-row--cancel { background: #ffccc7; }
.line-row--pending { background: #e6f7ff; }
.line-row--locked { opacity: 0.7; }
.line-text { font-size: 14px; }
.line-mark { font-weight: 700; font-size: 16px; }
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.action-btn {
  border-radius: 8px;
  padding: 10px 14px;
  min-height: 44px;
  box-sizing: border-box;
  color: #fff;
  font-size: 14px;
  display: flex;
  align-items: center;
}
.action-btn.hold { background: #d48806; }
.action-btn.fire { background: #389e0d; }
.action-btn.rush { background: #cf1322; }
</style>
