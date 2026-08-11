<script setup>
import { computed } from 'vue'

const props = defineProps({
  min: { type: Number, default: undefined },
  max: { type: Number, default: undefined },
  step: { type: Number, default: 1 },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
  id: { type: String, default: undefined },
  compact: { type: Boolean, default: false },
  decimal: { type: Boolean, default: false },
})

const emit = defineEmits(['enter'])

const model = defineModel({ type: [Number, String], default: '' })

function parseValue(raw) {
  if (raw === '' || raw === null || raw === undefined) return null
  const n = props.decimal ? parseFloat(String(raw)) : parseInt(String(raw), 10)
  return Number.isFinite(n) ? n : null
}

function clamp(n) {
  let v = n
  if (props.min !== undefined) v = Math.max(props.min, v)
  if (props.max !== undefined) v = Math.min(props.max, v)
  return v
}

function writeValue(n) {
  if (n === null) {
    model.value = ''
    return
  }
  model.value = clamp(n)
}

const current = computed(() => parseValue(model.value))

const atMin = computed(() => {
  if (props.min === undefined || current.value === null) return false
  return current.value <= props.min
})

const atMax = computed(() => {
  if (props.max === undefined || current.value === null) return false
  return current.value >= props.max
})

function stepBy(delta) {
  if (props.disabled || props.readonly) return
  const base = current.value ?? (props.min ?? 0)
  writeValue(base + delta * props.step)
}

function onInput(event) {
  const raw = event.target.value
  if (raw === '' || raw === '-') {
    model.value = raw
    return
  }
  const n = parseValue(raw)
  if (n === null) return
  model.value = n
}

function onBlur() {
  if (model.value === '' || model.value === '-') {
    model.value = ''
    return
  }
  const n = parseValue(model.value)
  if (n === null) {
    model.value = ''
    return
  }
  writeValue(n)
}
</script>

<template>
  <div class="luyun-number" :class="{ 'luyun-number--compact': compact }">
    <button
      type="button"
      class="luyun-number__step"
      :disabled="disabled || readonly || atMin"
      aria-label="减少"
      @click="stepBy(-1)"
    >−</button>
    <input
      :id="id"
      class="luyun-number__input input"
      type="text"
      :inputmode="decimal ? 'decimal' : 'numeric'"
      :value="model"
      :placeholder="placeholder"
      :disabled="disabled"
      :readonly="readonly"
      @input="onInput"
      @blur="onBlur"
      @keydown.up.prevent="stepBy(1)"
      @keydown.down.prevent="stepBy(-1)"
      @keyup.enter="emit('enter')"
    />
    <button
      type="button"
      class="luyun-number__step"
      :disabled="disabled || readonly || atMax"
      aria-label="增加"
      @click="stepBy(1)"
    >+</button>
  </div>
</template>
