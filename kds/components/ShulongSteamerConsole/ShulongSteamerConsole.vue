<template>
  <view class="steamer-console">
    <view class="steamer-body" :class="'awaiting-' + awaitingPlacement">
      <view v-if="awaitingPlacement !== 'hidden'" class="awaiting-pane">
        <text class="pane-title">待上笼</text>
        <scroll-view scroll-y class="awaiting-scroll">
          <view v-if="awaitingGroups.length === 0" class="empty-hint">
            <text>暂无待上笼蒸笼</text>
          </view>
          <view
            v-for="group in awaitingGroups"
            :key="group.dishName"
            class="awaiting-group"
          >
            <text class="group-header">{{ group.dishName }}</text>
            <view
              v-for="cage in group.cages"
              :key="cageId(cage)"
              class="awaiting-row"
              :class="{
                selected: isSelected(cage),
                notice: isAwaitingNotice(cage),
                [awaitingUrgencyClass(cage)]: !isAwaitingNotice(cage)
              }"
              @click="toggleCage(cage)"
            >
              <text class="cage-table">{{ cage.table_number }}桌</text>
              <text class="cage-dish">{{ cage.dish_name }}</text>
              <text v-if="isNewCage(cage)" class="new-badge">新</text>
              <text v-if="isAwaitingNotice(cage)" class="notice-mark">退</text>
            </view>
          </view>
        </scroll-view>
      </view>

      <view class="hole-map">
        <view
          v-for="steamer in layout.steamers"
          :key="steamer.id"
          class="steamer-block"
        >
          <text class="steamer-title">蒸炉 {{ steamer.id }}</text>
          <view class="hole-row">
            <view
              v-for="portIndex in portsFor(steamer)"
              :key="steamer.id + '-' + portIndex"
              class="hole"
              @click="tapHole(steamer.id, portIndex)"
            >
              <text class="hole-index">{{ portIndex }}</text>
              <view class="hole-slots">
                <view
                  v-for="(slot, slotIndex) in slotsFor(steamer.id, portIndex)"
                  :key="steamer.id + '-' + portIndex + '-' + slotIndex"
                  class="hole-slot"
                  :class="{ empty: slot.empty }"
                >
                  <view
                    v-if="!slot.empty"
                    class="hole-cage"
                    :class="{
                      selected: isHoleCageSelected(slot.cage),
                      hold: isCancelHold(slot.cage),
                      warn: holeUrgencyClass(slot.cage) === 'warn',
                      urgent: holeUrgencyClass(slot.cage) === 'urgent'
                    }"
                    @click.stop="toggleHoleCage(slot.cage)"
                  >
                    <text class="cage-primary">{{ cageCard(slot.cage).primary }}</text>
                    <view class="cage-secondary">
                      <text
                        v-for="(line, lineIndex) in cageCard(slot.cage).tableLines"
                        :key="lineIndex"
                        class="cage-table-part"
                      >{{ line }}</text>
                      <text class="cage-mins">{{ cageCard(slot.cage).steamMinutes }}分</text>
                    </view>
                    <text v-if="cageCard(slot.cage).holdMark" class="hold-mark">退</text>
                  </view>
                </view>
              </view>
            </view>
          </view>
        </view>
      </view>
    </view>

    <view v-if="showPluckBar" class="serve-bar">
      <button
        class="pluck-btn"
        :disabled="loading"
        @click="confirmPluck"
      >
        <text>{{ loading ? '提交中...' : '抽走' }}</text>
      </button>
    </view>
    <view v-else-if="showAwaitingServeBar" class="serve-bar">
      <button
        class="serve-btn"
        :disabled="loading"
        @click="confirmBasketServe"
      >
        <text>{{ loading ? '提交中...' : `出餐 (${selectedOrderIds.length})` }}</text>
      </button>
    </view>
    <view v-else-if="showSteamingServeBar" class="serve-bar">
      <button
        class="unload-btn"
        :disabled="loading"
        @click="confirmUnload"
      >
        <text>{{ loading ? '提交中...' : '下笼' }}</text>
      </button>
      <button
        class="serve-btn"
        :disabled="loading"
        @click="confirmBasketServe"
      >
        <text>{{ loading ? '提交中...' : `出餐 (${selectedSteamingIds.length})` }}</text>
      </button>
    </view>
  </view>
</template>

<script>
import { computed, ref, watch } from 'vue'
import { orderLineId } from '../../utils/batchCooking.js'
import {
  SHULONG_STEAMER_LAYOUT,
  deriveSteamerPhase,
  fillHoleSlots,
  formatSteamerCageCard,
  groupAwaitingSteamerCages,
  steamUrgencyLevel,
  steamerAwaitingPlacement,
  steamerBasketServeIntent,
  steamerHoleTapIntent,
  steamerPluckIntent,
  steamerUnloadIntent,
  toggleSteamerCageSelection
} from '../../utils/steamerConsole.js'

export default {
  name: 'ShulongSteamerConsole',
  props: {
    awaitingCages: {
      type: Array,
      default: () => []
    },
    steamingCages: {
      type: Array,
      default: () => []
    },
    layout: {
      type: Object,
      default: () => SHULONG_STEAMER_LAYOUT
    },
    loading: {
      type: Boolean,
      default: false
    },
    workSurface: {
      type: String,
      default: ''
    },
    now: {
      type: Number,
      default: 0
    },
    isNewCage: {
      type: Function,
      default: null
    },
    waitThresholdsMs: {
      type: Object,
      default: null
    },
    steamThresholdsMs: {
      type: Object,
      default: null
    }
  },
  emits: ['load', 'move', 'unload', 'serve', 'pluck'],
  setup(props, { emit }) {
    const selectedOrderIds = ref([])
    const selectedSteamingIds = ref([])
    const selectedHoldIds = ref([])

    const cageId = (cage) => orderLineId(cage)

    const clock = () => props.now || Date.now()

    const phaseOpts = () => ({
      now: clock(),
      noticeSeconds: props.layout.awaitingCancelNoticeSeconds
        || SHULONG_STEAMER_LAYOUT.awaitingCancelNoticeSeconds
    })

    const cagePhase = (cage) => deriveSteamerPhase(cage, phaseOpts())

    const awaitingGroups = computed(() =>
      groupAwaitingSteamerCages(props.awaitingCages, phaseOpts())
    )

    const awaitingPlacement = computed(() =>
      steamerAwaitingPlacement(props.workSurface)
    )

    const showAwaitingServeBar = computed(() =>
      awaitingPlacement.value !== 'hidden'
      && selectedOrderIds.value.length > 0
      && selectedSteamingIds.value.length === 0
    )

    const showSteamingServeBar = computed(() =>
      props.workSurface !== 'load'
      && selectedSteamingIds.value.length > 0
      && selectedOrderIds.value.length === 0
    )

    const showPluckBar = computed(() =>
      props.workSurface !== 'load' && selectedHoldIds.value.length > 0
    )

    const isAwaitingNotice = (cage) => cagePhase(cage) === '待上笼退示'

    const isCancelHold = (cage) => cagePhase(cage) === '退菜占位'

    const isSelected = (cage) => selectedOrderIds.value.includes(cageId(cage))

    const isHoleCageSelected = (cage) => {
      const id = cageId(cage)
      return selectedSteamingIds.value.includes(id) || selectedHoldIds.value.includes(id)
    }

    const cageCard = (cage) => formatSteamerCageCard(cage, clock())

    const isNewCage = (cage) => {
      if (typeof props.isNewCage !== 'function') return false
      return Boolean(props.isNewCage(cage))
    }

    const awaitingUrgencyClass = (cage) => {
      const thresholds = props.waitThresholdsMs
      if (!thresholds) return 'normal'
      const orderTime = Date.parse(cage?.order_time)
      if (!Number.isFinite(orderTime)) return 'normal'
      const wait = clock() - orderTime
      if (wait > Number(thresholds.urgent)) return 'urgent'
      if (wait > Number(thresholds.warning)) return 'high'
      return 'normal'
    }

    const holeUrgencyClass = (cage) => steamUrgencyLevel(cage, clock(), props.steamThresholdsMs)

    const applySelection = (next) => {
      selectedOrderIds.value = next.awaitingIds
      selectedSteamingIds.value = next.steamingIds
      selectedHoldIds.value = next.holdIds
    }

    const toggleCage = (cage) => {
      applySelection(toggleSteamerCageSelection({
        awaitingIds: selectedOrderIds.value,
        steamingIds: selectedSteamingIds.value,
        holdIds: selectedHoldIds.value,
        orderId: cageId(cage),
        phase: cagePhase(cage)
      }))
    }

    const toggleHoleCage = (cage) => {
      applySelection(toggleSteamerCageSelection({
        awaitingIds: selectedOrderIds.value,
        steamingIds: selectedSteamingIds.value,
        holdIds: selectedHoldIds.value,
        orderId: cageId(cage),
        phase: cagePhase(cage)
      }))
    }

    const confirmBasketServe = () => {
      if (props.loading) return
      const intent = steamerBasketServeIntent({
        awaitingIds: selectedOrderIds.value,
        steamingIds: selectedSteamingIds.value
      })
      if (!intent) return
      if (intent.type === 'reject') {
        uni.showToast({ title: '不能同时出待上笼和在蒸', icon: 'none' })
        return
      }
      emit('serve', intent)
    }

    const confirmUnload = () => {
      if (props.loading) return
      const intent = steamerUnloadIntent({
        selectedOrderIds: selectedSteamingIds.value
      })
      if (!intent) return
      emit('unload', intent)
    }

    const confirmPluck = () => {
      if (props.loading) return
      const intent = steamerPluckIntent({
        selectedHoldIds: selectedHoldIds.value
      })
      if (!intent) return
      emit('pluck', intent)
    }

    const portsFor = (steamer) => {
      const count = Number(steamer.portCount || steamer.port_count || 0)
      return Array.from({ length: count }, (_, index) => index + 1)
    }

    const cagesOnHole = (steamerId, portIndex) => {
      return props.steamingCages.filter((cage) => {
        const placement = cage.placement
        return placement
          && String(placement.steamer_id) === String(steamerId)
          && Number(placement.port_index) === Number(portIndex)
      })
    }

    const slotsFor = (steamerId, portIndex) => {
      return fillHoleSlots(props.steamingCages, {
        steamerId,
        portIndex,
        portCapacity: Number(props.layout.portCapacity || SHULONG_STEAMER_LAYOUT.portCapacity),
        now: clock()
      })
    }

    const tapHole = (steamerId, portIndex) => {
      if (props.loading) return
      const onHole = cagesOnHole(steamerId, portIndex)
      const intent = steamerHoleTapIntent({
        awaitingIds: selectedOrderIds.value,
        steamingIds: selectedSteamingIds.value,
        holdIds: selectedHoldIds.value,
        steamerId,
        portIndex,
        occupiedOnHole: onHole.length,
        portCapacity: Number(props.layout.portCapacity || SHULONG_STEAMER_LAYOUT.portCapacity),
        idsOnHole: onHole.map(cageId),
        workSurface: props.workSurface
      })
      if (!intent) return
      if (intent.type === 'reject') {
        const title = intent.reason === 'capacity'
          ? '蒸孔已满'
          : intent.reason === 'surface'
            ? (props.workSurface === 'load' ? '待上笼面不能换孔' : '在蒸面不能上笼')
            : '不能同时上笼和换孔'
        uni.showToast({ title, icon: 'none' })
        return
      }
      if (intent.type === 'load') {
        emit('load', intent)
        return
      }
      if (intent.type === 'move') {
        emit('move', intent)
      }
    }

    watch(
      () => props.awaitingCages,
      (cages) => {
        const live = new Set((cages || []).map(cageId))
        selectedOrderIds.value = selectedOrderIds.value.filter((id) => live.has(id))
      }
    )

    watch(
      () => props.steamingCages,
      (cages) => {
        const live = new Set((cages || []).map(cageId))
        selectedSteamingIds.value = selectedSteamingIds.value.filter((id) => live.has(id))
        selectedHoldIds.value = selectedHoldIds.value.filter((id) => live.has(id))
      }
    )

    return {
      selectedOrderIds,
      selectedSteamingIds,
      selectedHoldIds,
      awaitingGroups,
      awaitingPlacement,
      showAwaitingServeBar,
      showSteamingServeBar,
      showPluckBar,
      isNewCage,
      awaitingUrgencyClass,
      holeUrgencyClass,
      cageId,
      cageCard,
      isSelected,
      isAwaitingNotice,
      isCancelHold,
      isHoleCageSelected,
      toggleCage,
      toggleHoleCage,
      confirmBasketServe,
      confirmUnload,
      confirmPluck,
      portsFor,
      slotsFor,
      tapHole
    }
  }
}
</script>

<style scoped>
.steamer-console {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12upx;
  padding: 12upx 16upx 16upx;
  box-sizing: border-box;
}

.steamer-body {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12upx;
}

.steamer-body.awaiting-side {
  flex-direction: row;
  align-items: stretch;
}

.awaiting-pane {
  background: #fff;
  border-radius: 16upx;
  padding: 16upx 20upx;
  box-shadow: 0 4upx 16upx rgba(15, 23, 42, 0.06);
  box-sizing: border-box;
}

.awaiting-side .awaiting-pane {
  width: 360upx;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.pane-title {
  font-size: 28upx;
  font-weight: 600;
  color: #1f2937;
}

.awaiting-scroll {
  max-height: 280upx;
  margin-top: 12upx;
}

.awaiting-side .awaiting-scroll {
  max-height: none;
  flex: 1;
  min-height: 0;
}

.awaiting-group {
  margin-bottom: 12upx;
}

.group-header {
  display: block;
  font-size: 26upx;
  font-weight: 600;
  color: #0f766e;
  padding: 4upx 4upx 8upx;
}

.empty-hint {
  padding: 24upx 0;
  color: #9ca3af;
  font-size: 26upx;
}

.awaiting-row {
  display: flex;
  align-items: center;
  gap: 16upx;
  padding: 16upx 12upx;
  border-radius: 12upx;
  margin-bottom: 8upx;
  background: #f8fafc;
}

.awaiting-row.selected {
  background: #dbeafe;
  outline: 2upx solid #3b82f6;
}

.awaiting-row.high {
  box-shadow: inset 4upx 0 0 #fa8c16;
}

.awaiting-row.urgent {
  box-shadow: inset 4upx 0 0 #ff4d4f;
}

.awaiting-row.notice {
  background: #e5e7eb;
  color: #6b7280;
}

.new-badge {
  margin-left: auto;
  font-size: 18upx;
  font-weight: 700;
  color: #d48806;
  background: #fff7e6;
  border-radius: 6upx;
  padding: 0 8upx;
}

.awaiting-row.notice .cage-table,
.awaiting-row.notice .cage-dish {
  color: #6b7280;
}

.notice-mark,
.hold-mark {
  margin-left: auto;
  font-size: 18upx;
  font-weight: 700;
  color: #6b7280;
}

.cage-table {
  font-size: 28upx;
  font-weight: 600;
  color: #111827;
  min-width: 88upx;
}

.cage-dish {
  font-size: 28upx;
  color: #374151;
}

.hole-map {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: row;
  gap: 12upx;
}

.steamer-block {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 16upx;
  padding: 12upx;
  box-shadow: 0 4upx 16upx rgba(15, 23, 42, 0.06);
  box-sizing: border-box;
}

.steamer-title {
  font-size: 24upx;
  font-weight: 600;
  color: #0f766e;
  margin-bottom: 8upx;
  flex-shrink: 0;
}

.hole-row {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: row;
  gap: 6upx;
}

.hole {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 2upx dashed #cbd5e1;
  border-radius: 10upx;
  padding: 4upx;
  box-sizing: border-box;
  background: #f8fafc;
}

.hole-index {
  display: block;
  text-align: center;
  font-size: 18upx;
  color: #64748b;
  margin-bottom: 2upx;
  flex-shrink: 0;
}

.hole-slots {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.hole-slot {
  flex: 1;
  min-height: 0;
}

.hole-slot.empty {
  background: transparent;
}

.hole-cage {
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  background: #45b7d1;
  border-radius: 6upx;
  padding: 2upx 3upx;
  position: relative;
}

.hole-cage.selected {
  outline: 2upx solid #fbbf24;
  box-shadow: 0 0 0 2upx #f59e0b;
}

.hole-cage.warn {
  background: #d97706;
}

.hole-cage.urgent {
  background: #dc2626;
}

.hole-cage.hold {
  background: #9ca3af;
}

.hole-cage.hold .cage-primary,
.hole-cage.hold .cage-secondary,
.hole-cage.hold .hold-mark {
  color: #f3f4f6;
}

.hole-cage .hold-mark {
  position: absolute;
  top: 0;
  right: 2upx;
  margin-left: 0;
  font-size: 16upx;
}

.cage-primary {
  display: block;
  font-size: 18upx;
  font-weight: 600;
  color: #fff;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.15;
}

.cage-secondary {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: center;
  gap: 2upx 4upx;
  max-height: 2.4em;
  overflow: hidden;
  line-height: 1.15;
}

.cage-table-part,
.cage-mins {
  font-size: 14upx;
  color: #fff;
  text-align: center;
}

.serve-bar {
  position: sticky;
  bottom: 0;
  display: flex;
  justify-content: center;
  gap: 16upx;
  padding: 12upx 0 4upx;
  flex-shrink: 0;
}

.unload-btn {
  min-width: 200upx;
  height: 80upx;
  border: none;
  border-radius: 40upx;
  background: #64748b;
  color: #fff;
  font-size: 30upx;
  font-weight: 600;
}

.unload-btn:disabled {
  opacity: 0.6;
}

.pluck-btn {
  min-width: 200upx;
  height: 80upx;
  border: none;
  border-radius: 40upx;
  background: #6b7280;
  color: #fff;
  font-size: 30upx;
  font-weight: 600;
}

.pluck-btn:disabled {
  opacity: 0.6;
}

.serve-btn {
  min-width: 280upx;
  height: 80upx;
  border: none;
  border-radius: 40upx;
  background: #16a34a;
  color: #fff;
  font-size: 30upx;
  font-weight: 600;
}

.serve-btn:disabled {
  opacity: 0.6;
}
</style>
