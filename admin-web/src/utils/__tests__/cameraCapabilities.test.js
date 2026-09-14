import { describe, expect, it } from 'vitest'
import {
  rearCameraDevices,
  videoInputDevices,
  wideCameraDevices,
} from '../cameraCapabilities.js'

describe('camera capabilities', () => {
  const devices = [
    { kind: 'videoinput', deviceId: 'front', label: 'Front Camera' },
    { kind: 'videoinput', deviceId: 'back', label: 'Back Camera' },
    { kind: 'videoinput', deviceId: 'wide', label: 'Back Ultra Wide Camera' },
    { kind: 'audioinput', deviceId: 'mic', label: 'Microphone' },
  ]

  it('keeps video inputs and identifies rear and wide lenses', () => {
    expect(videoInputDevices(devices).map((row) => row.deviceId)).toEqual([
      'front',
      'back',
      'wide',
    ])
    expect(rearCameraDevices(devices).map((row) => row.deviceId)).toEqual(['back', 'wide'])
    expect(wideCameraDevices(devices).map((row) => row.deviceId)).toEqual(['wide'])
  })

  it('keeps unlabeled devices as fallback candidates instead of pretending they are front', () => {
    expect(rearCameraDevices([
      { kind: 'videoinput', deviceId: 'a', label: '' },
      { kind: 'videoinput', deviceId: 'b', label: '前置摄像头' },
    ]).map((row) => row.deviceId)).toEqual(['a'])
  })
})
