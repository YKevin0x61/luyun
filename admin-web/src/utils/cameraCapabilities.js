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
