/**
 * Cross-platform KDS alert sound engine.
 *
 * Public API (used by kitchen alert integration):
 *   playNewOrderDing(count) — short high chirps, one per new order (caller caps)
 *   playOvertimeAlarm()     — lower, urgent pattern distinct from ding
 *   unlockSound()           — H5: resume AudioContext inside a user gesture
 *   isSoundUnlocked()       — whether play* will actually emit sound
 *
 * Platform strategy:
 *   H5       — Web Audio synthesis (no binary assets); gated by autoplay unlock
 *   APP-PLUS — plus.device.beep + uni.vibrate* (same pattern as disconnect alerts)
 *
 * Sound production is concentrated in `produceSound` so a future swap to
 * real audio files only needs to replace that one function.
 */

/** @typedef {'ding' | 'overtime'} SoundKind */

const DING_FREQ_HZ = 1046.5 // C6 — bright short chirp
const DING_DURATION_MS = 70
const DING_GAP_MS = 110

// Overtime: lower, longer, double-pulse — clearly not a ding by ear
const OVERTIME_FREQ_A_HZ = 392 // G4
const OVERTIME_FREQ_B_HZ = 277.2 // C#4
const OVERTIME_PULSE_MS = 160
const OVERTIME_GAP_MS = 80
const OVERTIME_PULSE_PAIRS = 2

const APP_OVERTIME_BEEP_COUNT = 3

/** @type {AudioContext | null} */
let audioContext = null
/** Session unlock flag (H5). APP-PLUS is always treated as unlocked. */
let soundUnlocked = false

function isAppPlus() {
  return typeof plus !== 'undefined' && plus.device && typeof plus.device.beep === 'function'
}

function getAudioContextConstructor() {
  if (typeof window === 'undefined') return null
  return window.AudioContext || window.webkitAudioContext || null
}

/**
 * Swap point for how sound is actually produced.
 * Replace this body later to play real audio files instead of synthesis/beep.
 * @param {SoundKind} kind
 * @param {number} [count=1]
 */
function produceSound(kind, count = 1) {
  if (isAppPlus()) {
    produceAppPlusSound(kind, count)
    return
  }
  produceH5Sound(kind, count)
}

function produceAppPlusSound(kind, count) {
  try {
    if (kind === 'ding') {
      plus.device.beep(Math.max(1, count))
      if (typeof uni !== 'undefined' && typeof uni.vibrateShort === 'function') {
        uni.vibrateShort()
      }
      return
    }
    // overtime — more beeps + long vibrate so it reads differently from a ding
    plus.device.beep(APP_OVERTIME_BEEP_COUNT)
    if (typeof uni !== 'undefined' && typeof uni.vibrateLong === 'function') {
      uni.vibrateLong()
    }
  } catch (_) {
    // Device audio/vibrate failures must not break kitchen flow
  }
}

function ensureH5ContextRunning() {
  const ctx = audioContext
  if (!ctx) return null
  if (ctx.state === 'suspended') {
    // Best-effort resume; browsers may still require a later gesture
    ctx.resume().catch(() => {})
  }
  return ctx
}

function produceH5Sound(kind, count) {
  const ctx = ensureH5ContextRunning()
  if (!ctx) return

  if (kind === 'ding') {
    const n = Math.max(1, count)
    for (let i = 0; i < n; i += 1) {
      scheduleTone(ctx, DING_FREQ_HZ, (DING_DURATION_MS + DING_GAP_MS) * i, DING_DURATION_MS)
    }
    return
  }

  // overtime: alternating low double-pulses
  let offsetMs = 0
  for (let pair = 0; pair < OVERTIME_PULSE_PAIRS; pair += 1) {
    scheduleTone(ctx, OVERTIME_FREQ_A_HZ, offsetMs, OVERTIME_PULSE_MS)
    offsetMs += OVERTIME_PULSE_MS + OVERTIME_GAP_MS
    scheduleTone(ctx, OVERTIME_FREQ_B_HZ, offsetMs, OVERTIME_PULSE_MS)
    offsetMs += OVERTIME_PULSE_MS + OVERTIME_GAP_MS * 2
  }
}

/**
 * @param {AudioContext} ctx
 * @param {number} freqHz
 * @param {number} offsetMs
 * @param {number} durationMs
 */
function scheduleTone(ctx, freqHz, offsetMs, durationMs) {
  const start = ctx.currentTime + offsetMs / 1000
  const end = start + durationMs / 1000
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = 'sine'
  osc.frequency.value = freqHz
  // Soft attack/release to avoid clicks
  gain.gain.setValueAtTime(0.0001, start)
  gain.gain.exponentialRampToValueAtTime(0.25, start + 0.01)
  gain.gain.exponentialRampToValueAtTime(0.0001, end)
  osc.connect(gain)
  gain.connect(ctx.destination)
  osc.start(start)
  osc.stop(end + 0.02)
}

/**
 * Play new-order dings. No-op on H5 until unlockSound() has succeeded.
 * @param {number} count
 */
export function playNewOrderDing(count) {
  if (!isSoundUnlocked()) return
  const n = Math.max(0, Math.floor(Number(count) || 0))
  if (n <= 0) return
  produceSound('ding', n)
}

/**
 * Play the overtime alarm (timbre/rhythm distinct from ding).
 * No-op on H5 until unlockSound() has succeeded.
 */
export function playOvertimeAlarm() {
  if (!isSoundUnlocked()) return
  produceSound('overtime', 1)
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
