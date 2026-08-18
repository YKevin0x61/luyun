/**
 * Cross-platform KDS alert sound engine.
 *
 * Public API (used by kitchen alert integration):
 *   playNewOrderDing(count, options) — three 嘀s when the engine says play
 *   playOvertimeAlarm(options)       — lower, urgent double-pulse pairs
 *   playCancelAlert(options)         — two hits (退菜/取消)
 *   playDisconnectAlert(options)     — one hit (断连告警)
 *   unlockSound()                    — H5: resume AudioContext inside a user gesture
 *   isSoundUnlocked()                — whether play* will actually emit sound
 *
 * options.tone is 提示音款 (register/timbre). Rhythm stays on the 告警种类.
 * options.volume is 本屏告警音量 (0–1, floor 0.2). Factory medium 0.6:
 * total peak = 0.9 * (volume / 0.6), capped at 1. Each hit splits that
 * budget across a piercing osc and a lower body — no hard clip.
 *
 * H5 and APP both synthesize (no audio files, no system beep, no vibration).
 * H5 is gated by autoplay unlock. APP is always treated as unlocked; the
 * AudioContext is created lazily on first play. A new play* stops any
 * still-ringing pattern so only one 告警种类 is audible at a time.
 */

import {
  DEFAULT_ALERT_VOLUME,
  normalizeAlertTone,
  normalizeAlertVolume
} from './storage.js'

/** @typedef {'ding' | 'overtime' | 'cancel' | 'disconnect'} SoundKind */

// Staccato 叮叮叮 / 嘀嘀嘀 — short square beeps, not a soft musical chirp.
const DING_DURATION_MS = 70
const DING_GAP_MS = 80
const MEDIUM_PEAK_GAIN = 0.9
const BEEP_ATTACK_S = 0.004
const BEEP_RELEASE_S = 0.012
const BODY_RATIO = 0.5
const MIN_BODY_HZ = 350
const PIERCE_MIX = 0.58
const BODY_MIX = 0.42

const OVERTIME_PULSE_MS = 110
const OVERTIME_GAP_MS = 55
const OVERTIME_PULSE_PAIRS = 2

const NEW_ORDER_HIT_COUNT = 3
const CANCEL_HIT_COUNT = 2
const DISCONNECT_HIT_COUNT = 1

/**
 * 款 = register/timbre only. All five stay beep-like (square/saw);
 * pitch band is what separates them in a noisy kitchen.
 */
const TONE_VOICES = Object.freeze({
  清脆: {
    dingHz: 1760,
    overtimeAHz: 1175,
    overtimeBHz: 880,
    wave: 'square'
  },
  穿透: {
    dingHz: 2794,
    overtimeAHz: 1976,
    overtimeBHz: 1480,
    wave: 'square'
  },
  圆润: {
    dingHz: 988,
    overtimeAHz: 740,
    overtimeBHz: 554,
    wave: 'square'
  },
  低沉: {
    dingHz: 494,
    overtimeAHz: 370,
    overtimeBHz: 277,
    wave: 'square'
  },
  厚实: {
    dingHz: 659,
    overtimeAHz: 494,
    overtimeBHz: 370,
    wave: 'sawtooth'
  }
})

/** @type {AudioContext | null} */
let audioContext = null
/** Session unlock flag (H5). APP-PLUS is always treated as unlocked. */
let soundUnlocked = false
/** @type {Array<{ osc: OscillatorNode, gain: GainNode }>} */
let activeVoices = []

function stopActiveVoices() {
  for (const voice of activeVoices) {
    try {
      voice.osc.stop()
    } catch (_) {
      // already stopped
    }
    try {
      voice.osc.disconnect()
      voice.gain.disconnect()
    } catch (_) {
      // already disconnected
    }
  }
  activeVoices = []
}

function isAppPlus() {
  return typeof plus !== 'undefined'
}

function getAudioContextConstructor() {
  if (typeof window === 'undefined') return null
  return window.AudioContext || window.webkitAudioContext || null
}

function ensureContextRunning() {
  if (!audioContext) {
    const Ctor = getAudioContextConstructor()
    if (!Ctor) return null
    try {
      audioContext = new Ctor()
    } catch (_) {
      return null
    }
  }
  if (audioContext.state === 'suspended') {
    // Best-effort resume; browsers may still require a later gesture
    audioContext.resume().catch(() => {})
  }
  return audioContext
}

function resolvePlayOptions(options) {
  const raw = options && typeof options === 'object' ? options : {}
  return {
    tone: normalizeAlertTone(raw.tone),
    volume: normalizeAlertVolume(raw.volume)
  }
}

function voiceForTone(tone) {
  return TONE_VOICES[tone] || TONE_VOICES.清脆
}

function peakGainForVolume(volume) {
  return Math.min(1, MEDIUM_PEAK_GAIN * (volume / DEFAULT_ALERT_VOLUME))
}

/**
 * @param {SoundKind} kind
 * @param {number} [count=1]
 * @param {{ tone: string, volume: number }} playOptions
 */
function produceSound(kind, count, playOptions) {
  const ctx = ensureContextRunning()
  if (!ctx) return
  stopActiveVoices()

  const voice = voiceForTone(playOptions.tone)
  const peakGain = peakGainForVolume(playOptions.volume)

  if (kind === 'ding') {
    scheduleHits(ctx, NEW_ORDER_HIT_COUNT, voice, peakGain)
    return
  }
  if (kind === 'cancel') {
    scheduleHits(ctx, CANCEL_HIT_COUNT, voice, peakGain)
    return
  }
  if (kind === 'disconnect') {
    scheduleHits(ctx, DISCONNECT_HIT_COUNT, voice, peakGain)
    return
  }

  let offsetMs = 0
  for (let pair = 0; pair < OVERTIME_PULSE_PAIRS; pair += 1) {
    scheduleTone(ctx, voice.overtimeAHz, offsetMs, OVERTIME_PULSE_MS, voice.wave, peakGain)
    offsetMs += OVERTIME_PULSE_MS + OVERTIME_GAP_MS
    scheduleTone(ctx, voice.overtimeBHz, offsetMs, OVERTIME_PULSE_MS, voice.wave, peakGain)
    offsetMs += OVERTIME_PULSE_MS + OVERTIME_GAP_MS * 2
  }
}

/**
 * @param {AudioContext} ctx
 * @param {number} count
 * @param {{ dingHz: number, wave: OscillatorType }} voice
 * @param {number} peakGain
 */
function scheduleHits(ctx, count, voice, peakGain) {
  for (let i = 0; i < count; i += 1) {
    scheduleTone(
      ctx,
      voice.dingHz,
      (DING_DURATION_MS + DING_GAP_MS) * i,
      DING_DURATION_MS,
      voice.wave,
      peakGain
    )
  }
}

/**
 * @param {AudioContext} ctx
 * @param {number} freqHz
 * @param {number} offsetMs
 * @param {number} durationMs
 * @param {OscillatorType} wave
 * @param {number} peakGain
 */
function startBeepOsc(ctx, freqHz, wave, peakGain, start, end) {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = wave
  osc.frequency.value = freqHz
  const attackEnd = start + BEEP_ATTACK_S
  const releaseStart = Math.max(attackEnd, end - BEEP_RELEASE_S)
  gain.gain.setValueAtTime(0.0001, start)
  gain.gain.linearRampToValueAtTime(peakGain, attackEnd)
  gain.gain.setValueAtTime(peakGain, releaseStart)
  gain.gain.linearRampToValueAtTime(0.0001, end)
  osc.connect(gain)
  gain.connect(ctx.destination)
  osc.start(start)
  osc.stop(end + 0.01)
  activeVoices.push({ osc, gain })
}

function scheduleTone(ctx, freqHz, offsetMs, durationMs, wave, peakGain) {
  const start = ctx.currentTime + offsetMs / 1000
  const end = start + durationMs / 1000
  const bodyHz = Math.max(MIN_BODY_HZ, freqHz * BODY_RATIO)
  startBeepOsc(ctx, freqHz, wave, peakGain * PIERCE_MIX, start, end)
  startBeepOsc(ctx, bodyHz, wave, peakGain * BODY_MIX, start, end)
}

/**
 * Play new-order 嘀嘀嘀 (three hits). `count` only gates whether to play.
 * No-op on H5 until unlockSound() has succeeded.
 * @param {number} count
 * @param {{ tone?: string, volume?: number }} [options]
 */
export function playNewOrderDing(count, options) {
  if (!isSoundUnlocked()) return
  const n = Math.max(0, Math.floor(Number(count) || 0))
  if (n <= 0) return
  produceSound('ding', n, resolvePlayOptions(options))
}

/**
 * Play the overtime alarm (timbre/rhythm distinct from ding).
 * No-op on H5 until unlockSound() has succeeded.
 * @param {{ tone?: string, volume?: number }} [options]
 */
export function playOvertimeAlarm(options) {
  if (!isSoundUnlocked()) return
  produceSound('overtime', 1, resolvePlayOptions(options))
}

/**
 * Play 退菜/取消: two hits in the selected 款. No-op on H5 until unlockSound() has succeeded.
 * @param {{ tone?: string, volume?: number }} [options]
 */
export function playCancelAlert(options) {
  if (!isSoundUnlocked()) return
  produceSound('cancel', 1, resolvePlayOptions(options))
}

/**
 * Play 断连告警: one hit in the selected 款. No-op on H5 until unlockSound() has succeeded.
 * @param {{ tone?: string, volume?: number }} [options]
 */
export function playDisconnectAlert(options) {
  if (!isSoundUnlocked()) return
  produceSound('disconnect', 1, resolvePlayOptions(options))
}

/**
 * Unlock audio. Must be called from a user gesture on H5 (autoplay policy).
 * On APP-PLUS this is a no-op success (always unlocked).
 * @returns {Promise<boolean>} whether sound is unlocked afterwards
 */
export async function unlockSound() {
  if (isAppPlus()) {
    soundUnlocked = true
    return true
  }

  const Ctor = getAudioContextConstructor()
  if (!Ctor) return false

  try {
    if (!audioContext) {
      audioContext = new Ctor()
    }
    if (audioContext.state === 'suspended') {
      await audioContext.resume()
    }
    // Warm the graph inside the gesture so later scheduled tones are allowed
    const silent = audioContext.createBuffer(1, 1, audioContext.sampleRate)
    const src = audioContext.createBufferSource()
    src.buffer = silent
    src.connect(audioContext.destination)
    src.start(0)
    soundUnlocked = true
    return true
  } catch (_) {
    soundUnlocked = false
    return false
  }
}

/**
 * @returns {boolean}
 */
export function isSoundUnlocked() {
  if (isAppPlus()) return true
  return soundUnlocked
}
