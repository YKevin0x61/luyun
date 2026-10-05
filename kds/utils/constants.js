/**
 * KDS系统常量配置
 */

import { getTimeThresholdsMs } from './timeThresholds.js'

// API配置
/**
 * API 地址的兜底（C-A5）：**本地存储没配过时才用**，正常路径是设置页写入
 * `kds_api_settings.baseUrl`（`utils/storage.js` 的 `ApiSettingsManager`）。
 *
 * 这里**不再硬编码生产域名**。原来的兜底是 `https://luyun.ykevin0x61.com`，于是一台
 * 本机/内网部署的屏即使带了 `?token=`，也照样去打生产 API：首页显示的是**生产数据**
 * （待做 820 / 767），而 `/api/stations` 直接 `request:fail`，控制台一句
 * 「初始化档口配置失败：网络连接失败」。运维分不清是"屏坏了"还是"指错服务器"
 * —— 这比"没数据"更危险，因为数字看着是真的。
 *
 * 现在的取法按可用性从高到低：
 *  1. H5（本仓库交付的那一种形态）：`location.origin`。屏从哪台服务器加载的，就打哪台
 *     —— 本机预览、门店内网、反向代理后面，三处都对，也不需要运维先配一次；
 *  2. 非 H5（APP-PLUS 等没有 `location` 的运行环境）：空串。**故意不给生产域名**：
 *     让请求失败得明白（地址没配），而不是悄悄打到别的门店的服务器上；
 *  3. 开发构建：`http://localhost:8000`（既有行为，本地后端默认端口）。
 *
 * 真值 `#ifdef` 用不了（那是构建期宏，代码会同时进 H5 与原生包），`process.env` 在 H5 里
 * 也已被替换成字面量 —— 所以运行时判断只放在这一个函数里，两个消费方
 * （`storage.js` 的 `getBaseUrl()` 与这里的 `API_CONFIG`）都调它，不各写一份。
 */
export const API_FALLBACK_DEV_URL = 'http://localhost:8000'

export function resolveFallbackApiBaseUrl({ isDev = false, origin = '' } = {}) {
  const cleaned = typeof origin === 'string' ? origin.trim().replace(/\/$/, '') : ''
  // `file://` 打开（直接双击 dist/index.html）时 origin 是 "file://" 或 "null"，
  // 拼出来的地址只会 404 —— 那种情况当"没配"处理。
  if (cleaned && /^https?:\/\//i.test(cleaned)) return cleaned
  if (isDev) return API_FALLBACK_DEV_URL
  return ''
}

export const API_CONFIG = {
  BASE_URL: process.env.NODE_ENV === 'development'
    ? API_FALLBACK_DEV_URL
    : resolveFallbackApiBaseUrl({
        origin: typeof location !== 'undefined' ? location.origin : '',
      }),
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