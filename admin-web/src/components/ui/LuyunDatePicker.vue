<script setup>
import { computed } from 'vue'
import { VueDatePicker } from '@vuepic/vue-datepicker'

const props = defineProps({
  placeholder: { type: String, default: '选择日期' },
  disabled: { type: Boolean, default: false },
  dark: { type: Boolean, default: true },
  minDate: { type: [String, Date], default: undefined },
  maxDate: { type: [String, Date], default: undefined },
  ariaLabel: { type: String, default: '选择日期' },
})

const model = defineModel({ type: String, default: '' })

const inner = computed({
  get() {
    return model.value || null
  },
  set(v) {
    model.value = v ?? ''
  },
})

// @vuepic/vue-datepicker v14: display format lives in `formats`, and the time
// picker is disabled through `timeConfig` — `format` / `enable-time-picker`
// only exist on the older v8-style API.
const formats = { input: 'yyyy-MM-dd' }
const timeConfig = { enableTimePicker: false }

const ariaLabels = computed(() => ({
  input: props.ariaLabel || props.placeholder || '选择日期',
  clearInput: `清除${props.ariaLabel || '日期'}`,
  toggleOverlay: `打开${props.ariaLabel || '日期选择器'}`,
  calendarIcon: `打开${props.ariaLabel || '日期选择器'}`,
  menu: props.ariaLabel || '日期选择器',
}))
</script>

<template>
  <VueDatePicker
    v-model="inner"
    model-type="yyyy-MM-dd"
    :formats="formats"
    :time-config="timeConfig"
    :disabled="disabled"
    :placeholder="placeholder"
    :min-date="minDate"
    :max-date="maxDate"
    :dark="dark"
    :aria-labels="ariaLabels"
    teleport="body"
    auto-apply
    class="luyun-date-picker"
  />
</template>
