<script setup>
import { computed } from 'vue'
import { VueDatePicker } from '@vuepic/vue-datepicker'

const props = defineProps({
  placeholder: { type: String, default: '选择日期' },
  disabled: { type: Boolean, default: false },
  minDate: { type: [String, Date], default: undefined },
  maxDate: { type: [String, Date], default: undefined },
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
</script>

<template>
  <VueDatePicker
    v-model="inner"
    model-type="yyyy-MM-dd"
    format="yyyy-MM-dd"
    :enable-time-picker="false"
    :disabled="disabled"
    :placeholder="placeholder"
    :min-date="minDate"
    :max-date="maxDate"
    dark
    auto-apply
    class="luyun-date-picker"
  />
</template>
