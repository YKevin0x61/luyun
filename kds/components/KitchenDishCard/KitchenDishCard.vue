<template>
  <view
    class="dish-card"
    :class="{
      'dish-overtime': dish.isOvertime,
      'dish-new': isNew,
      selected: selectedQuantity > 0,
      'density-compact': density === DENSITY_MODES.COMPACT,
      'density-ultra': density === DENSITY_MODES.ULTRA
    }"
    @click="$emit('increase')"
  >
    <view v-if="isNew" class="new-badge">
      <text class="new-badge-text">新</text>
    </view>
    <view class="dish-header">
      <text class="dish-name">{{ dish.dishName }}</text>
      <view class="dish-quantity">
        <text class="quantity-text">{{ dish.totalQuantity }}份</text>
        <text v-if="density !== DENSITY_MODES.ULTRA" class="orders-count">({{ dish.orders.length }}单)</text>
      </view>
    </view>

    <view class="time-info">
      <text class="time-label">最长等待:</text>
      <text class="time-value" :class="dish.waitTimeClass">{{ dish.maxWaitTimeFormatted }}</text>
    </view>

    <!-- 超紧凑：隐藏每桌明细，桌号进详情；紧凑：每桌收成单行 -->
    <view v-if="density !== DENSITY_MODES.ULTRA" class="orders-detail">
      <view class="orders-grid">
        <view v-for="order in dish.orders" :key="order.id" class="order-block">
          <view class="block-header">
            <text class="block-table">{{ order.table_number }}桌</text>
            <text class="block-quantity">{{ order.quantity }}份</text>
            <text v-if="density === DENSITY_MODES.COMPACT" class="block-time">{{ formatOrderTime(order.order_time) }}</text>
          </view>
          <text v-if="density !== DENSITY_MODES.COMPACT" class="block-time">{{ formatOrderTime(order.order_time) }}</text>
        </view>
      </view>
    </view>

    <view class="dish-actions" @click.stop>
      <view class="actions-container">
        <view class="action-section decrease-section">
          <button
            v-if="selectedQuantity > 0"
            class="quantity-btn decrease-btn"
            @click="$emit('decrease')"
          >
            <text>-</text>
          </button>
        </view>

        <view class="action-section quantity-section">
          <view
            class="quantity-display"
            :class="{ 'has-quantity': selectedQuantity > 0 }"
          >
            <text class="quantity-number">{{ selectedQuantity }}</text>
            <text class="quantity-max">/ {{ dish.totalQuantity }}</text>
          </view>
        </view>

        <view class="action-section detail-section">
          <button class="detail-btn" @click="$emit('show-detail')">
            <text class="detail-text">详情</text>
          </button>
        </view>
      </view>
    </view>
  </view>
</template>

<script>
import { DENSITY_MODES } from '../../utils/storage.js'

export default {
  name: 'KitchenDishCard',
  props: {
    dish: {
      type: Object,
      required: true
    },
    selectedQuantity: {
      type: Number,
      default: 0
    },
    /** 新单角标：由告警引擎 newBadges 驱动，与超时红边框用外发光区分 */
    isNew: {
      type: Boolean,
      default: false
    },
    /** 显示密度：standard | compact | ultra（厨房页进页快照） */
    density: {
      type: String,
      default: DENSITY_MODES.STANDARD
    }
  },
  emits: ['increase', 'decrease', 'show-detail'],
  setup() {
    const formatOrderTime = (timeStr) => {
      const date = new Date(timeStr)
      const hours = date.getHours().toString().padStart(2, '0')
      const minutes = date.getMinutes().toString().padStart(2, '0')
      return `${hours}:${minutes}`
    }

    return { formatOrderTime, DENSITY_MODES }
  }
}
</script>

<style scoped>
.dish-card {
  background: white;
  border-radius: 12upx;
  margin: 0;
  padding: clamp(8upx, 1vh, 12upx);
  border: 3upx solid #1890FF;
  transition: all 0.3s ease;
  width: 100%;
  min-width: 0;
  min-height: clamp(280upx, 35vh, 400upx);
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  box-sizing: border-box;
  position: relative;
  cursor: pointer;
  box-shadow: 0 2upx 8upx rgba(0,0,0,0.06);
  touch-action: manipulation;
  user-select: none;
  -webkit-user-select: none;
}

.dish-card:hover {
  transform: translateY(-4upx);
  box-shadow: 0 8upx 24upx rgba(0,0,0,0.15);
  border-color: #40A9FF;
}

.dish-card:active {
  transform: scale(0.98);
  box-shadow: 0 4upx 16upx rgba(82, 196, 26, 0.3);
  border-color: #52C41A;
  background: linear-gradient(135deg, rgba(82, 196, 26, 0.05), rgba(255, 255, 255, 1));
}

.dish-card.selected {
  border-color: #52C41A;
  box-shadow: 0 4upx 16upx rgba(82, 196, 26, 0.2);
}

.dish-card.dish-overtime {
  border-color: #FF4D4F;
  background: linear-gradient(135deg, #FFF2F0, #FFFFFF);
  box-shadow: 0 4upx 16upx rgba(255, 77, 79, 0.3);
}

/* 新单高亮：外发光，避免与超时红边框抢同一套 border 视觉 */
.dish-card.dish-new {
  box-shadow:
    0 0 0 4upx rgba(250, 173, 20, 0.55),
    0 0 24upx rgba(250, 173, 20, 0.45),
    0 4upx 16upx rgba(0, 0, 0, 0.08);
}

.dish-card.dish-new.dish-overtime {
  box-shadow:
    0 0 0 4upx rgba(250, 173, 20, 0.65),
    0 0 28upx rgba(250, 173, 20, 0.4),
    0 4upx 16upx rgba(255, 77, 79, 0.3);
  border-color: #FF4D4F;
}

.new-badge {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 2;
  min-width: 48upx;
  padding: 4upx 12upx;
  background: linear-gradient(135deg, #FAAD14, #FFC53D);
  border-radius: 0 12upx 0 12upx;
  box-shadow: 0 2upx 8upx rgba(250, 173, 20, 0.45);
}

.new-badge-text {
  color: #fff;
  font-size: 22upx;
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: 1upx;
}

.dish-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1upx;
  flex-shrink: 0;
}

.dish-name {
  font-size: 36upx;
  font-weight: bold;
  color: #2C3E50;
  line-height: 1.0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  max-width: 65%;
}

.dish-quantity {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  gap: 4upx;
  flex-shrink: 0;
  white-space: nowrap;
}

.quantity-text {
  font-size: 44upx;
  font-weight: 800;
  color: #1890FF;
  line-height: 1;
  letter-spacing: -1upx;
}

.dish-quantity .orders-count {
  font-size: 22upx;
  color: #8C8C8C;
  line-height: 1;
  font-weight: 600;
}

.time-info {
  display: flex;
  align-items: center;
  gap: 8upx;
  margin-bottom: 1upx;
  flex-shrink: 0;
}

.time-label {
  font-size: 24upx;
  color: #666;
  white-space: nowrap;
  font-weight: 500;
}

.time-value {
  font-size: 36upx;
  font-weight: bold;
  white-space: nowrap;
}

.time-value.urgent {
  color: #FF4D4F;
}

.time-value.high {
  color: #FA8C16;
}

.time-value.normal {
  color: #52C41A;
}

.orders-detail {
  background: #F8F9FA;
  border-radius: 8upx;
  padding: 4upx;
  margin-bottom: 1upx;
  flex: 1;
  overflow: hidden;
  min-height: 0;
  max-height: 260upx;
}

.orders-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8upx;
  justify-content: flex-start;
  overflow-y: auto;
  max-height: 210upx;
  padding: 2upx;
}

.orders-grid::-webkit-scrollbar {
  width: 6upx;
}

.orders-grid::-webkit-scrollbar-track {
  background: #f1f1f1;
  border-radius: 3upx;
}

.orders-grid::-webkit-scrollbar-thumb {
  background: #c1c1c1;
  border-radius: 3upx;
}

.orders-grid::-webkit-scrollbar-thumb:hover {
  background: #a8a8a8;
}

.order-block {
  background: white;
  border-radius: 6upx;
  padding: 8upx 10upx;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  width: calc((100% - 8upx) / 2);
  min-height: 65upx;
  border: 1upx solid #D9F7BE;
  box-shadow: 0 2upx 6upx rgba(0,0,0,0.1);
  transition: all 0.2s ease;
  gap: 3upx;
  flex-shrink: 0;
  box-sizing: border-box;
}

.order-block:hover {
  transform: translateY(-2upx);
  box-shadow: 0 4upx 12upx rgba(0,0,0,0.15);
  border-color: #1890FF;
}

.block-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  gap: 8upx;
}

.block-table {
  font-size: 20upx;
  font-weight: bold;
  color: #1890FF;
  white-space: nowrap;
  text-align: left;
  line-height: 1.2;
  flex-shrink: 0;
}

.block-quantity {
  font-size: 18upx;
  color: #666;
  white-space: nowrap;
  font-weight: 500;
  text-align: right;
  line-height: 1.2;
  flex-shrink: 0;
}

.block-time {
  font-size: 20upx;
  color: #333;
  white-space: nowrap;
  text-align: center;
  line-height: 1.2;
  margin-top: 2upx;
  font-weight: 500;
}

.dish-actions {
  display: flex;
  justify-content: center;
  flex-shrink: 0;
  margin-top: auto;
}

.actions-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8upx;
  padding: 12upx;
}

.action-section {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  height: 64upx;
  min-width: 0;
}

.decrease-section {
  justify-content: flex-start;
}

.quantity-section {
  justify-content: center;
}

.detail-section {
  justify-content: flex-end;
}

.quantity-btn {
  width: 100%;
  height: 64upx;
  border-radius: 12upx;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 36upx;
  font-weight: bold;
  transition: all 0.3s ease;
  touch-action: manipulation;
  user-select: none;
  box-shadow: 0 4upx 12upx rgba(0,0,0,0.15);
  flex-shrink: 0;
}

.decrease-btn {
  background: linear-gradient(135deg, #FF6B6B, #FF8E85);
  color: white;
  border: 2upx solid rgba(255, 255, 255, 0.3);
  position: relative;
  overflow: hidden;
}

.decrease-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.2), transparent);
  pointer-events: none;
}

.decrease-btn:disabled {
  background: #CCCCCC;
  color: #999999;
  opacity: 0.6;
  box-shadow: none;
  border-color: transparent;
}

.decrease-btn:disabled::before {
  display: none;
}

.decrease-btn:active:not(:disabled) {
  transform: scale(0.92);
  background: linear-gradient(135deg, #FF5555, #FF7775);
  box-shadow: 0 4upx 12upx rgba(255, 107, 107, 0.4);
}

.detail-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12upx 16upx;
  background: linear-gradient(135deg, #1890FF, #40A9FF);
  color: white;
  border: none;
  border-radius: 12upx;
  height: 64upx;
  width: 100%;
  transition: all 0.3s ease;
  box-shadow: 0 4upx 12upx rgba(24, 144, 255, 0.3);
  touch-action: manipulation;
  user-select: none;
}

.detail-btn:active {
  transform: scale(0.95);
  background: linear-gradient(135deg, #096dd9, #1890FF);
  box-shadow: 0 2upx 8upx rgba(24, 144, 255, 0.4);
}

.detail-text {
  font-size: 26upx;
  font-weight: 600;
  line-height: 1;
}

.quantity-display {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  background: rgba(24, 144, 255, 0.08);
  border: 2upx solid rgba(24, 144, 255, 0.2);
  border-radius: 12upx;
  padding: 8upx 16upx;
  width: 100%;
  height: 64upx;
  text-align: center;
  transition: all 0.3s ease;
  gap: 4upx;
}

.quantity-display.has-quantity {
  background: linear-gradient(135deg, rgba(24, 144, 255, 0.15), rgba(64, 169, 255, 0.1));
  border-color: #1890FF;
  box-shadow: 0 4upx 12upx rgba(24, 144, 255, 0.2);
  transform: scale(1.02);
}

.quantity-number {
  font-size: 48upx;
  font-weight: 900;
  color: #1890FF;
  line-height: 1;
  text-shadow: 0 2upx 4upx rgba(24, 144, 255, 0.3);
  transition: all 0.3s ease;
}

.quantity-display.has-quantity .quantity-number {
  color: #0066CC;
  font-size: 52upx;
  text-shadow: 0 3upx 6upx rgba(24, 144, 255, 0.4);
  animation: quantity-pulse 1.5s ease-in-out infinite;
}

@keyframes quantity-pulse {
  0%, 100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.05);
  }
}

.quantity-max {
  font-size: 24upx;
  color: #8c8c8c;
  line-height: 1;
  font-weight: 500;
  opacity: 0.8;
}

/* —— 紧凑：每桌明细收成单行 —— */
.dish-card.density-compact {
  min-height: clamp(200upx, 28vh, 300upx);
}

.dish-card.density-compact .orders-detail {
  max-height: 160upx;
  flex: 0 1 auto;
}

.dish-card.density-compact .orders-grid {
  max-height: 140upx;
  flex-direction: column;
  flex-wrap: nowrap;
  gap: 4upx;
}

.dish-card.density-compact .order-block {
  width: 100%;
  min-height: 0;
  flex-direction: row;
  align-items: center;
  padding: 4upx 10upx;
  gap: 0;
}

.dish-card.density-compact .block-header {
  width: 100%;
}

.dish-card.density-compact .block-time {
  margin-top: 0;
  margin-left: auto;
  font-size: 18upx;
  color: #666;
}

/* —— 超紧凑：只留菜名+份数+最长等待，降低最小高度 —— */
.dish-card.density-ultra {
  min-height: clamp(140upx, 18vh, 220upx);
}

.dish-card.density-ultra .dish-name {
  font-size: 32upx;
  max-width: 70%;
}

.dish-card.density-ultra .quantity-text {
  font-size: 36upx;
}

.dish-card.density-ultra .time-value {
  font-size: 30upx;
}

.dish-card.density-ultra .actions-container {
  padding: 8upx;
}

.dish-card.density-ultra .action-section,
.dish-card.density-ultra .quantity-btn,
.dish-card.density-ultra .detail-btn,
.dish-card.density-ultra .quantity-display {
  height: 52upx;
}
</style>
