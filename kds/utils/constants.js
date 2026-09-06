/**
 * KDS系统常量配置
 */

import { getTimeThresholdsMs } from './timeThresholds.js'

// API配置
export const API_CONFIG = {
  BASE_URL: process.env.NODE_ENV === 'development'
    ? 'http://localhost:8000'
    : 'https://luyun.ykevin0x61.com',
  TIMEOUT: 10000,
  RETRY_COUNT: 3
}

// 档口 catalog 只从 /api/stations 进 Pinia stations store，不在此硬编码。

// 窗口配置
export const DELIVERY_WINDOWS = {
  WINDOW1: {
    id: 'window1',
    name: '窗口1',
    description: '西饼档专用窗口',
    stations: ['xibing'],
    path: '/pages/delivery/window1/window1'
  },
  WINDOW2: {
    id: 'window2',
    name: '窗口2',
    description: '肠粉蒸笼共用窗口',
    stations: ['changfen', 'shulong'],
    path: '/pages/delivery/window2/window2'
  },
  WINDOW3: {
    id: 'window3',
    name: '窗口3',
    description: '明档2专用窗口',
    stations: ['mingdang2'],
    path: '/pages/delivery/window3/window3'
  },
  WINDOW4: {
    id: 'window4',
    name: '窗口4',
    description: '明档1煎炸共用窗口',
    stations: ['mingdang1', 'jianzha'],
    path: '/pages/delivery/window4/window4'
  },
  WINDOW5: {
    id: 'window5',
    name: '其他窗口',
    description: '其他档口专用窗口',
    stations: ['qita'],
    path: '/pages/delivery/window5/window5'
  }
}

// 档口到窗口的映射
export const STATION_WINDOW_MAPPING = {
  'xibing': 1,
  'changfen': 2,
  'shulong': 2,
  'mingdang1': 4,
  'mingdang2': 3,
  'jianzha': 4,
  'qita': 5
}

// 菜品状态
export const DISH_STATUS = {
  PENDING: '待出餐',
  READY: '已制作待上菜',
  SERVED: '已上菜',
  CANCELLED: '退菜'
}

/** 是否为退菜/退款记录（不应出现在厨房待制作列表） */
export function isRefundOrder(order) {
  if (!order) return false
  if (order.status === DISH_STATUS.CANCELLED) return true
  if (typeof order.quantity === 'number' && order.quantity < 0) return true
  const flowId = order.business_flow_id || ''
  return flowId.includes('_refund_')
}

// 优先级
export const PRIORITY_LEVELS = {
  URGENT: 'urgent',
  HIGH: 'high',
  NORMAL: 'normal'
}

// 时间阈值 (毫秒)：读本地 alert warnMin/urgentMin；勿再写死 15/20
export const TIME_THRESHOLDS = {
  get WARNING() {
    return getTimeThresholdsMs().warning
  },
  get URGENT() {
    return getTimeThresholdsMs().urgent
  }
}

// 轮询配置
export const POLLING_CONFIG = {
  DEFAULT_INTERVAL: 3000,   // 默认3秒轮询
  MIN_INTERVAL: 1000,       // 最小1秒
  MAX_INTERVAL: 30000,      // 最大30秒
  RETRY_TIMES: 3,           // 重试次数
  CACHE_TTL: 30000          // 缓存30秒
}