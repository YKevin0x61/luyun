<template>
  <view class="floor-page">
    <view v-if="disconnected" class="disconnect-banner">
      <text class="disconnect-banner-text">实时连接已断开，正在重连…</text>
    </view>
    <view class="page-header">
      <text class="back-link" @click="goHome">← 返回</text>
      <view class="header-titles">
        <text class="page-title">楼面控制台</text>
        <text class="page-subtitle">按桌看制作 · 等叫 / 叫起 / 加急</text>
      </view>
      <view class="refresh-btn" @click="refresh">
        <text>刷新</text>
      </view>
    </view>

    <view v-if="busy" class="empty">加载中…</view>
    <view v-else-if="tables.length === 0" class="empty">当前没有需要盯的堂食桌</view>
    <scroll-view v-else class="table-list" scroll-y>
      <view v-for="table in tables" :key="table.table_number" class="table-card">
        <text class="table-name">{{ table.table_number }} 桌</text>
        <view
          v-for="group in table.groups"
          :key="table.table_number + '-' + group.dishName"
          class="dish-group"
        >
          <view class="dish-head">
            <text class="dish-name">{{ group.dishName }}</text>
            <text class="dish-station">{{ group.stationLabel }}</text>
          </view>
          <view class="line-chips">
            <view
              v-for="line in group.lines"
              :key="line.order_id"
              class="chip"
              :class="['chip--' + phaseClass(line.phase), { 'chip--locked': !isActionable(line) }]"
              @click="toggleLine(table.table_number, group.dishName, line.order_id, line)"
            >
              <text>{{ line.phase }}{{ line.is_rushed ? '·加急' : '' }}</text>
              <text v-if="isSelected(table.table_number, group.dishName, line.order_id)" class="chip-mark">✓</text>
            </view>
          </view>
          <view class="actions">
            <view
              v-if="selectedHoldIds(table, group).length"
              class="action-btn hold"
              @click="runHold(table, group)"
            >
              <text>等叫 {{ selectedHoldIds(table, group).length }}</text>
            </view>
            <view
              v-if="selectedFireIds(table, group).length"
              class="action-btn fire"
              @click="runFire(table, group)"
            >
              <text>叫起 {{ selectedFireIds(table, group).length }}</text>
            </view>
            <view
              v-if="selectedRushIds(table, group).length"
              class="action-btn rush"
              @click="runRush(table, group)"
            >
              <text>加急 {{ selectedRushIds(table, group).length }}</text>
            </view>
          </view>
        </view>
      </view>
    </scroll-view>
  </view>
</template>

<script>
import { computed, onMounted, reactive, ref } from 'vue'
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
  defaultSelectedOrderIds,
  groupLinesByDishName,
  isActionable
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
    let refreshQueued = false

    const disconnected = computed(() => realtimeStore.connectionStatus !== 'connected')

    const syncSelection = (nextTables) => {
      const nextKeys = new Set()
      for (const table of nextTables) {
        for (const group of table.groups) {
          const key = groupKey(table.table_number, group.dishName)
          nextKeys.add(key)
          const actionableIds = new Set(group.lines.filter(isActionable).map((line) => line.order_id))
          const prev = (selectedByGroup[key] || []).filter((id) => actionableIds.has(id))
          selectedByGroup[key] = prev.length > 0 ? prev : defaultSelectedOrderIds(group.lines)
        }
      }
      for (const key of Object.keys(selectedByGroup)) {
        if (!nextKeys.has(key)) delete selectedByGroup[key]
      }
    }

    const decorateTables = (rawTables) => {
      return (rawTables || []).map((table) => {
        const groups = groupLinesByDishName(table.lines || []).map((group) => ({
          ...group,
          stationLabel:
            stationsStore.getStationById(group.lines[0]?.station)?.name || group.lines[0]?.station || ''
        }))
        return { table_number: table.table_number, groups }
      })
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
          const next = decorateTables(data?.tables || [])
          tables.value = next
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
      const conflicts = result?.conflicts || []
      if (conflicts.length === 0) return
      const first = conflicts[0]
      uni.showToast({
        title: `${conflicts.length} 份未改：${first.reason}`,
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
        const reason = (typeof detail === 'object' && (detail?.conflicts?.[0]?.reason || detail?.message))
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

    const goHome = () => {
      uni.reLaunch({ url: '/pages/index/index' })
    }

    onShow(() => {
      refresh()
    })
    onMounted(() => {
      realtimeStore.init()
    })

    return {
      busy,
      tables,
      disconnected,
      refresh,
      goHome,
      isSelected,
      toggleLine,
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
  gap: 12px;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}
.back-link,
.refresh-btn {
  color: #1890ff;
  font-size: 14px;
}
.header-titles {
  flex: 1;
}
.page-title {
  display: block;
  font-size: 18px;
  font-weight: 700;
}
.page-subtitle {
  display: block;
  font-size: 12px;
  color: #666;
}
.empty {
  padding: 48px 16px;
  text-align: center;
  color: #888;
}
.table-list {
  height: calc(100vh - 64px);
  padding: 12px;
  box-sizing: border-box;
}
.table-card {
  background: #fff;
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 12px;
}
.table-name {
  font-size: 20px;
  font-weight: 700;
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
.line-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  background: #f5f5f5;
}
.chip--hold { background: #fff1b8; }
.chip--steam { background: #bae7ff; }
.chip--ready { background: #d9f7be; }
.chip--cancel { background: #ffccc7; }
.chip--pending { background: #e6f7ff; }
.chip--locked { opacity: 0.7; }
.chip-mark { font-weight: 700; }
.actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.action-btn {
  border-radius: 8px;
  padding: 8px 12px;
  color: #fff;
  font-size: 13px;
}
.action-btn.hold { background: #d48806; }
.action-btn.fire { background: #389e0d; }
.action-btn.rush { background: #cf1322; }
</style>
