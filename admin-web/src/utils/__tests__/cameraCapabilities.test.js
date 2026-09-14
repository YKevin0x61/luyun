import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  cameraLensPair,
  rearCameraDevices,
  preferredWideCameraDevices,
  standardCameraDevices,
  supportsNativeCameraCapture,
  videoInputDevices,
  wideCameraDevices,
} from '../cameraCapabilities.js'

afterEach(() => {
  vi.unstubAllGlobals()
})

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
    expect(standardCameraDevices(devices).map((row) => row.deviceId)).toEqual(['back'])
    expect(cameraLensPair(devices, 'back')).toEqual({
      wideDevice: { deviceId: 'wide', label: 'Back Ultra Wide Camera' },
      standardDevice: { deviceId: 'back', label: 'Back Camera' },
      canSwitchDevices: true,
    })
  })

  it('keeps unlabeled devices as fallback candidates instead of pretending they are front', () => {
    expect(rearCameraDevices([
      { kind: 'videoinput', deviceId: 'a', label: '' },
      { kind: 'videoinput', deviceId: 'b', label: '前置摄像头' },
    ]).map((row) => row.deviceId)).toEqual(['a'])
  })

  it('prefers ultra wide labels for the two-state wide target', () => {
    const wideDevices = [
      { kind: 'videoinput', deviceId: 'wide', label: 'Back Wide Camera' },
      { kind: 'videoinput', deviceId: 'ultra', label: 'Back Ultra Wide Camera' },
      { kind: 'videoinput', deviceId: 'front', label: 'Front Camera' },
    ]
    expect(preferredWideCameraDevices(wideDevices).map((row) => row.deviceId)).toEqual([
      'ultra',
      'wide',
    ])
  })

  it('detects native camera fallback on touch devices', () => {
    vi.stubGlobal('window', { matchMedia: () => ({ matches: true }) })
    vi.stubGlobal('navigator', { maxTouchPoints: 0, userAgent: 'Mobile Safari iPhone' })
    expect(supportsNativeCameraCapture()).toBe(true)
  })
})
