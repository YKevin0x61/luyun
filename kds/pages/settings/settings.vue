<template>
  <view class="settings-container">
    <view class="page-header">
      <view class="header-bar">
        <text class="back-link" @click="goHome">← 返回</text>
        <view class="header-titles">
          <text class="page-title">系统设置</text>
          <text class="page-subtitle">{{ activeSectionSubtitle }}</text>
        </view>
        <view class="header-secondary">
          <text class="nav-link" @click="goOrders">订单</text>
          <text class="nav-link" @click="goManagement">管理</text>
        </view>
      </view>
    </view>

    <view class="settings-shell">
      <view class="settings-nav">
        <view
          v-for="section in settingsSections"
          :key="section.id"
          class="settings-nav-item"
          :class="{ 'settings-nav-item--on': activeSection === section.id }"
          @click="activeSection = section.id"
        >
          <text class="settings-nav-label">{{ section.label }}</text>
          <text class="settings-nav-desc">{{ section.desc }}</text>
        </view>
      </view>

      <view class="settings-pane">
        <!-- 连接：API · 鉴权 · 实时 · presets -->
        <view v-show="activeSection === 'connect'" class="settings-section">
          <view class="settings-card">
            <view class="card-header">
              <text class="card-title">API 服务器设置</text>
              <view class="connection-status" :class="connectionStatusClass">
                <text class="status-text">{{ connectionStatusText }}</text>
              </view>
            </view>
            <view class="card-content">
              <view class="form-item">
                <text class="form-label">API 地址</text>
                <input
                  class="form-input"
                  v-model="apiSettings.baseUrl"
                  placeholder="请输入后端 API 地址"
                  :disabled="testing || saving"
                />
                <text class="form-hint">例如: https://luyun.ykevin0x61.com（生产）或 http://10.0.2.2:8000（模拟器）</text>
              </view>
              <view class="current-settings">
                <text class="settings-label">当前配置:</text>
                <text class="settings-value">{{ currentBaseUrl }}</text>
                <text class="settings-time" v-if="apiSettings.updatedAt">
                  最后更新: {{ formatTime(apiSettings.updatedAt) }}
                </text>
              </view>
              <view class="button-group">
                <button
                  class="btn-test"
                  :class="{ 'btn-loading': testing }"
                  :disabled="!apiSettings.baseUrl || testing || saving"
                  @click="testConnection"
                >
                  {{ testing ? '测试中...' : '测试连接' }}
                </button>
                <button
                  class="btn-save"
                  :class="{ 'btn-loading': saving }"
                  :disabled="!apiSettings.baseUrl || testing || saving"
                  @click="saveSettings"
                >
                  {{ saving ? '保存中...' : '保存设置' }}
                </button>
                <button
                  class="btn-reset"
                  :disabled="testing || saving"
                  @click="resetToDefault"
                >
                  重置默认
                </button>
              </view>
            </view>
          </view>

          <view class="settings-card">
            <view class="card-header">
              <text class="card-title">API 鉴权</text>
              <view class="connection-status" :class="authStatusClass">
                <text class="status-text">{{ authStatusText }}</text>
              </view>
            </view>
            <view class="card-content">
              <view class="auth-mode-tabs">
                <view
                  class="auth-mode-tab"
                  :class="{ 'auth-mode-tab-active': authMode === 'manual' }"
                  @click="authMode = 'manual'"
                >
                  <text class="auth-mode-tab-text">手动 Token</text>
                </view>
                <view
                  class="auth-mode-tab"
                  :class="{ 'auth-mode-tab-active': authMode === 'login' }"
                  @click="authMode = 'login'"
                >
                  <text class="auth-mode-tab-text">账号登录</text>
                </view>
              </view>

              <view v-if="authMode === 'manual'">
                <view class="form-item">
                  <text class="form-label">API Token</text>
                  <input
                    class="form-input"
                    v-model="manualToken"
                    password
                    placeholder="粘贴或输入 API Token"
                    :disabled="authBusy"
                  />
                  <text class="form-hint">可在 Web 管理后台「设置」中生成 Token</text>
                </view>
                <view class="button-group">
                  <button
                    class="btn-save"
                    :class="{ 'btn-loading': authBusy }"
                    :disabled="!manualToken || authBusy"
                    @click="saveManualToken"
                  >
                    {{ authBusy ? '保存中...' : '保存 Token' }}
                  </button>
                  <button
                    class="btn-test"
                    :disabled="!manualToken || authBusy"
                    @click="saveAndTestManualToken"
                  >
                    保存并测试
                  </button>
                </view>
              </view>

              <view v-else>
                <view class="form-item">
                  <text class="form-label">用户名</text>
                  <input
                    class="form-input"
                    v-model="loginUsername"
                    placeholder="门店管理员账号"
                    :disabled="authBusy"
                  />
                </view>
                <view class="form-item">
                  <text class="form-label">密码</text>
                  <input
                    class="form-input"
                    v-model="loginPassword"
                    password
                    placeholder="登录密码"
                    :disabled="authBusy"
                  />
                </view>
                <view class="button-group">
                  <button
                    class="btn-save"
                    :class="{ 'btn-loading': authBusy }"
                    :disabled="!loginUsername || !loginPassword || authBusy"
                    @click="loginAndSaveToken"
                  >
                    {{ authBusy ? '登录中...' : '登录并保存' }}
                  </button>
                </view>
              </view>

              <view class="current-settings" v-if="savedAuthInfo.token">
                <text class="settings-label">已保存 Token:</text>
                <text class="settings-value">{{ maskedToken }}</text>
                <text class="settings-time" v-if="savedAuthInfo.updatedAt">
                  最后更新: {{ formatTime(savedAuthInfo.updatedAt) }}
                </text>
              </view>

              <view class="button-group auth-action-buttons">
                <button
                  class="btn-test"
                  :disabled="authBusy || !savedAuthInfo.token"
                  @click="testAuth"
                >
                  {{ authTesting ? '测试中...' : '测试鉴权' }}
                </button>
                <button
                  class="btn-reset"
                  :disabled="authBusy || !savedAuthInfo.token"
                  @click="clearAuth"
                >
                  清除鉴权
                </button>
              </view>

              <view class="realtime-hint">
                <text class="hint-text">• 写操作（如标记出餐）需要有效的 API Token</text>
                <text class="hint-text">• 读接口无需鉴权，Token 仅用于写操作</text>
                <text class="hint-text">• 账号登录会自动获取并保存 Token</text>
              </view>
            </view>
          </view>

          <view class="settings-card">
            <view class="card-header">
              <text class="card-title">实时连接状态</text>
              <view class="connection-status" :class="realtimeStatusClass">
                <text class="status-text">{{ realtimeStatusText }}</text>
              </view>
            </view>
            <view class="card-content">
              <view class="info-list">
                <view class="info-item">
                  <text class="info-label">连接状态</text>
                  <text class="info-value">{{ realtimeStatusText }}</text>
                </view>
                <view class="info-item">
                  <text class="info-label">上次更新</text>
                  <text class="info-value">{{ realtimeLastUpdateText }}</text>
                </view>
                <view class="info-item">
                  <text class="info-label">服务器地址</text>
                  <text class="info-value">{{ currentBaseUrl }}</text>
                </view>
              </view>
              <view class="realtime-hint">
                <text class="hint-text">• 系统已切换为 WebSocket 实时推送，无需手动配置刷新频率</text>
                <text class="hint-text">• 断线会自动重连；厨房页会在断线时显示醒目提示</text>
                <text class="hint-text">• 各页面仍保留下拉手动刷新，可随时强制拉取最新数据</text>
              </view>
            </view>
          </view>

          <view class="settings-card">
            <view class="card-header">
              <text class="card-title">快速配置</text>
            </view>
            <view class="card-content">
              <view class="preset-list">
                <view
                  class="preset-item"
                  v-for="preset in presetConfigs"
                  :key="preset.name"
                  @click="selectPreset(preset)"
                >
                  <view class="preset-info">
                    <text class="preset-name">{{ preset.name }}</text>
                    <text class="preset-url">{{ preset.url }}</text>
                  </view>
                  <text class="preset-action">选择</text>
                </view>
              </view>
            </view>
          </view>
        </view>

        <!-- 本屏：职责档口 · 密度 -->
        <view v-show="activeSection === 'screen'" class="settings-section">
          <view class="settings-card">
            <view class="card-header">
              <text class="card-title">本屏 KDS 设置</text>
              <view class="connection-status" :class="watchedStationsBadgeClass">
                <text class="status-text">{{ watchedStationsStatusText }}</text>
              </view>
            </view>
            <view class="card-content">
              <view class="form-item">
                <text class="form-label">本屏负责档口</text>
                <text class="form-hint">不选 = 全部档口；选 1 个则厨房页锁定该档全屏。改动即时保存，重进厨房页生效。</text>
                <view class="station-chip-list">
                  <view
                    v-for="station in stationOptions"
                    :key="station.id"
                    class="station-chip"
                    :class="{ 'station-chip-active': isWatchedStationSelected(station.id) }"
                    :style="isWatchedStationSelected(station.id) ? { borderColor: station.color } : {}"
                    @click="toggleWatchedStation(station.id)"
                  >
                    <text class="station-chip-text">{{ station.name }}</text>
                  </view>
                </view>
              </view>
              <view class="button-group">
                <button
                  class="btn-reset"
                  :disabled="watchedStations.length === 0"
                  @click="setWatchedStationsAll"
                >
                  设为全部档口
                </button>
              </view>
              <view class="form-item density-form-item">
                <text class="form-label">显示密度</text>
                <text class="form-hint">高峰期用更紧凑布局一屏看更多。改动即时保存，重进厨房页生效。</text>
                <view class="density-mode-list">
                  <view
                    v-for="option in densityOptions"
                    :key="option.value"
                    class="density-mode-chip"
                    :class="{ 'density-mode-chip-active': density === option.value }"
                    @click="setDensityMode(option.value)"
                  >
                    <text class="density-mode-chip-text">{{ option.label }}</text>
                  </view>
                </view>
              </view>
              <view class="form-item">
                <text class="form-label">菜品卡片份数上限</text>
                <input
                  class="form-input"
                  type="number"
                  v-model="dishCardQuantityCapInput"
                  placeholder="0"
                  @blur="persistDishCardQuantityCap"
                  @confirm="persistDishCardQuantityCap"
                />
                <text class="form-hint">0=不拆分；超过则按最早订单拆成多张卡。改动即时保存，重进厨房页生效。</text>
              </view>
            </view>
          </view>
        </view>

        <!-- 设备与系统：蓝牙 · 系统信息 -->
        <view v-show="activeSection === 'device'" class="settings-section">
          <view class="settings-card" v-if="isAppPlus">
            <view class="card-header">
              <text class="card-title">蓝牙打印机</text>
              <view class="connection-status" :class="printerStatusClass">
                <text class="status-text">{{ printerStatusText }}</text>
              </view>
            </view>
            <view class="card-content">
              <view class="form-item printer-toggle-row">
                <text class="form-label">制作完成自动出单</text>
                <switch
                  :checked="printerSettings.enabled"
                  :disabled="printerBusy"
                  @change="onPrintEnabledChange"
                />
              </view>
              <view class="current-settings">
                <text class="settings-label">当前打印机:</text>
                <text class="settings-value">
                  {{ printerSettings.deviceName || '未选择' }}
                </text>
                <text class="settings-time" v-if="printerSettings.deviceAddress">
                  {{ printerSettings.deviceAddress }}
                </text>
              </view>
              <view class="button-group">
                <button
                  class="btn-test"
                  :disabled="printerBusy"
                  @click="refreshPairedDevices"
                >
                  {{ printerScanning ? '扫描中...' : '搜索设备' }}
                </button>
                <button
                  class="btn-save"
                  :class="{ 'btn-loading': printerBusy }"
                  :disabled="!selectedPrinterAddress || printerBusy"
                  @click="connectSelectedPrinter"
                >
                  {{ printerBusy ? '连接中...' : '连接打印机' }}
                </button>
                <button
                  class="btn-reset"
                  :disabled="printerBusy || !printerSettings.enabled"
                  @click="testPrinter"
                >
                  测试打印
                </button>
              </view>
              <view class="printer-device-list" v-if="pairedDevices.length > 0">
                <text class="presets-title">已配对 / 已发现设备</text>
                <view
                  class="preset-item printer-device-item"
                  v-for="device in pairedDevices"
                  :key="device.address"
                  :class="{ 'printer-device-selected': selectedPrinterAddress === device.address }"
                  @click="selectPrinterDevice(device)"
                >
                  <view class="preset-info">
                    <text class="preset-name">{{ device.name || '未知设备' }}</text>
                    <text class="preset-url">{{ device.address }}</text>
                    <text class="preset-url">{{ device.paired ? '已配对，可连接' : '未配对，请先在系统蓝牙中配对' }}</text>
                  </view>
                  <text class="preset-action">
                    {{ selectedPrinterAddress === device.address ? '已选' : '选择' }}
                  </text>
                </view>
              </view>
              <view class="realtime-hint">
                <text class="hint-text">• 请先在系统蓝牙中配对 BlueTooth Printer（默认 PIN: 1234 或 0000）</text>
                <text class="hint-text">• 搜索可发现附近设备；连接前须完成系统配对</text>
                <text class="hint-text">• 每完成一道菜打印一张，自动切纸</text>
                <text class="hint-text">• 使用项目内置 kds-bluetooth-printer 插件，打包 APK 即可</text>
              </view>
            </view>
          </view>

          <view v-else class="settings-card">
            <view class="card-header">
              <text class="card-title">蓝牙打印机</text>
            </view>
            <view class="card-content">
              <text class="form-hint">蓝牙打印仅在 APP 端可用；当前为 H5，请使用打包客户端配置打印机。</text>
            </view>
          </view>

          <view class="settings-card">
            <view class="card-header">
              <text class="card-title">系统信息</text>
            </view>
            <view class="card-content">
              <view class="info-list">
                <view class="info-item">
                  <text class="info-label">应用版本</text>
                  <text class="info-value">{{ systemInfo.version }}</text>
                </view>
                <view class="info-item">
                  <text class="info-label">设备信息</text>
                  <text class="info-value">{{ systemInfo.platform }} {{ systemInfo.system }}</text>
                </view>
                <view class="info-item">
                  <text class="info-label">网络类型</text>
                  <text class="info-value">{{ systemInfo.networkType }}</text>
                </view>
              </view>
            </view>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script>
import {
  ApiSettingsManager,
  ApiAuthManager,
  PrinterSettingsManager,
  ScreenSettingsManager,
  DENSITY_MODES
} from '../../utils/storage.js'
import {
  isPrinterPlatformSupported,
  getPairedDevices,
  startDeviceDiscovery,
  stopDeviceDiscovery,
  connectPrinter,
  isPrinterConnected
} from '../../utils/bluetoothPrinter.js'
import { printTestTicket } from '../../utils/dishTicketPrinter.js'
import { useRealtimeStore } from '../../stores/realtime.js'
import { useStationsStore } from '../../stores/stations.js'

export default {
  name: 'Settings',
  
  data() {
    return {
      realtimeStore: useRealtimeStore(),
      stationsStore: useStationsStore(),
      /** @type {'connect'|'screen'|'device'} */
      activeSection: 'connect',
      settingsSections: [
        { id: 'connect', label: '连接', desc: '服务器与鉴权' },
        { id: 'screen', label: '本屏', desc: '档口、密度与拆卡' },
        { id: 'device', label: '设备与系统', desc: '打印与版本' }
      ],
      /** @type {string[]} 空数组 = 全部档口 */
      watchedStations: [],
      /** @type {string} ScreenSettingsManager density mode */
      density: DENSITY_MODES.STANDARD,
      densityOptions: [
        { value: DENSITY_MODES.STANDARD, label: '标准' },
        { value: DENSITY_MODES.COMPACT, label: '紧凑' },
        { value: DENSITY_MODES.ULTRA, label: '超紧凑' }
      ],
      /** @type {string} bound to input; normalized integer persisted on blur */
      dishCardQuantityCapInput: '0',
      apiSettings: {
        baseUrl: '',
        updatedAt: null
      },
      currentBaseUrl: '',
      testing: false,
      saving: false,
      connectionStatus: 'unknown', // unknown, connected, disconnected
      authMode: 'manual',
      manualToken: '',
      loginUsername: '',
      loginPassword: '',
      savedAuthInfo: {
        token: '',
        mode: 'manual',
        updatedAt: null
      },
      authStatus: 'unknown', // unknown, valid, invalid, none
      authBusy: false,
      authTesting: false,
      systemInfo: {
        version: '1.0.0',
        platform: '',
        system: '',
        networkType: ''
      },
      presetConfigs: [
        {
          name: 'Android 模拟器',
          url: 'http://10.0.2.2:8000'
        },
        {
          name: '本地开发',
          url: 'http://localhost:8000'
        },
        {
          name: '局域网服务器',
          url: 'http://192.168.1.100:8000'
        },
        {
          name: '生产环境',
          url: 'https://luyun.ykevin0x61.com'
        }
      ],
      isAppPlus: false,
      printerSettings: {
        enabled: false,
        deviceAddress: '',
        deviceName: '',
        updatedAt: null
      },
      pairedDevices: [],
      selectedPrinterAddress: '',
      printerScanning: false,
      printerBusy: false,
      printerConnected: false
    }
  },

  computed: {
    activeSectionSubtitle() {
      const map = {
        connect: 'API 服务器 · 鉴权 · 实时连接 · 快速配置',
        screen: '本屏职责档口 · 显示密度 · 菜品卡片份数上限',
        device: '蓝牙打印 · 系统信息'
      }
      return map[this.activeSection] || '系统设置'
    },

    stationOptions() {
      return this.stationsStore.stationList.map(({ id, name, color }) => ({
        id,
        name,
        color
      }))
    },

    watchedStationsBadgeClass() {
      // Neutral badge — avoid reusing connection status-connected/unknown semantics
      return { 'status-unknown': true }
    },

    watchedStationsStatusText() {
      if (this.watchedStations.length === 0) return '全部档口'
      if (this.watchedStations.length === 1) return '单档口锁定'
      return `${this.watchedStations.length} 个档口`
    },

    realtimeStatusClass() {
      return {
        'status-connected': this.realtimeStore.connectionStatus === 'connected',
        'status-disconnected': this.realtimeStore.connectionStatus === 'disconnected',
        'status-unknown': this.realtimeStore.connectionStatus === 'reconnecting'
      }
    },

    realtimeStatusText() {
      return this.realtimeStore.connectionStatusText
    },

    realtimeLastUpdateText() {
      const time = this.realtimeStore.lastUpdateTime
      return time ? this.formatTime(time) : '暂无'
    },

    printerStatusClass() {
      if (!this.printerSettings.enabled) {
        return { 'status-unknown': true }
      }
      return {
        'status-connected': this.printerConnected,
        'status-disconnected': !this.printerConnected
      }
    },

    printerStatusText() {
      if (!this.printerSettings.enabled) {
        return '未启用'
      }
      return this.printerConnected ? '打印机已连接' : '打印机未连接'
    },
    connectionStatusClass() {
      return {
        'status-connected': this.connectionStatus === 'connected',
        'status-disconnected': this.connectionStatus === 'disconnected',
        'status-unknown': this.connectionStatus === 'unknown'
      }
    },

    connectionStatusText() {
      switch (this.connectionStatus) {
        case 'connected':
          return '连接正常'
        case 'disconnected':
          return '连接失败'
        default:
          return '未知状态'
      }
    },

    authStatusClass() {
      return {
        'status-connected': this.authStatus === 'valid',
        'status-disconnected': this.authStatus === 'invalid',
        'status-unknown': this.authStatus === 'unknown' || this.authStatus === 'none'
      }
    },

    authStatusText() {
      switch (this.authStatus) {
        case 'valid':
          return '鉴权有效'
        case 'invalid':
          return '鉴权无效'
        case 'none':
          return '未配置'
        default:
          return '未知状态'
      }
    },

    maskedToken() {
      const token = this.savedAuthInfo.token || ''
      if (token.length <= 8) {
        return token ? '****' : ''
      }
      return `${token.slice(0, 4)}...${token.slice(-4)}`
    }
  },

  onLoad() {
    // #ifdef APP-PLUS
    this.isAppPlus = true
    // #endif
    this.loadSettings()
    this.loadAuthSettings()
    this.loadPrinterSettings()
    this.loadScreenSettings()
    this.loadSystemInfo()
    this.checkCurrentConnection()
  },

  onUnload() {
    if (this.isAppPlus) {
      stopDeviceDiscovery()
      this.printerScanning = false
    }
  },

  methods: {
    goHome() {
      uni.reLaunch({ url: '/pages/index/index' })
    },

    goOrders() {
      uni.navigateTo({ url: '/pages/orders/orders' })
    },

    goManagement() {
      // #ifdef H5
      window.location.href = '/admin/'
      // #endif
      // #ifndef H5
      uni.showToast({ title: '请使用浏览器访问 /admin/', icon: 'none' })
      // #endif
    },

    /**
     * 加载当前设置
     */
    loadSettings() {
      const settings = ApiSettingsManager.getApiSettings()
      if (settings) {
        this.apiSettings = { ...settings }
      }
      this.currentBaseUrl = ApiSettingsManager.getBaseUrl()
      this.apiSettings.baseUrl = this.currentBaseUrl
    },

    loadScreenSettings() {
      this.watchedStations = [...ScreenSettingsManager.getWatchedStations()]
      this.density = ScreenSettingsManager.getDensity()
      this.dishCardQuantityCapInput = String(
        ScreenSettingsManager.getDishCardQuantityCap()
      )
    },

    isWatchedStationSelected(stationId) {
      return this.watchedStations.includes(stationId)
    },

    persistWatchedStations(stationIds) {
      const next = Array.isArray(stationIds) ? [...stationIds] : []
      const ok = ScreenSettingsManager.setWatchedStations(next)
      if (!ok) {
        uni.showToast({ title: '保存失败', icon: 'error' })
        return false
      }
      this.watchedStations = [...ScreenSettingsManager.getWatchedStations()]
      return true
    },

    toggleWatchedStation(stationId) {
      if (!stationId) return
      const selected = new Set(this.watchedStations)
      if (selected.has(stationId)) {
        selected.delete(stationId)
      } else {
        selected.add(stationId)
      }
      // Preserve stationsStore order for stable chip ↔ tab ordering
      const ordered = this.stationOptions
        .map((s) => s.id)
        .filter((id) => selected.has(id))
      if (!this.persistWatchedStations(ordered)) return
      uni.showToast({
        title: ordered.length === 0 ? '已设为全部档口' : '已保存',
        icon: 'success'
      })
    },

    setWatchedStationsAll() {
      if (!this.persistWatchedStations([])) return
      uni.showToast({ title: '已设为全部档口', icon: 'success' })
    },

    setDensityMode(mode) {
      if (mode === this.density) return
      const ok = ScreenSettingsManager.setDensity(mode)
      if (!ok) {
        uni.showToast({ title: '保存失败', icon: 'error' })
        return
      }
      this.density = ScreenSettingsManager.getDensity()
      uni.showToast({ title: '已保存', icon: 'success' })
    },

    persistDishCardQuantityCap() {
      const ok = ScreenSettingsManager.setDishCardQuantityCap(
        this.dishCardQuantityCapInput
      )
      if (!ok) {
        uni.showToast({ title: '保存失败', icon: 'error' })
        return
      }
      const normalized = ScreenSettingsManager.getDishCardQuantityCap()
      this.dishCardQuantityCapInput = String(normalized)
      uni.showToast({ title: '已保存', icon: 'success' })
    },

    loadAuthSettings() {
      const auth = ApiAuthManager.getAuth()
      if (auth && auth.token) {
        this.savedAuthInfo = { ...auth }
        this.authMode = auth.mode === 'login' ? 'login' : 'manual'
        if (auth.mode === 'login') {
          this.loginUsername = auth.username || ''
        } else {
          this.manualToken = auth.token
        }
        this.checkAuthStatus()
      } else {
        this.savedAuthInfo = { token: '', mode: 'manual', updatedAt: null }
        this.authStatus = 'none'
      }
    },

    getAuthBaseUrl() {
      return ApiSettingsManager.getBaseUrl()
    },

    requestAuthVerify(token) {
      const baseUrl = this.getAuthBaseUrl()
      return new Promise((resolve, reject) => {
        uni.request({
          url: `${baseUrl}/api/auth/verify`,
          method: 'GET',
          header: {
            'X-Admin-Token': token
          },
          timeout: 5000,
          success: (response) => {
            resolve(response)
          },
          fail: (error) => {
            reject(error)
          }
        })
      })
    },

    async checkAuthStatus() {
      const token = ApiAuthManager.getToken()
      if (!token) {
        this.authStatus = 'none'
        return
      }
      try {
        const response = await this.requestAuthVerify(token)
        this.authStatus = response.statusCode === 200 ? 'valid' : 'invalid'
      } catch (error) {
        this.authStatus = 'invalid'
      }
    },

    saveManualToken() {
      if (!this.manualToken) {
        uni.showToast({ title: '请输入 Token', icon: 'none' })
        return
      }
      this.authBusy = true
      try {
        const success = ApiAuthManager.saveToken(this.manualToken, 'manual')
        if (success) {
          this.savedAuthInfo = ApiAuthManager.getAuth()
          this.authStatus = 'unknown'
          uni.showToast({ title: 'Token 已保存', icon: 'success' })
        } else {
          uni.showToast({ title: '保存失败', icon: 'error' })
        }
      } finally {
        this.authBusy = false
      }
    },

    async saveAndTestManualToken() {
      if (!this.manualToken) {
        uni.showToast({ title: '请输入 Token', icon: 'none' })
        return
      }
      this.authBusy = true
      try {
        ApiAuthManager.saveToken(this.manualToken, 'manual')
        this.savedAuthInfo = ApiAuthManager.getAuth()
        await this.testAuth()
      } finally {
        this.authBusy = false
      }
    },

    loginAndSaveToken() {
      if (!this.loginUsername || !this.loginPassword) {
        uni.showToast({ title: '请输入用户名和密码', icon: 'none' })
        return
      }

      this.authBusy = true
      const baseUrl = this.getAuthBaseUrl()

      uni.request({
        url: `${baseUrl}/api/auth/login`,
        method: 'POST',
        header: {
          'Content-Type': 'application/json'
        },
        data: {
          username: this.loginUsername,
          password: this.loginPassword,
          issue_api_token: true
        },
        timeout: 10000,
        success: (response) => {
          if (response.statusCode === 200 && response.data?.api_token) {
            ApiAuthManager.saveToken(response.data.api_token, 'login', {
              username: this.loginUsername
            })
            this.savedAuthInfo = ApiAuthManager.getAuth()
            this.authStatus = 'valid'
            this.loginPassword = ''
            uni.showToast({ title: '登录成功，Token 已保存', icon: 'success' })
          } else {
            const detail = response.data?.detail || '登录失败'
            uni.showToast({ title: detail, icon: 'none' })
            this.authStatus = 'invalid'
          }
          this.authBusy = false
        },
        fail: () => {
          uni.showToast({ title: '网络请求失败', icon: 'error' })
          this.authBusy = false
        }
      })
    },

    async testAuth() {
      const token = ApiAuthManager.getToken()
      if (!token) {
        uni.showToast({ title: '请先配置 Token', icon: 'none' })
        return
      }

      this.authTesting = true
      try {
        const response = await this.requestAuthVerify(token)
        if (response.statusCode === 200) {
          this.authStatus = 'valid'
          uni.showToast({ title: '鉴权测试通过', icon: 'success' })
        } else {
          this.authStatus = 'invalid'
          const detail = response.data?.detail || '鉴权失败'
          uni.showToast({ title: detail, icon: 'none' })
        }
      } catch (error) {
        this.authStatus = 'invalid'
        uni.showToast({ title: '鉴权测试失败', icon: 'error' })
      } finally {
        this.authTesting = false
      }
    },

    clearAuth() {
      uni.showModal({
        title: '确认清除',
        content: '确定要清除已保存的 API 鉴权信息吗？',
        success: (res) => {
          if (res.confirm) {
            ApiAuthManager.clearAuth()
            this.savedAuthInfo = { token: '', mode: 'manual', updatedAt: null }
            this.manualToken = ''
            this.loginPassword = ''
            this.authStatus = 'none'
            uni.showToast({ title: '已清除鉴权', icon: 'success' })
          }
        }
      })
    },

    async loadPrinterSettings() {
      if (!this.isAppPlus) {
        return
      }
      const settings = PrinterSettingsManager.getPrinterSettings()
      this.printerSettings = { ...settings }
      this.selectedPrinterAddress = settings.deviceAddress || ''
      this.printerConnected = isPrinterConnected()
      await this.refreshPairedDevices(false)
    },

    onPrintEnabledChange(event) {
      const enabled = !!event.detail.value
      this.printerSettings.enabled = enabled
      PrinterSettingsManager.setPrintEnabled(enabled)
      uni.showToast({
        title: enabled ? '已启用自动出单' : '已关闭自动出单',
        icon: 'success'
      })
    },

    selectPrinterDevice(device) {
      this.selectedPrinterAddress = device.address
      this.printerSettings.deviceAddress = device.address
      this.printerSettings.deviceName = device.name || ''
    },

    async refreshPairedDevices(showToast = true) {
      if (!this.isAppPlus || !isPrinterPlatformSupported()) {
        return
      }

      if (this.printerScanning) {
        stopDeviceDiscovery()
      }

      this.printerScanning = true

      try {
        const started = await startDeviceDiscovery((devices, finished) => {
          this.pairedDevices = devices || []
          if (finished) {
            this.printerScanning = false
            if (showToast) {
              uni.showToast({
                title: this.pairedDevices.length > 0 ? '设备搜索完成' : '未发现设备',
                icon: 'none'
              })
            }
          }
        })

        if (!started) {
          this.pairedDevices = await getPairedDevices()
          this.printerScanning = false
          if (showToast) {
            uni.showToast({
              title: this.pairedDevices.length > 0 ? '已读取配对设备' : '未发现设备，请检查蓝牙是否开启',
              icon: 'none'
            })
          }
        }
      } catch (error) {
        this.printerScanning = false
        if (showToast) {
          uni.showToast({
            title: error.message || '搜索失败',
            icon: 'none'
          })
        }
      }
    },

    async connectSelectedPrinter() {
      if (!this.selectedPrinterAddress) {
        uni.showToast({ title: '请先选择打印机', icon: 'none' })
        return
      }

      const selected = this.pairedDevices.find(
        (device) => device.address === this.selectedPrinterAddress
      )

      if (selected && selected.paired === false) {
        uni.showToast({ title: '请先在系统蓝牙中配对该打印机', icon: 'none' })
        return
      }

      this.printerBusy = true
      try {
        await connectPrinter(
          this.selectedPrinterAddress,
          selected?.name || this.printerSettings.deviceName
        )
        this.printerSettings = PrinterSettingsManager.getPrinterSettings()
        this.printerConnected = isPrinterConnected()
        uni.showToast({ title: '打印机连接成功', icon: 'success' })
      } catch (error) {
        this.printerConnected = isPrinterConnected()
        const message = error?.message || String(error) || '连接失败'
        uni.showModal({
          title: '连接打印机失败',
          content: message,
          showCancel: false
        })
      } finally {
        this.printerBusy = false
      }
    },

    async testPrinter() {
      if (!this.printerSettings.enabled) {
        uni.showToast({ title: '请先启用自动出单', icon: 'none' })
        return
      }

      this.printerBusy = true
      try {
        // 若尚未保存过打印机地址，但页面上已选中设备，先补一次连接
        if (!this.printerSettings.deviceAddress && this.selectedPrinterAddress) {
          const selected = this.pairedDevices.find(
            (device) => device.address === this.selectedPrinterAddress
          )
          await connectPrinter(this.selectedPrinterAddress, selected?.name || '')
          this.printerSettings = PrinterSettingsManager.getPrinterSettings()
        }

        await printTestTicket()
        this.printerConnected = isPrinterConnected()
        uni.showToast({ title: '测试页已发送', icon: 'success' })
      } catch (error) {
        this.printerConnected = isPrinterConnected()
        const message = error?.message || String(error) || '测试打印失败'
        uni.showModal({
          title: '测试打印失败',
          content: message,
          showCancel: false
        })
      } finally {
        this.printerBusy = false
      }
    },

    /**
     * 加载系统信息
     */
    loadSystemInfo() {
      try {
        const systemInfo = uni.getSystemInfoSync()
        this.systemInfo = {
          version: '1.0.0',
          platform: systemInfo.platform || 'unknown',
          system: `${systemInfo.system} ${systemInfo.version}`,
          networkType: 'unknown'
        }

        // 获取网络类型
        uni.getNetworkType({
          success: (res) => {
            this.systemInfo.networkType = res.networkType
          }
        })
      } catch (error) {
        console.error('获取系统信息失败:', error)
      }
    },

    /**
     * 检查当前连接状态
     */
    async checkCurrentConnection() {
      try {
        const isConnected = await ApiSettingsManager.testConnection(this.currentBaseUrl)
        this.connectionStatus = isConnected ? 'connected' : 'disconnected'
      } catch (error) {
        this.connectionStatus = 'disconnected'
      }
    },

    /**
     * 测试连接
     */
    async testConnection() {
      if (!this.apiSettings.baseUrl) {
        uni.showToast({
          title: '请输入 API 地址',
          icon: 'error'
        })
        return
      }

      this.testing = true
      
      try {
        const isConnected = await ApiSettingsManager.testConnection(this.apiSettings.baseUrl)
        
        if (isConnected) {
          this.connectionStatus = 'connected'
          uni.showToast({
            title: '连接测试成功',
            icon: 'success'
          })
        } else {
          this.connectionStatus = 'disconnected'
          uni.showToast({
            title: '连接测试失败',
            icon: 'error'
          })
        }
      } catch (error) {
        this.connectionStatus = 'disconnected'
        uni.showToast({
          title: '连接测试失败',
          icon: 'error'
        })
      } finally {
        this.testing = false
      }
    },

    /**
     * 保存设置
     */
    async saveSettings() {
      if (!this.apiSettings.baseUrl) {
        uni.showToast({
          title: '请输入 API 地址',
          icon: 'error'
        })
        return
      }

      this.saving = true

      try {
        const success = ApiSettingsManager.setBaseUrl(this.apiSettings.baseUrl)
        
        if (success) {
          this.currentBaseUrl = this.apiSettings.baseUrl
          
          uni.showToast({
            title: '设置保存成功',
            icon: 'success'
          })

          // 重新检查连接状态
          setTimeout(() => {
            this.checkCurrentConnection()
          }, 1000)
        } else {
          uni.showToast({
            title: '设置保存失败',
            icon: 'error'
          })
        }
      } catch (error) {
        console.error('保存设置失败:', error)
        uni.showToast({
          title: '设置保存失败',
          icon: 'error'
        })
      } finally {
        this.saving = false
      }
    },

    /**
     * 重置为默认设置
     */
    resetToDefault() {
      uni.showModal({
        title: '确认重置',
        content: '确定要重置为默认设置吗？',
        success: (res) => {
          if (res.confirm) {
            const success = ApiSettingsManager.resetToDefault()
            
            if (success) {
              this.loadSettings()
              uni.showToast({
                title: '重置成功',
                icon: 'success'
              })
              
              setTimeout(() => {
                this.checkCurrentConnection()
              }, 1000)
            } else {
              uni.showToast({
                title: '重置失败',
                icon: 'error'
              })
            }
          }
        }
      })
    },

    /**
     * 选择预设配置
     */
    selectPreset(preset) {
      this.apiSettings.baseUrl = preset.url
    },

    /**
     * 格式化时间
     */
    formatTime(timeString) {
      try {
        const date = new Date(timeString)
        return date.toLocaleString('zh-CN')
      } catch (error) {
        return '未知时间'
      }
    }
  }
}
</script>

<style scoped>
.settings-container {
  min-height: 100vh;
  background: #eef1f4;
  padding: 24upx 24upx 48upx;
  padding-top: calc(24upx + env(safe-area-inset-top));
  box-sizing: border-box;
}

.page-header {
  margin-bottom: 20upx;
}

.header-bar {
  display: flex;
  align-items: flex-start;
  gap: 20upx;
}

.back-link {
  flex-shrink: 0;
  font-size: 26upx;
  color: #0b6bcb;
  font-weight: 500;
  padding: 8upx 0;
  line-height: 1.4;
}

.header-titles {
  flex: 1;
  min-width: 0;
}

.header-secondary {
  display: flex;
  gap: 24upx;
  flex-shrink: 0;
  padding-top: 8upx;
}

.nav-link {
  font-size: 26upx;
  color: #5b6573;
  font-weight: 500;
}

.page-title {
  display: block;
  font-size: 40upx;
  font-weight: 700;
  color: #1a2332;
  margin-bottom: 6upx;
}

.page-subtitle {
  display: block;
  font-size: 24upx;
  color: #5b6573;
}

.settings-shell {
  display: flex;
  flex-direction: column;
  gap: 16upx;
  background: #ffffff;
  border: 1upx solid #d5dbe3;
  border-radius: 18upx;
  overflow: hidden;
  box-shadow: 0 2upx 12upx rgba(26, 35, 50, 0.06);
  min-height: calc(100vh - 160upx);
}

.settings-nav {
  display: flex;
  gap: 8upx;
  padding: 16upx;
  background: #f8fafc;
  border-bottom: 1upx solid #d5dbe3;
  overflow-x: auto;
}

.settings-nav-item {
  flex: 1;
  min-width: 160upx;
  padding: 16upx 18upx;
  border-radius: 12upx;
  background: transparent;
}

.settings-nav-item--on {
  background: #e7f1fb;
}

.settings-nav-label {
  display: block;
  font-size: 28upx;
  font-weight: 700;
  color: #1a2332;
}

.settings-nav-item--on .settings-nav-label {
  color: #0b6bcb;
}

.settings-nav-desc {
  display: block;
  margin-top: 4upx;
  font-size: 20upx;
  color: #5b6573;
}

.settings-pane {
  flex: 1;
  padding: 20upx;
}

.settings-section {
  display: flex;
  flex-direction: column;
  gap: 16upx;
}

.settings-card {
  background: #ffffff;
  border-radius: 16upx;
  border: 1upx solid #e5eaf0;
  box-shadow: 0 1upx 4upx rgba(26, 35, 50, 0.04);
  overflow: hidden;
}

.card-header {
  padding: 28upx 28upx 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16upx;
}

.card-title {
  font-size: 30upx;
  font-weight: 600;
  color: #1a1a1a;
}

.connection-status {
  padding: 6upx 14upx;
  border-radius: 20upx;
  background: #f0f0f0;
  flex-shrink: 0;
}

.status-connected {
  background: #e6f7ff;
  color: #1890ff;
}

.status-disconnected {
  background: #fff1f0;
  color: #ff4d4f;
}

.status-unknown {
  background: #f0f0f0;
  color: #666666;
}

.status-text {
  font-size: 22upx;
}

.card-content {
  padding: 24upx 28upx 28upx;
}

.form-item {
  margin-bottom: 28upx;
}

.form-label {
  display: block;
  font-size: 28upx;
  color: #1a1a1a;
  font-weight: 500;
  margin-bottom: 12upx;
}

.form-input {
  width: 100%;
  height: 80upx;
  padding: 0 24upx;
  border: 2upx solid #e5e5e5;
  border-radius: 10upx;
  font-size: 28upx;
  box-sizing: border-box;
  background: #fafafa;
}

.form-input:focus {
  border-color: #1890ff;
  background: #ffffff;
}

.form-hint {
  display: block;
  font-size: 22upx;
  color: #999999;
  margin-top: 8upx;
  line-height: 1.45;
}

.station-chip-list,
.density-mode-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12upx;
  margin-top: 16upx;
}

.station-chip,
.density-mode-chip {
  padding: 14upx 24upx;
  background: #fafafa;
  border-radius: 999upx;
  border: 2upx solid #e9ecef;
}

.station-chip-active,
.density-mode-chip-active {
  background: #e6f7ff;
  border-color: #1890ff;
}

.station-chip-text,
.density-mode-chip-text {
  font-size: 26upx;
  color: #333333;
}

.station-chip-active .station-chip-text,
.density-mode-chip-active .density-mode-chip-text {
  color: #1890ff;
  font-weight: 600;
}

.density-form-item {
  margin-top: 28upx;
  margin-bottom: 0;
}

.auth-mode-tabs {
  display: flex;
  gap: 12upx;
  margin-bottom: 28upx;
}

.auth-mode-tab {
  flex: 1;
  padding: 18upx 20upx;
  background: #fafafa;
  border-radius: 10upx;
  border: 2upx solid #e9ecef;
  text-align: center;
}

.auth-mode-tab-active {
  border-color: #1890ff;
  background: #e6f7ff;
}

.auth-mode-tab-text {
  font-size: 26upx;
  color: #333333;
}

.auth-mode-tab-active .auth-mode-tab-text {
  color: #1890ff;
  font-weight: 600;
}

.auth-action-buttons {
  margin-top: 12upx;
}

.printer-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16upx;
}

.printer-device-list {
  margin-top: 20upx;
}

.printer-device-item {
  border: 2upx solid transparent;
}

.printer-device-selected {
  border-color: #1890ff;
  background: #f0f8ff;
}

.current-settings {
  background: #f7f8fa;
  padding: 20upx;
  border-radius: 10upx;
  margin-bottom: 24upx;
}

.settings-label {
  display: block;
  font-size: 22upx;
  color: #666666;
  margin-bottom: 6upx;
}

.settings-value {
  display: block;
  font-size: 26upx;
  color: #1a1a1a;
  word-break: break-all;
  margin-bottom: 6upx;
}

.settings-time {
  display: block;
  font-size: 22upx;
  color: #999999;
}

.button-group {
  display: flex;
  gap: 12upx;
  flex-wrap: wrap;
}

.btn-test,
.btn-save,
.btn-reset {
  flex: 1 1 180upx;
  min-width: 160upx;
  height: 72upx;
  line-height: 72upx;
  border: none;
  border-radius: 10upx;
  font-size: 26upx;
  padding: 0 20upx;
}

.btn-test {
  background: #1890ff;
  color: #ffffff;
}

.btn-save {
  background: #52c41a;
  color: #ffffff;
}

.btn-reset {
  background: #ff4d4f;
  color: #ffffff;
}

.btn-loading {
  opacity: 0.6;
}

.preset-list {
  display: flex;
  flex-direction: column;
  gap: 12upx;
}

.preset-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16upx;
  padding: 20upx;
  background: #fafafa;
  border-radius: 10upx;
}

.preset-item:active {
  background: #f0f0f0;
}

.preset-info {
  flex: 1;
  min-width: 0;
}

.preset-name {
  display: block;
  font-size: 26upx;
  color: #1a1a1a;
  margin-bottom: 6upx;
}

.preset-url {
  display: block;
  font-size: 22upx;
  color: #666666;
  word-break: break-all;
}

.preset-action {
  font-size: 26upx;
  color: #1890ff;
  flex-shrink: 0;
}

.info-list {
  display: flex;
  flex-direction: column;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24upx;
  padding: 18upx 0;
  border-bottom: 1upx solid #f0f0f0;
}

.info-item:last-child {
  border-bottom: none;
}

.info-label {
  font-size: 26upx;
  color: #666666;
  flex-shrink: 0;
}

.info-value {
  font-size: 26upx;
  color: #1a1a1a;
  text-align: right;
  word-break: break-all;
}

.card-subtitle {
  display: block;
  font-size: 22upx;
  color: #666666;
  margin-top: 4upx;
}

.realtime-hint {
  margin-top: 20upx;
  padding: 18upx;
  background: #f6f8fa;
  border-radius: 10upx;
  border-left: 4upx solid #1890ff;
}

.hint-text {
  display: block;
  font-size: 22upx;
  color: #666666;
  line-height: 1.5;
  margin-bottom: 6upx;
}

.hint-text:last-child {
  margin-bottom: 0;
}

.presets-title {
  display: block;
  font-size: 26upx;
  font-weight: 600;
  color: #1a1a1a;
  margin-bottom: 12upx;
}

/* 窄屏：按钮纵向堆叠，筛选更易点 */
@media screen and (max-width: 599upx) {
  .button-group {
    flex-direction: column;
  }

  .btn-test,
  .btn-save,
  .btn-reset {
    flex: none;
    width: 100%;
    min-width: 0;
  }

  .card-header {
    flex-wrap: wrap;
  }
}

/* Landscape tablet: left nav + single content pane (variant B) */
@media screen and (min-width: 1200px) {
  .settings-container {
    padding: 20px 24px 32px;
  }

  .page-title {
    font-size: 22px;
  }

  .page-subtitle {
    font-size: 13px;
  }

  .settings-shell {
    flex-direction: row;
    min-height: calc(100vh - 96px);
    border-radius: 14px;
  }

  .settings-nav {
    flex-direction: column;
    width: 220px;
    flex-shrink: 0;
    border-bottom: none;
    border-right: 1px solid #d5dbe3;
    overflow-x: visible;
    gap: 6px;
    padding: 16px 12px;
  }

  .settings-nav-item {
    flex: none;
    min-width: 0;
  }

  .settings-pane {
    padding: 22px 24px;
  }

  .settings-section {
    max-width: 820px;
  }

  .card-content {
    padding: 28upx 32upx 32upx;
  }

  .preset-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12upx;
  }
}

@media screen and (max-height: 500upx) and (orientation: landscape) {
  .settings-container {
    padding: 16upx 20upx 32upx;
  }

  .page-header {
    margin-bottom: 12upx;
  }

  .page-title {
    font-size: 32upx;
  }

  .page-subtitle {
    font-size: 20upx;
  }

  .card-header {
    padding: 16upx 20upx 0;
  }

  .card-content {
    padding: 16upx 20upx 20upx;
  }

  .form-item {
    margin-bottom: 16upx;
  }

  .form-input {
    height: 64upx;
    font-size: 24upx;
  }

  .btn-test,
  .btn-save,
  .btn-reset {
    height: 60upx;
    line-height: 60upx;
    font-size: 24upx;
  }
}
</style> 