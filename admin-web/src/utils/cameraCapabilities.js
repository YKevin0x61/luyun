const FRONT_LABEL = /front|user|selfie|face|facetime|前置|自拍|正面/i
const WIDE_LABEL = /wide|ultra[ -]?wide|0[.,]5|0[.,]6|广角|超广/i

export function videoInputDevices(devices) {
  return (Array.isArray(devices) ? devices : [])
    .filter((device) => device && device.kind === 'videoinput' && device.deviceId)
    .map((device) => ({
      deviceId: String(device.deviceId),
      label: String(device.label || '').trim(),
    }))
}

export function rearCameraDevices(devices) {
  const videoInputs = videoInputDevices(devices)
  const labeledRear = videoInputs.filter((device) => device.label && !FRONT_LABEL.test(device.label))
  if (labeledRear.length) return labeledRear
  return videoInputs.filter((device) => !FRONT_LABEL.test(device.label))
}

export function wideCameraDevices(devices) {
  return rearCameraDevices(devices).filter((device) => WIDE_LABEL.test(device.label))
}

export function preferredWideCameraDevices(devices) {
  const score = (device) => {
    const label = device.label.toLowerCase()
    if (/ultra|超广/.test(label)) return 2
    if (/0[.,]5|0[.,]6/.test(label)) return 1
    return 0
  }
  return wideCameraDevices(devices).sort((left, right) => score(right) - score(left))
}

export function standardCameraDevices(devices) {
  const wideIds = new Set(wideCameraDevices(devices).map((device) => device.deviceId))
  return rearCameraDevices(devices).filter((device) => !wideIds.has(device.deviceId))
}

export function cameraLensPair(devices, currentDeviceId = '') {
  const current = String(currentDeviceId || '')
  const wide = preferredWideCameraDevices(devices)
  const standard = standardCameraDevices(devices)
  const wideDevice = wide[0] || null
  const standardDevice = (
    standard.find((device) => device.deviceId === current)
    || standard[0]
    || (current && !wide.some((device) => device.deviceId === current)
      ? { deviceId: current, label: '' }
      : null)
  )
  return {
    wideDevice,
    standardDevice,
    canSwitchDevices: Boolean(
      wideDevice
      && standardDevice
      && wideDevice.deviceId !== standardDevice.deviceId
    ),
  }
}

export function supportsNativeCameraCapture() {
  if (typeof navigator === 'undefined' || typeof window === 'undefined') return false
  const userAgent = String(navigator.userAgent || '')
  if (!/Android|iPhone|iPad|iPod|Mobile/i.test(userAgent)) return false
  if (navigator.maxTouchPoints > 0) return true
  return Boolean(window.matchMedia?.('(pointer: coarse)').matches)
}
