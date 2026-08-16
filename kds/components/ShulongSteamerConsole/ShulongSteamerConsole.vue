<template>
  <view class="steamer-console">
    <view class="steamer-body" :class="'awaiting-' + awaitingPlacement">
      <view v-if="awaitingPlacement !== 'hidden'" class="awaiting-pane">
        <text class="pane-title">待上笼组 · 点次数选最早，再点孔上笼</text>
        <scroll-view scroll-y class="awaiting-scroll">
          <view class="awaiting-scroll-body">
            <view v-if="awaitingGroups.length === 0" class="empty-hint">
              <text>暂无待上笼蒸笼</text>
            </view>
            <view
              v-for="group in awaitingGroups"
              :key="group.chunkId || group.dishName"
              class="awaiting-group"
            >
              <view class="awaiting-group-main">
                <view
                  v-if="group.selectableCages.length"
                  class="awaiting-qty"
                  :class="{
                    selected: groupSelectedCount(group) > 0,
                    'is-new': groupHasNew(group),
                    conflict: groupHasConflict(group)
                  }"
                  @click="advanceGroup(group)"
                >
                  <text class="group-qty">{{ groupQtyLabel(group) }}</text>
                  <text class="group-name">{{ group.dishName }}</text>
                  <view
                    class="group-detail-btn"
                    :class="{ open: isGroupDetailOpen(group) }"
                    @click.stop="toggleGroupDetail(group)"
                  >
                    <text class="group-detail-text">{{ isGroupDetailOpen(group) ? '收起' : '详情' }}</text>
                  </view>
                </view>
                <view v-if="group.noticeCages.length" class="awaiting-notices">
                  <view
                    v-for="cage in group.noticeCages"
                    :key="cageId(cage)"
                    class="awaiting-chip notice"
                  >
                    <text class="notice-mark">退示</text>
                  </view>
                </view>
              </view>
              <view v-if="isGroupDetailOpen(group)" class="awaiting-detail">
                <view
                  v-for="cage in groupDetailCages(group)"
                  :key="cageId(cage)"
                  class="awaiting-detail-row"
                  :class="{ conflict: cageIsConflict(cage) }"
                >
                  <text class="detail-table">{{ cageDetailTable(cage) }}</text>
                  <text class="detail-time">{{ cageDetailTime(cage) }}</text>
                </view>
              </view>
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
          <text class="steamer-title">炉 {{ steamer.id }}</text>
          <view class="hole-row">
            <view
              v-for="portIndex in portsFor(steamer)"
              :key="steamer.id + '-' + portIndex"
              class="hole"
              @click="tapHole(steamer.id, portIndex)"
            >
              <text class="hole-index" :class="{ armed: holeArmed }">孔{{ portIndex }}</text>
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
                      urgent: holeUrgencyClass(slot.cage) === 'urgent',
                      conflict: cageIsConflict(slot.cage)
                    }"
                    @click.stop="toggleHoleCage(slot.cage)"
                  >
                    <view class="cage-primary-row">
                      <text class="cage-primary">{{ cageCard(slot.cage).primary }}</text>
                      <text v-if="cageCard(slot.cage).rushMark" class="rush-mark">催</text>
                      <text v-if="cageCard(slot.cage).holdMark" class="hold-mark">退</text>
                    </view>
                    <view class="cage-meta">
                      <text class="cage-table-line">{{ cageCard(slot.cage).tableLines.join(' ') }}</text>
                      <text class="cage-steam-time">{{ cageCard(slot.cage).timeLabel }}</text>
                    </view>
                  </view>
                </view>
              </view>
              <view
                v-if="holeHasCages(steamer.id, portIndex)"
                class="hole-select-all"
                :class="{ on: holeAllSelected(steamer.id, portIndex) }"
                @click.stop="selectAllOnHole(steamer.id, portIndex)"
              >
                <text class="hole-select-all-text">{{ holeAllSelected(steamer.id, portIndex) ? '取消' : '全选' }}</text>
              </view>
            </view>
          </view>
        </view>
      </view>
    </view>

    <view class="serve-bar">
      <text class="serve-hint">{{ serveHint }}</text>
      <button
        v-if="showPluckBar"
        class="pluck-btn"
        :disabled="loading"
        @click="confirmPluck"
      >
        <text>{{ loading ? '提交中...' : `抽走 (${selectedHoldIds.length})` }}</text>
      </button>
      <button
        v-if="showSteamingServeBar"
        class="unload-btn"
        :disabled="loading"
        @click="confirmUnload"
      >
        <text>{{ loading ? '提交中...' : '下笼' }}</text>
      </button>
      <button
        v-if="showAwaitingServeBar || showSteamingServeBar"
        class="serve-btn"
        :disabled="loading"
        @click="confirmBasketServe"
      >
        <text>{{ loading ? '提交中...' : `出餐 (${serveCount})` }}</text>
      </button>
    </view>
  </view>
</template>

<script>
import { computed, ref, watch } from 'vue'
import { orderLineId } from '../../utils/batchCooking.js'
import { hasMarkedOrderLine, orderLineIsMarked } from '../../utils/serveConfirm.js'
import { dishSplitKnobsChanged } from '../../utils/dishCardChunks.js'
import {
  SHULONG_STEAMER_LAYOUT,
  deriveSteamerPhase,
  fillHoleSlots,
  advanceAwaitingGroupSelection,
  awaitingGroupSelectedCount,
  composeAwaitingSteamerGroups,
  formatSteamerCageCard,
  formatSteamerTableLabel,
  sortAwaitingCagesFifo,
  steamUrgencyLevel,
  steamerAwaitingPlacement,
  steamerBasketServeIntent,
  steamerHoleTapIntent,
  steamerPluckIntent,
  steamerUnloadIntent,
  toggleSteamerCageSelection,
  selectAllHoleCages,
  isHoleFullySelected
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
    },
    dishCardQuantityCap: {
      type: Number,
      default: 0
    },
    orderGapMinutes: {
      type: Number,
      default: 0
    },
    conflictOrderIds: {
      type: Array,
      default: () => []
    }
  },
  emits: ['load', 'move', 'unload', 'serve', 'pluck', 'selection-change'],
  setup(props, { emit }) {
    const selectedOrderIds = ref([])
    const selectedSteamingIds = ref([])
    const selectedHoldIds = ref([])
    const detailGroupKey = ref('')

    const cageId = (cage) => orderLineId(cage)
    const groupKey = (group) => group.chunkId || group.dishName

    const clock = () => props.now || Date.now()

    const phaseOpts = () => ({
      now: clock(),
      noticeSeconds: props.layout.awaitingCancelNoticeSeconds
        || SHULONG_STEAMER_LAYOUT.awaitingCancelNoticeSeconds
    })

    const cagePhase = (cage) => deriveSteamerPhase(cage, phaseOpts())

    const awaitingGroups = ref([])
    const chunkSnapshotByDish = ref({})
    const splitKnobsSeen = ref(null)

    const applyAwaitingGroups = (previous) => {
      const { groups, previousByDish } = composeAwaitingSteamerGroups(
        props.awaitingCages,
        phaseOpts(),
        {
          cap: Number(props.dishCardQuantityCap) || 0,
          orderGapMinutes: Number(props.orderGapMinutes) || 0,
          previousByDish: previous
        }
      )
      awaitingGroups.value = groups
      chunkSnapshotByDish.value = previousByDish
      splitKnobsSeen.value = {
        cap: Number(props.dishCardQuantityCap) || 0,
        orderGapMinutes: Number(props.orderGapMinutes) || 0
      }
    }

    const awaitingPlacement = computed(() => steamerAwaitingPlacement())

    const showAwaitingServeBar = computed(() =>
      awaitingPlacement.value !== 'hidden'
      && selectedOrderIds.value.length > 0
      && selectedSteamingIds.value.length === 0
    )

    const showSteamingServeBar = computed(() =>
      selectedSteamingIds.value.length > 0
      && selectedOrderIds.value.length === 0
    )

    const showPluckBar = computed(() => selectedHoldIds.value.length > 0)

    const holeArmed = computed(() =>
      selectedOrderIds.value.length > 0 || selectedSteamingIds.value.length > 0
    )

    const serveCount = computed(() =>
      selectedOrderIds.value.length + selectedSteamingIds.value.length
    )

    const serveHint = computed(() => {
      if (showPluckBar.value) return '退菜占位已选中 · 抽走不出餐'
      if (showAwaitingServeBar.value) return '点孔即上笼 · 或直接出餐'
      if (showSteamingServeBar.value) return '点孔换孔 · 下笼退回待上笼 · 出餐要按确认'
      return '点组选最早 · 点孔上笼或换孔 · 出餐要按确认'
    })

    const groupSelectedCount = (group) =>
      awaitingGroupSelectedCount(group.selectableCages, selectedOrderIds.value)

    const groupQtyLabel = (group) => {
      const total = Number(group.totalQuantity) || group.selectableCages.length
      const selected = groupSelectedCount(group)
      return selected > 0 ? `${selected}/${total}` : String(total)
    }

    const groupHasNew = (group) =>
      (group.selectableCages || []).some((cage) => isNewCage(cage))

    const cageIsConflict = (cage) => orderLineIsMarked(props.conflictOrderIds, cage)
    const groupHasConflict = (group) =>
      hasMarkedOrderLine(props.conflictOrderIds, group.selectableCages)

    const isGroupDetailOpen = (group) => detailGroupKey.value === groupKey(group)

    const toggleGroupDetail = (group) => {
      const key = groupKey(group)
      detailGroupKey.value = detailGroupKey.value === key ? '' : key
    }

    const groupDetailCages = (group) => sortAwaitingCagesFifo(group.selectableCages)

    const cageDetailTable = (cage) => {
      const label = formatSteamerTableLabel(cage?.table_number, cage?.source)
      return label.lines.filter(Boolean).join(' ')
    }

    const cageDetailTime = (cage) => {
      const date = new Date(cage?.order_time)
      if (Number.isNaN(date.getTime())) return ''
      const hours = String(date.getHours()).padStart(2, '0')
      const minutes = String(date.getMinutes()).padStart(2, '0')
      return `${hours}:${minutes}`
    }

    const isCancelHold = (cage) => cagePhase(cage) === '退菜占位'

    const isHoleCageSelected = (cage) => {
      const id = cageId(cage)
      return selectedSteamingIds.value.includes(id) || selectedHoldIds.value.includes(id)
    }

    const cageCard = (cage) => formatSteamerCageCard(cage, clock())

    const isNewCage = (cage) => {
      if (typeof props.isNewCage !== 'function') return false
      return Boolean(props.isNewCage(cage))
    }

    const holeUrgencyClass = (cage) => steamUrgencyLevel(cage, clock(), props.steamThresholdsMs)

    const applySelection = (next) => {
      selectedOrderIds.value = next.awaitingIds
      selectedSteamingIds.value = next.steamingIds
      selectedHoldIds.value = next.holdIds
    }

    const notifySelectionChange = () => {
      emit('selection-change')
    }

    const advanceGroup = (group) => {
      selectedOrderIds.value = advanceAwaitingGroupSelection({
        selectableCages: group.selectableCages,
        selectedIds: selectedOrderIds.value
      })
      notifySelectionChange()
    }

    const toggleHoleCage = (cage) => {
      applySelection(toggleSteamerCageSelection({
        awaitingIds: selectedOrderIds.value,
        steamingIds: selectedSteamingIds.value,
        holdIds: selectedHoldIds.value,
        orderId: cageId(cage),
        phase: cagePhase(cage)
      }))
      notifySelectionChange()
    }

    const holeHasCages = (steamerId, portIndex) => cagesOnHole(steamerId, portIndex).length > 0

    const holeAllSelected = (steamerId, portIndex) => isHoleFullySelected({
      cagesOnHole: cagesOnHole(steamerId, portIndex),
      steamingIds: selectedSteamingIds.value,
      holdIds: selectedHoldIds.value,
      ...phaseOpts()
    })

    const selectAllOnHole = (steamerId, portIndex) => {
      applySelection(selectAllHoleCages({
        cagesOnHole: cagesOnHole(steamerId, portIndex),
        awaitingIds: selectedOrderIds.value,
        steamingIds: selectedSteamingIds.value,
        holdIds: selectedHoldIds.value,
        ...phaseOpts()
      }))
      notifySelectionChange()
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
        idsOnHole: onHole.map(cageId)
      })
      if (!intent) return
      if (intent.type === 'reject') {
        const title = intent.reason === 'capacity'
          ? '蒸孔已满'
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
      () => [
        props.awaitingCages,
        props.dishCardQuantityCap,
        props.orderGapMinutes,
        props.now,
        props.layout.awaitingCancelNoticeSeconds
      ],
      () => {
        const cap = Number(props.dishCardQuantityCap) || 0
        const gap = Number(props.orderGapMinutes) || 0
        if (dishSplitKnobsChanged(splitKnobsSeen.value, cap, gap)) {
          selectedOrderIds.value = []
          applyAwaitingGroups({})
        } else {
          applyAwaitingGroups(chunkSnapshotByDish.value)
        }
        const live = new Set((props.awaitingCages || []).map(cageId))
        selectedOrderIds.value = selectedOrderIds.value.filter((id) => live.has(id))
      },
      { immediate: true }
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
      holeArmed,
      serveCount,
      serveHint,
      groupSelectedCount,
      groupQtyLabel,
      groupHasNew,
      cageIsConflict,
      groupHasConflict,
      isGroupDetailOpen,
      toggleGroupDetail,
      groupDetailCages,
      cageDetailTable,
      cageDetailTime,
      isNewCage,
      holeUrgencyClass,
      cageId,
      cageCard,
      isCancelHold,
      isHoleCageSelected,
      advanceGroup,
      toggleHoleCage,
      holeHasCages,
      holeAllSelected,
      selectAllOnHole,
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
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #e8edf1;
  box-sizing: border-box;
  overflow: hidden;
}

.steamer-body {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: row;
  align-items: stretch;
  gap: 8px;
  padding: 8px 10px 0;
  overflow: hidden;
}

.awaiting-pane {
  width: 300px;
  flex-shrink: 0;
  align-self: stretch;
  background: #fff;
  border: 2px solid #45B7D1;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.pane-title {
  font-size: 13px;
  font-weight: 800;
  color: #2c3e50;
  padding: 6px 10px;
  flex-shrink: 0;
}

.awaiting-scroll {
  flex: 1;
  min-height: 0;
  min-width: 0;
  width: 100%;
  height: 0;
  touch-action: pan-y;
}

.awaiting-scroll-body {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  padding: 0 8px 8px;
  box-sizing: border-box;
}

.awaiting-group {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  min-height: 44px;
  min-width: 0;
  max-width: 100%;
  margin-bottom: 6px;
}

.awaiting-group-main {
  display: flex;
  align-items: stretch;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
}

.awaiting-qty {
  flex: 1;
  min-width: 0;
  max-width: 100%;
  min-height: 52px;
  padding: 6px 6px 6px 10px;
  border-radius: 8px;
  border: 2px solid #45B7D1;
  background: #e6fffb;
  display: flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  overflow: hidden;
}

.awaiting-qty.selected {
  border-color: #52c41a;
  background: #f6ffed;
}

.awaiting-qty.is-new {
  box-shadow: 0 0 0 2px rgba(250, 173, 20, 0.7);
}

.awaiting-qty.conflict {
  border-color: #ff4d4f;
  box-shadow: 0 0 0 2px rgba(255, 77, 79, 0.55);
}

.group-name {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 800;
  color: #2c3e50;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.group-qty {
  flex-shrink: 0;
  font-size: 22px;
  font-weight: 800;
  color: #d97706;
}

.awaiting-qty.selected .group-qty {
  color: #389e0d;
}

.group-detail-btn {
  flex-shrink: 0;
  min-width: 48px;
  min-height: 40px;
  padding: 0 10px;
  border-radius: 6px;
  background: #1890ff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
}

.group-detail-text {
  color: #fff;
  font-size: 13px;
  font-weight: 800;
  line-height: 1;
}

.group-detail-btn.open {
  background: #096dd9;
}

.awaiting-detail {
  padding: 6px 8px;
  border-radius: 8px;
  border: 1px solid #c5d6de;
  background: #f7fafc;
  box-sizing: border-box;
}

.awaiting-detail-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 28px;
}

.awaiting-detail-row.conflict {
  background: #fff2f0;
  box-shadow: inset 3px 0 0 #ff4d4f;
}

.detail-table,
.detail-time {
  font-size: 13px;
  font-weight: 700;
  color: #2c3e50;
}

.detail-time {
  flex-shrink: 0;
  color: #64748b;
}

.empty-hint {
  padding: 24px 0;
  color: #9ca3af;
  font-size: 13px;
}

.awaiting-notices {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.awaiting-chip {
  min-height: 44px;
  min-width: 44px;
  padding: 0 10px;
  border-radius: 8px;
  border: 2px solid #bfbfbf;
  background: #f5f5f5;
  display: flex;
  align-items: center;
  box-sizing: border-box;
}

.awaiting-chip.notice .notice-mark {
  color: #8c8c8c;
}

.notice-mark,
.hold-mark,
.rush-mark {
  font-size: 10px;
  font-weight: 800;
}

.rush-mark {
  color: #ff4d4f;
}

.hold-mark {
  color: #8c8c8c;
}

.hole-map {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: row;
  gap: 10px;
  overflow: hidden;
}

.steamer-block {
  flex: 1;
  min-width: 0;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #d9e3ea;
  border: 3px solid #5a7a88;
  border-radius: 10px;
  overflow: hidden;
  box-sizing: border-box;
}

.steamer-title {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: #fff;
  background: #4a6673;
  padding: 4px 8px;
  flex-shrink: 0;
}

.hole-row {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: row;
  gap: 3px;
  padding: 4px;
  overflow: hidden;
}

.hole {
  flex: 1 1 0;
  width: 0;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #c5d0d6;
  border-radius: 8px;
  overflow: hidden;
}

.hole-index {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  background: #3d5a66;
  color: #fff;
  font-weight: 800;
  font-size: 13px;
  flex-shrink: 0;
}

.hole-index.armed {
  background: #135200;
  animation: steamer-arm-pulse 1s ease-in-out infinite;
}

.hole-select-all {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 36px;
  flex-shrink: 0;
  background: #3d5a66;
  box-sizing: border-box;
}

.hole-select-all-text {
  color: #fff;
  font-size: 12px;
  font-weight: 800;
  line-height: 1;
}

.hole-select-all.on {
  background: #52c41a;
}

@keyframes steamer-arm-pulse {
  0%, 100% { background: #135200; }
  50% { background: #237804; }
}

.hole-slots {
  flex: 1;
  min-height: 0;
  min-width: 0;
  width: 100%;
  display: grid;
  grid-template-rows: repeat(10, minmax(0, 1fr));
  grid-auto-rows: 0;
  gap: 2px;
  padding: 2px;
  overflow: hidden;
}

.hole-slot {
  min-height: 0;
  min-width: 0;
  width: 100%;
  overflow: hidden;
}

.hole-cage {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
  background: #fff;
  border: 2px solid #45B7D1;
  border-radius: 6px;
  padding: 1px 3px;
  color: #2c3e50;
  overflow: hidden;
}

.hole-cage.selected {
  outline: 3px solid #52c41a;
  outline-offset: -3px;
}

.hole-cage.conflict {
  outline: 3px solid #ff4d4f;
  outline-offset: -3px;
}

.hole-cage.warn {
  border-color: #d48806;
  background: #fff7e6;
}

.hole-cage.urgent {
  border-color: #ff4d4f;
  background: #fff2f0;
}

.hole-cage.hold {
  border-color: #8c8c8c;
  background: #f0f0f0;
  color: #8c8c8c;
}

.cage-primary-row {
  display: flex;
  align-items: baseline;
  gap: 2px;
  min-width: 0;
  max-width: 100%;
}

.cage-primary {
  display: block;
  flex: 1;
  width: 0;
  min-width: 0;
  font-size: 12px;
  font-weight: 800;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cage-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  min-width: 0;
  max-width: 100%;
}

.cage-table-line,
.cage-steam-time {
  display: block;
  min-width: 0;
  max-width: 100%;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.15;
  color: #64748b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cage-steam-time {
  flex: 1 0 100%;
}

.hole-cage.hold .cage-table-line,
.hole-cage.hold .cage-steam-time {
  color: #8c8c8c;
}

.serve-bar {
  flex-shrink: 0;
  min-height: 56px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #fff;
  border-top: 1px solid #e8e8e8;
}

.serve-hint {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #64748b;
}

.unload-btn,
.pluck-btn,
.serve-btn {
  margin: 0;
  padding: 0 18px;
  min-height: 52px;
  width: auto;
  flex-shrink: 0;
  border-radius: 12px;
  font-size: 18px;
  font-weight: 800;
  line-height: 52px;
}

.unload-btn {
  border: 1px solid #e8e8e8;
  background: #fff;
  color: #2c3e50;
}

.pluck-btn {
  border: 0;
  background: #8c8c8c;
  color: #fff;
}

.serve-btn {
  border: 0;
  background: linear-gradient(135deg, #1890ff, #40a9ff);
  color: #fff;
  padding: 0 28px;
}

.unload-btn:disabled,
.pluck-btn:disabled,
.serve-btn:disabled {
  opacity: 0.6;
}
</style>
