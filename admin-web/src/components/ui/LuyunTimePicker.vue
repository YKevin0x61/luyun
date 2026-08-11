<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  placeholder: { type: String, default: '选择时间' },
  disabled: { type: Boolean, default: false },
  id: { type: String, default: undefined },
  minuteStep: { type: Number, default: 1 },
})

const model = defineModel({ type: String, default: '' })

const rootRef = ref(null)
const hourColRef = ref(null)
const minuteColRef = ref(null)
const open = ref(false)
const suppressScrollWrite = ref(false)

const ITEM_HEIGHT = 36

const hours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'))

const minutes = computed(() => {
  const step = Math.max(1, props.minuteStep)
  const list = []
  for (let m = 0; m < 60; m += step) {
    list.push(String(m).padStart(2, '0'))
  }
  return list
})

function parseParts(value) {
  const [hRaw, mRaw] = String(value || '00:00').split(':')
  const h = Math.min(23, Math.max(0, parseInt(hRaw, 10) || 0))
  let m = Math.min(59, Math.max(0, parseInt(mRaw, 10) || 0))
  const step = Math.max(1, props.minuteStep)
  m = Math.round(m / step) * step
  if (m >= 60) m = 60 - step
  return {
    hour: String(h).padStart(2, '0'),
    minute: String(m).padStart(2, '0'),
  }
}

const parts = computed(() => parseParts(model.value))

const displayValue = computed(() => {
  if (!model.value) return ''
  const { hour, minute } = parts.value
  return `${hour}:${minute}`
})

function writeTime(hour, minute) {
  model.value = `${hour}:${minute}`
}

function readCenterIndex(colEl, maxIndex) {
  if (!colEl) return 0
  const idx = Math.round(colEl.scrollTop / ITEM_HEIGHT)
  return Math.min(Math.max(idx, 0), maxIndex)
}

function scrollToValue(colEl, value, items) {
  if (!colEl) return
  const idx = items.indexOf(value)
  if (idx < 0) return
  colEl.scrollTop = idx * ITEM_HEIGHT
}

async function syncScrollPosition() {
  suppressScrollWrite.value = true
  await nextTick()
  const { hour, minute } = parts.value
  scrollToValue(hourColRef.value, hour, hours)
  scrollToValue(minuteColRef.value, minute, minutes.value)
  requestAnimationFrame(() => {
    suppressScrollWrite.value = false
  })
}

function toggleOpen() {
  if (props.disabled) return
  open.value = !open.value
}

function close() {
  open.value = false
}

function onHourScroll() {
  if (suppressScrollWrite.value) return
  const idx = readCenterIndex(hourColRef.value, hours.length - 1)
  writeTime(hours[idx], parts.value.minute)
}

function onMinuteScroll() {
  if (suppressScrollWrite.value) return
  const idx = readCenterIndex(minuteColRef.value, minutes.value.length - 1)
  writeTime(parts.value.hour, minutes.value[idx])
}

function pickHour(hour) {
  writeTime(hour, parts.value.minute)
  suppressScrollWrite.value = true
  scrollToValue(hourColRef.value, hour, hours)
  requestAnimationFrame(() => {
    suppressScrollWrite.value = false
    close()
  })
}

function pickMinute(minute) {
  writeTime(parts.value.hour, minute)
  suppressScrollWrite.value = true
  scrollToValue(minuteColRef.value, minute, minutes.value)
  requestAnimationFrame(() => {
    suppressScrollWrite.value = false
    close()
  })
}

function onDocumentPointerDown(event) {
  if (!open.value) return
  if (rootRef.value?.contains(event.target)) return
  close()
}

function onDocumentKeyDown(event) {
  if (!open.value) return
  if (event.key === 'Escape') close()
}

watch(open, (isOpen) => {
  if (isOpen) {
    syncScrollPosition()
    document.addEventListener('pointerdown', onDocumentPointerDown, true)
    document.addEventListener('keydown', onDocumentKeyDown)
  } else {
    document.removeEventListener('pointerdown', onDocumentPointerDown, true)
    document.removeEventListener('keydown', onDocumentKeyDown)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown, true)
  document.removeEventListener('keydown', onDocumentKeyDown)
})
</script>

<template>
  <div
    ref="rootRef"
    class="luyun-time-picker"
    :class="{ 'is-open': open, 'is-disabled': disabled }"
  >
    <button
      :id="id"
      type="button"
      class="input luyun-time-picker__trigger"
      :disabled="disabled"
      :aria-expanded="open ? 'true' : 'false'"
      aria-haspopup="listbox"
      @click="toggleOpen"
    >
      <span :class="{ 'is-placeholder': !displayValue }">{{ displayValue || placeholder }}</span>
      <svg class="luyun-time-picker__chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>

    <div v-if="open" class="luyun-time-picker__popover" role="listbox" :aria-label="placeholder">
      <div class="luyun-time-picker__wheel">
        <ul
          ref="hourColRef"
          class="luyun-time-picker__column"
          aria-label="小时"
          @scroll="onHourScroll"
        >
          <li
            v-for="h in hours"
            :key="h"
            class="luyun-time-picker__item"
            :class="{ 'is-active': h === parts.hour }"
            @click="pickHour(h)"
          >{{ h }}</li>
        </ul>
        <ul
          ref="minuteColRef"
          class="luyun-time-picker__column"
          aria-label="分钟"
          @scroll="onMinuteScroll"
        >
          <li
            v-for="m in minutes"
            :key="m"
            class="luyun-time-picker__item"
            :class="{ 'is-active': m === parts.minute }"
            @click="pickMinute(m)"
          >{{ m }}</li>
        </ul>
      </div>
    </div>
  </div>
</template>
