import App from './App.vue'

// #ifndef VUE3
import Vue from 'vue'
import './uni.promisify.adaptor'
Vue.config.productionTip = false
App.mpType = 'app'
const app = new Vue({
  ...App
})
app.$mount()
// #endif

// #ifdef VUE3
import { createSSRApp } from 'vue'
import { createPinia } from 'pinia'

// 导入所有store以确保它们被正确注册
import { useOrdersStore } from './stores/orders.js'
import { useStationsStore } from './stores/stations.js'
import { useRealtimeStore } from './stores/realtime.js'

// 导入工具类和常量
import { TimeCalculator } from './utils/timeCalculator.js'
import { OrderPrioritySelector } from './utils/prioritySelector.js'
import { StationWindowMapper } from './utils/stationWindowMapper.js'
import { KITCHEN_STATIONS, API_CONFIG } from './utils/constants.js'

export function createApp() {
  const app = createSSRApp(App)
  
  // 创建Pinia实例
  const pinia = createPinia()
  app.use(pinia)
  
  // 配置全局属性，让所有组件都能访问工具类
  app.config.globalProperties.$timeCalculator = TimeCalculator
  app.config.globalProperties.$prioritySelector = OrderPrioritySelector
  app.config.globalProperties.$stationMapper = StationWindowMapper
  app.config.globalProperties.$kitchenStations = KITCHEN_STATIONS
  app.config.globalProperties.$apiConfig = API_CONFIG
  
  // 全局错误处理
  app.config.errorHandler = (err, vm, info) => {
    console.error('Global error:', err)
    console.error('Error info:', info)
    
    // 显示用户友好的错误提示
    uni.showToast({
      title: '系统错误，请重试',
      icon: 'error',
      duration: 3000
    })
    
    // 可以在这里添加错误上报逻辑
    // 例如发送到错误监控服务
  }
  
  // 全局警告处理（开发环境）
  app.config.warnHandler = (msg, vm, trace) => {
    if (process.env.NODE_ENV === 'development') {
      console.warn('Global warning:', msg)
      console.warn('Warning trace:', trace)
    }
  }
  
  // 应用初始化（在 createApp 时执行一次）
  initializeAppConfig()
  preloadCriticalData()
  setupNetworkStatusListener()
  initializeRealtimeConnection()
  
  return {
    app,
    pinia
  }
}

// 启动全局 WebSocket 实时连接（替代原有轮询）。连接是模块级单例，
// 幂等：即使各页面 onMounted 里也调用 init()，也只会真正建立一次连接。
function initializeRealtimeConnection() {
  try {
    useRealtimeStore().init()
    console.log('实时连接已初始化')
  } catch (error) {
    console.error('初始化实时连接失败:', error)
  }
}

// 初始化应用配置
function initializeAppConfig() {
  try {
    // 初始化全局状态对象，防止在非 H5 环境中 window 未定义
    if (!globalThis.lastOrderCount) globalThis.lastOrderCount = {}
    if (!globalThis.lastStatsKey) globalThis.lastStatsKey = {}
    if (!globalThis.lastCompletedCount) globalThis.lastCompletedCount = {}
    console.log('全局状态对象已初始化')
    
    // 设置API基础配置
    console.log('初始化API配置:', API_CONFIG)
    
    // 初始化本地存储
    const appConfig = uni.getStorageSync('app_config') || {}
    if (!appConfig.initialized) {
      const defaultConfig = {
        initialized: true,
        version: '1.0.0',
        firstLaunch: new Date().toISOString(),
        theme: 'default',
        language: 'zh-CN',
        notifications: true,
        autoRefresh: true,
        refreshInterval: 30000, // 30秒
        enableSound: false,
        enableVibration: false
      }
      
      uni.setStorageSync('app_config', defaultConfig)
      console.log('应用配置初始化完成:', defaultConfig)
    }
    
  } catch (error) {
    console.error('初始化应用配置失败:', error)
  }
}

// 预加载关键数据
async function preloadCriticalData() {
  try {
    console.log('开始预加载关键数据...')
    
    // 由于这是在应用启动时调用，我们需要确保Pinia store已经可用
    // 这里只做基础的数据结构初始化
    
    // 预加载档口配置
    console.log('预加载档口配置:', KITCHEN_STATIONS)
    
    // 验证工具类可用性
    const testTime = TimeCalculator.formatTime(new Date())
    console.log('工具类验证通过，当前时间:', testTime)
    
    console.log('关键数据预加载完成')
    
  } catch (error) {
    console.error('预加载关键数据失败:', error)
  }
}

// 设置网络状态监听
function setupNetworkStatusListener() {
  try {
    // 监听网络状态变化
    uni.onNetworkStatusChange((res) => {
      console.log('网络状态变化:', res)
      
      if (res.isConnected) {
        console.log('网络已连接，类型:', res.networkType)
        uni.showToast({
          title: '网络已连接',
          icon: 'success',
          duration: 1500
        })
      } else {
        console.log('网络已断开')
        uni.showToast({
          title: '网络连接中断',
          icon: 'error',
          duration: 3000
        })
      }
    })
    
    // 获取初始网络状态
    uni.getNetworkType({
      success: (res) => {
        console.log('当前网络类型:', res.networkType)
      }
    })
    
  } catch (error) {
    console.error('设置网络状态监听失败:', error)
  }
}

// #endif