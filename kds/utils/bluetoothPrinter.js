/**
 * 蓝牙打印机封装（自研 UTS 插件 kds-bluetooth-printer + Android 扫描）
 */

import { PrinterSettingsManager } from './storage.js'
import { canWarmupPrinter, createConnectGate } from './printerConnectGate.js'

// #ifdef APP-PLUS
import {
  getPairedDevices as utsGetPairedDevices,
  stopDiscovery as utsStopDiscovery,
  connect as utsConnect,
  disconnect as utsDisconnect,
  isConnected as utsIsConnected,
  printText as utsPrintText,
  printNewLine as utsPrintNewLine,
  cutPaper as utsCutPaper
} from '@/uni_modules/kds-bluetooth-printer'
// #endif

const BOND_BONDED = 12
const DISCOVERY_TIMEOUT_MS = 12000

export const PrintAlign = {
  LEFT: 0,
  CENTER: 1,
  RIGHT: 2
}

export const FontSize = {
  NORMAL: 0,
  LARGE: 1,
  EXTRA_LARGE: 2
}

let androidDiscoveryReceiver = null
let androidDiscoveryFilter = null
let androidDiscoveryTimeout = null
let androidDiscoveredMap = {}

// Android 原生蓝牙连接状态（绕过 UTS 桥接，避免 printText 参数序列化失败）
let androidPrinterSocket = null
let androidPrinterOutputStream = null
let androidConnectedAddress = ''

function getPrinterApi() {
  // #ifdef APP-PLUS
  return {
    getPairedDevices: utsGetPairedDevices,
    stopDiscovery: utsStopDiscovery,
    connect: utsConnect,
    disconnect: utsDisconnect,
    isConnected: utsIsConnected,
    printText: utsPrintText,
    printNewLine: utsPrintNewLine,
    cutPaper: utsCutPaper
  }
  // #endif
  return null
}

function isAppPlusAndroid() {
  // #ifdef APP-PLUS
  return typeof plus !== 'undefined' && !!plus.android
  // #endif
  return false
}

function importAndroidClass(className) {
  return plus.android.importClass(className)
}

function findBondedDeviceAndroid(address) {
  if (!isAppPlusAndroid() || !address) {
    return null
  }

  try {
    const BluetoothAdapter = importAndroidClass('android.bluetooth.BluetoothAdapter')
    const adapter = BluetoothAdapter.getDefaultAdapter()
    if (!adapter) {
      return null
    }

    const bonded = adapter.getBondedDevices()
    if (!bonded) {
      return null
    }

    const iterator = bonded.iterator()
    while (iterator.hasNext()) {
      const device = iterator.next()
      plus.android.importClass(device)
      if (device.getAddress() === address) {
        return device
      }
    }
  } catch (error) {
    console.error('[蓝牙打印] 查找已配对设备失败:', error)
  }

  return null
}

function openSocketAndroid(device) {
  const BluetoothAdapter = importAndroidClass('android.bluetooth.BluetoothAdapter')
  const UUID = importAndroidClass('java.util.UUID')
  const SPP_UUID = UUID.fromString('00001101-0000-1000-8000-00805F9B34FB')

  const adapter = BluetoothAdapter.getDefaultAdapter()
  if (adapter && adapter.isDiscovering()) {
    adapter.cancelDiscovery()
  }

  try {
    const socket = device.createRfcommSocketToServiceRecord(SPP_UUID)
    plus.android.importClass(socket)
    socket.connect()
    return socket
  } catch (primaryError) {
    try {
      const Integer = importAndroidClass('java.lang.Integer')
      const method = device.getClass().getMethod('createRfcommSocket', Integer.TYPE)
      const fallbackSocket = method.invoke(device, Integer.valueOf(1))
      plus.android.importClass(fallbackSocket)
      fallbackSocket.connect()
      return fallbackSocket
    } catch (fallbackError) {
      throw new Error('连接失败，请确认打印机已开机、在有效范围内')
    }
  }
}

/**
 * 确保设备已配对（未配对则发起系统配对，等待用户在系统弹窗中确认/输入 PIN）
 */
function ensureDeviceBondedAndroid(address) {
  return new Promise((resolve, reject) => {
    try {
      const BluetoothAdapter = importAndroidClass('android.bluetooth.BluetoothAdapter')
      const BluetoothDevice = importAndroidClass('android.bluetooth.BluetoothDevice')
      const IntentFilter = importAndroidClass('android.content.IntentFilter')
      const adapter = BluetoothAdapter.getDefaultAdapter()
      if (!adapter) {
        reject(new Error('设备不支持蓝牙'))
        return
      }

      const device = adapter.getRemoteDevice(address)
      plus.android.importClass(device)

      if (device.getBondState() === BOND_BONDED) {
        resolve(device)
        return
      }

      const main = plus.android.runtimeMainActivity()
      const filter = new IntentFilter()
      filter.addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED)

      let settled = false
      let receiver = null
      let timeoutId = null

      const cleanup = () => {
        if (timeoutId) {
          clearTimeout(timeoutId)
          timeoutId = null
        }
        if (receiver) {
          try {
            main.unregisterReceiver(receiver)
          } catch (error) {
            // ignore
          }
          receiver = null
        }
      }

      receiver = plus.android.implements(
        'io.dcloud.feature.internal.reflect.BroadcastReceiver',
        {
          onReceive(context, intent) {
            if (settled) {
              return
            }
            try {
              plus.android.importClass(intent)
              if (intent.getAction() !== BluetoothDevice.ACTION_BOND_STATE_CHANGED) {
                return
              }
              const changedDevice = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
              if (!changedDevice) {
                return
              }
              plus.android.importClass(changedDevice)
              if (changedDevice.getAddress() !== address) {
                return
              }
              const bondState = intent.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, -1)
              if (bondState === BOND_BONDED) {
                settled = true
                cleanup()
                resolve(device)
              } else if (bondState === 10) {
                settled = true
                cleanup()
                reject(new Error('配对被拒绝或失败，请在系统蓝牙设置中手动配对（默认 PIN: 1234 或 0000）'))
              }
            } catch (error) {
              // ignore
            }
          }
        }
      )

      main.registerReceiver(receiver, filter)

      const started = device.createBond()
      if (!started) {
        cleanup()
        reject(new Error('无法发起配对，请在系统蓝牙设置中手动配对该打印机'))
        return
      }

      timeoutId = setTimeout(() => {
        if (!settled) {
          settled = true
          cleanup()
          reject(new Error('配对超时，请在弹出的系统配对请求中确认（默认 PIN: 1234 或 0000），或在系统蓝牙设置中手动配对'))
        }
      }, 20000)
    } catch (error) {
      reject(new Error(error?.message || String(error) || '配对失败'))
    }
  })
}

function writeBytesAndroid(byteValues) {
  if (!androidPrinterOutputStream) {
    return false
  }

  const ByteArrayOutputStream = importAndroidClass('java.io.ByteArrayOutputStream')
  const baos = new ByteArrayOutputStream()
  for (let i = 0; i < byteValues.length; i++) {
    baos.write(byteValues[i])
  }
  const bytes = baos.toByteArray()
  androidPrinterOutputStream.write(bytes)
  androidPrinterOutputStream.flush()
  return true
}

function writeTextAndroid(text) {
  if (!androidPrinterOutputStream) {
    return false
  }

  const JString = importAndroidClass('java.lang.String')
  const Charset = importAndroidClass('java.nio.charset.Charset')
  const GBK = Charset.forName('GBK')
  const bytes = new JString(String(text)).getBytes(GBK)
  androidPrinterOutputStream.write(bytes)
  androidPrinterOutputStream.flush()
  return true
}

function disconnectUtsPrinterSilently() {
  const api = getPrinterApi()
  if (!api) {
    return
  }
  try {
    api.disconnect()
  } catch (error) {
    // ignore
  }
}

function disconnectPrinterViaAndroid() {
  try {
    if (androidPrinterOutputStream) {
      androidPrinterOutputStream.close()
    }
  } catch (error) {
    // ignore
  }
  try {
    if (androidPrinterSocket) {
      androidPrinterSocket.close()
    }
  } catch (error) {
    // ignore
  }

  androidPrinterOutputStream = null
  androidPrinterSocket = null
  androidConnectedAddress = ''
  return true
}

function isPrinterConnectedViaAndroid() {
  if (!androidPrinterSocket) {
    return false
  }

  try {
    plus.android.importClass(androidPrinterSocket)
    return androidPrinterSocket.isConnected()
  } catch (error) {
    return false
  }
}

async function connectPrinterViaAndroid(address, deviceName = '') {
  try {
    disconnectPrinterViaAndroid()
    disconnectUtsPrinterSilently()

    let device = findBondedDeviceAndroid(address)
    if (!device) {
      device = await ensureDeviceBondedAndroid(address)
    }

    const socket = openSocketAndroid(device)
    androidPrinterSocket = socket
    androidPrinterOutputStream = socket.getOutputStream()
    plus.android.importClass(androidPrinterOutputStream)
    androidConnectedAddress = address
    writeBytesAndroid([0x1B, 0x40])

    PrinterSettingsManager.savePrinterDevice({ address, name: deviceName })
    return true
  } catch (error) {
    disconnectPrinterViaAndroid()
    throw new Error(error?.message || String(error) || '连接打印机失败')
  }
}

function printTextViaAndroid(text, align, fontSize) {
  if (!isPrinterConnectedViaAndroid()) {
    throw new Error('打印机未连接')
  }

  const resolvedAlign = align != null ? align : PrintAlign.LEFT
  const resolvedFontSize = fontSize != null ? fontSize : FontSize.NORMAL

  let alignValue = 0
  if (resolvedAlign === PrintAlign.CENTER) {
    alignValue = 1
  } else if (resolvedAlign === PrintAlign.RIGHT) {
    alignValue = 2
  }

  let sizeValue = 0x00
  if (resolvedFontSize === FontSize.LARGE) {
    sizeValue = 0x11
  } else if (resolvedFontSize === FontSize.EXTRA_LARGE) {
    sizeValue = 0x33
  }

  writeBytesAndroid([0x1B, 0x61, alignValue])
  writeBytesAndroid([0x1D, 0x21, sizeValue])
  writeTextAndroid(text)
  return true
}

function printNewLineViaAndroid(lines = 1) {
  if (!isPrinterConnectedViaAndroid()) {
    throw new Error('打印机未连接')
  }

  const count = Math.max(1, Number(lines) || 1)
  for (let i = 0; i < count; i++) {
    writeBytesAndroid([0x0A])
  }
  return true
}

function cutPaperViaAndroid() {
  if (!isPrinterConnectedViaAndroid()) {
    throw new Error('打印机未连接')
  }

  writeBytesAndroid([0x1D, 0x56, 0x00])
  return true
}

function setBoldViaAndroid(enabled) {
  if (!isPrinterConnectedViaAndroid()) {
    throw new Error('打印机未连接')
  }

  writeBytesAndroid([0x1B, 0x45, enabled ? 1 : 0])
  return true
}

function resetPrintStyleViaAndroid() {
  if (!isPrinterConnectedViaAndroid()) {
    throw new Error('打印机未连接')
  }

  writeBytesAndroid([0x1B, 0x45, 0])
  writeBytesAndroid([0x1D, 0x21, 0x00])
  writeBytesAndroid([0x1B, 0x61, 0])
  return true
}

function useAndroidPrinterBridge() {
  return isAppPlusAndroid()
}

function mergeBluetoothDevices(pairedDevices, discoveredMap) {
  const merged = new Map()
  ;(pairedDevices || []).forEach((device) => {
    if (device && device.address) {
      merged.set(device.address, device)
    }
  })
  Object.values(discoveredMap || {}).forEach((device) => {
    if (device && device.address && !merged.has(device.address)) {
      merged.set(device.address, device)
    }
  })
  return Array.from(merged.values())
}

function getPairedDevicesViaAndroid() {
  if (!isAppPlusAndroid()) {
    return []
  }

  try {
    const BluetoothAdapter = importAndroidClass('android.bluetooth.BluetoothAdapter')
    const adapter = BluetoothAdapter.getDefaultAdapter()
    if (!adapter) {
      return []
    }

    const bonded = adapter.getBondedDevices()
    if (!bonded) {
      return []
    }

    const iterator = bonded.iterator()
    const devices = []
    while (iterator.hasNext()) {
      const device = iterator.next()
      plus.android.importClass(device)
      const address = device.getAddress()
      if (!address) {
        continue
      }
      devices.push({
        name: device.getName() || '未知设备',
        address,
        paired: true
      })
    }
    return devices
  } catch (error) {
    console.error('[蓝牙打印] Android 获取已配对设备失败:', error)
    return []
  }
}

function stopAndroidDeviceDiscovery() {
  if (androidDiscoveryTimeout) {
    clearTimeout(androidDiscoveryTimeout)
    androidDiscoveryTimeout = null
  }

  if (!isAppPlusAndroid()) {
    androidDiscoveredMap = {}
    return false
  }

  try {
    const main = plus.android.runtimeMainActivity()
    const BluetoothAdapter = importAndroidClass('android.bluetooth.BluetoothAdapter')
    const adapter = BluetoothAdapter.getDefaultAdapter()
    if (adapter) {
      adapter.cancelDiscovery()
    }
    if (androidDiscoveryReceiver && main) {
      main.unregisterReceiver(androidDiscoveryReceiver)
    }
  } catch (error) {
    console.error('[蓝牙打印] 停止 Android 扫描失败:', error)
  }

  androidDiscoveryReceiver = null
  androidDiscoveryFilter = null
  androidDiscoveredMap = {}
  return true
}

function startAndroidDeviceDiscovery(onProgress) {
  const main = plus.android.runtimeMainActivity()
  const BluetoothAdapter = importAndroidClass('android.bluetooth.BluetoothAdapter')
  const BluetoothDevice = importAndroidClass('android.bluetooth.BluetoothDevice')
  const IntentFilter = importAndroidClass('android.content.IntentFilter')

  const adapter = BluetoothAdapter.getDefaultAdapter()
  if (!adapter || !adapter.isEnabled()) {
    onProgress(getPairedDevicesViaAndroid(), true)
    return false
  }

  stopAndroidDeviceDiscovery()
  androidDiscoveredMap = {}

  let pairedDevices = getPairedDevicesViaAndroid()

  const notify = (finished) => {
    if (typeof onProgress === 'function') {
      onProgress(mergeBluetoothDevices(pairedDevices, androidDiscoveredMap), finished)
    }
  }

  androidDiscoveryFilter = new IntentFilter()
  androidDiscoveryFilter.addAction(BluetoothDevice.ACTION_FOUND)
  androidDiscoveryFilter.addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)

  androidDiscoveryReceiver = plus.android.implements(
    'io.dcloud.feature.internal.reflect.BroadcastReceiver',
    {
      onReceive(context, intent) {
        try {
          plus.android.importClass(intent)
          const action = intent.getAction()

          if (action === BluetoothDevice.ACTION_FOUND) {
            const device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
            if (!device) {
              return
            }
            plus.android.importClass(device)
            const address = device.getAddress()
            if (!address) {
              return
            }
            androidDiscoveredMap[address] = {
              name: device.getName() || '未知设备',
              address,
              paired: device.getBondState() === BOND_BONDED
            }
            notify(false)
            return
          }

          if (action === BluetoothAdapter.ACTION_DISCOVERY_FINISHED) {
            stopAndroidDeviceDiscovery()
            pairedDevices = getPairedDevicesViaAndroid()
            notify(true)
          }
        } catch (error) {
          console.error('[蓝牙打印] 扫描广播处理失败:', error)
        }
      }
    }
  )

  main.registerReceiver(androidDiscoveryReceiver, androidDiscoveryFilter)
  notify(false)

  if (adapter.isDiscovering()) {
    adapter.cancelDiscovery()
  }

  const started = adapter.startDiscovery()
  if (!started) {
    stopAndroidDeviceDiscovery()
    notify(true)
    return false
  }

  androidDiscoveryTimeout = setTimeout(() => {
    if (!androidDiscoveryReceiver) {
      return
    }
    stopAndroidDeviceDiscovery()
    pairedDevices = getPairedDevicesViaAndroid()
    notify(true)
  }, DISCOVERY_TIMEOUT_MS)

  return true
}

export function isPrinterPlatformSupported() {
  // #ifdef APP-PLUS
  return true
  // #endif
  return false
}

export function isPrinterPluginAvailable() {
  return getPrinterApi() !== null
}

/**
 * 申请蓝牙搜索/连接所需的 Android 运行时权限
 */
export function requestBluetoothPermissions() {
  // #ifdef APP-PLUS
  return new Promise((resolve, reject) => {
    if (typeof plus === 'undefined' || !plus.android) {
      resolve(true)
      return
    }

    const Build = plus.android.importClass('android.os.Build')
    const sdkInt = Build.VERSION.SDK_INT
    const permissions = []

    if (sdkInt >= 31) {
      permissions.push(
        'android.permission.BLUETOOTH_SCAN',
        'android.permission.BLUETOOTH_CONNECT'
      )
    }

    permissions.push(
      'android.permission.ACCESS_FINE_LOCATION',
      'android.permission.ACCESS_COARSE_LOCATION'
    )

    plus.android.requestPermissions(
      permissions,
      (result) => {
        const denied = [
          ...(result.deniedAlways || []),
          ...(result.deniedPresent || [])
        ]
        if (denied.length > 0) {
          reject(new Error('需要蓝牙和位置权限才能搜索打印机，请在系统设置中授权'))
          return
        }
        resolve(true)
      },
      (error) => {
        reject(new Error(error?.message || '权限申请失败'))
      }
    )
  })
  // #endif
  return Promise.resolve(true)
}

export async function getPairedDevices() {
  await requestBluetoothPermissions()

  const api = getPrinterApi()
  if (api) {
    try {
      return api.getPairedDevices() || []
    } catch (error) {
      console.warn('[蓝牙打印] UTS 获取配对设备失败，改用 Android API:', error)
    }
  }

  return getPairedDevicesViaAndroid()
}

export async function startDeviceDiscovery(onProgress) {
  await requestBluetoothPermissions()

  const api = getPrinterApi()
  if (api) {
    try {
      api.stopDiscovery()
    } catch (error) {
      // ignore
    }
  }

  if (!isAppPlusAndroid()) {
    return false
  }

  try {
    return startAndroidDeviceDiscovery(onProgress)
  } catch (error) {
    console.error('[蓝牙打印] 扫描设备失败:', error)
    throw error
  }
}

export function stopDeviceDiscovery() {
  const api = getPrinterApi()
  if (api) {
    try {
      api.stopDiscovery()
    } catch (error) {
      // ignore
    }
  }
  return stopAndroidDeviceDiscovery()
}

export async function connectPrinter(address, deviceName = '') {
  if (!address) {
    throw new Error('未选择打印机')
  }

  await requestBluetoothPermissions()
  stopDeviceDiscovery()

  if (useAndroidPrinterBridge()) {
    return connectPrinterViaAndroid(address, deviceName)
  }

  const api = getPrinterApi()
  if (!api) {
    throw new Error('当前平台不支持蓝牙打印')
  }

  return new Promise((resolve, reject) => {
    api.connect(address, (success, error) => {
      if (success) {
        PrinterSettingsManager.savePrinterDevice({ address, name: deviceName })
        resolve(true)
        return
      }
      reject(new Error(error || '连接打印机失败'))
    })
  })
}

const ensureConnectedGate = createConnectGate()

async function ensurePrinterConnectedOnce() {
  if (useAndroidPrinterBridge()) {
    if (isPrinterConnectedViaAndroid()) {
      return true
    }

    const settings = PrinterSettingsManager.getPrinterSettings()
    if (!settings.deviceAddress) {
      throw new Error('请先在设置中配置打印机')
    }

    return connectPrinterViaAndroid(settings.deviceAddress, settings.deviceName)
  }

  const api = getPrinterApi()
  if (!api) {
    throw new Error('当前平台不支持蓝牙打印')
  }

  if (api.isConnected()) {
    return true
  }

  const settings = PrinterSettingsManager.getPrinterSettings()
  if (!settings.deviceAddress) {
    throw new Error('请先在设置中配置打印机')
  }

  return connectPrinter(settings.deviceAddress, settings.deviceName)
}

export async function ensurePrinterConnected() {
  return ensureConnectedGate.run(ensurePrinterConnectedOnce)
}

/**
 * Open the saved printer in the background so the first ticket after a
 * successful 出餐 does not wait on Bluetooth SPP connect. Errors are swallowed:
 * the print queue still connects (and retries) when a job actually runs.
 */
export function warmupPrinter() {
  const settings = PrinterSettingsManager.getPrinterSettings()
  if (!canWarmupPrinter({
    platformSupported: isPrinterPlatformSupported(),
    printEnabled: PrinterSettingsManager.isPrintEnabled(),
    deviceAddress: settings.deviceAddress
  })) {
    return Promise.resolve()
  }

  return ensurePrinterConnected().catch((error) => {
    console.warn('[蓝牙打印] 预连接失败:', error?.message || error)
  })
}

export function disconnectPrinter() {
  if (useAndroidPrinterBridge()) {
    return disconnectPrinterViaAndroid()
  }

  const api = getPrinterApi()
  if (!api) {
    return false
  }
  try {
    return api.disconnect()
  } catch (error) {
    console.error('[蓝牙打印] 断开连接失败:', error)
    return false
  }
}

export function isPrinterConnected() {
  if (useAndroidPrinterBridge()) {
    return isPrinterConnectedViaAndroid()
  }

  const api = getPrinterApi()
  if (!api) {
    return false
  }
  try {
    return api.isConnected()
  } catch (error) {
    return false
  }
}

export function printText(text, align, fontSize) {
  if (useAndroidPrinterBridge()) {
    return printTextViaAndroid(text, align, fontSize)
  }

  const api = getPrinterApi()
  if (!api) {
    throw new Error('蓝牙打印插件未安装')
  }
  return api.printText(text, align ?? null, fontSize ?? null)
}

export function printNewLine(lines = 1) {
  if (useAndroidPrinterBridge()) {
    return printNewLineViaAndroid(lines)
  }

  const api = getPrinterApi()
  if (!api) {
    throw new Error('蓝牙打印插件未安装')
  }
  return api.printNewLine(lines ?? null)
}

export function cutPaper() {
  if (useAndroidPrinterBridge()) {
    return cutPaperViaAndroid()
  }

  const api = getPrinterApi()
  if (!api) {
    throw new Error('蓝牙打印插件未安装')
  }
  return api.cutPaper()
}

export function setBold(enabled = true) {
  if (useAndroidPrinterBridge()) {
    return setBoldViaAndroid(!!enabled)
  }

  const api = getPrinterApi()
  if (!api || typeof api.setBold !== 'function') {
    return false
  }
  return api.setBold(!!enabled)
}

export function resetPrintStyle() {
  if (useAndroidPrinterBridge()) {
    return resetPrintStyleViaAndroid()
  }

  const api = getPrinterApi()
  if (!api || typeof api.resetPrintStyle !== 'function') {
    return false
  }
  return api.resetPrintStyle()
}

export function getPrintAlign() {
  return PrintAlign
}

export function getFontSize() {
  return FontSize
}
