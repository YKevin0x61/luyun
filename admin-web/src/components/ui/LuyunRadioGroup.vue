<script setup>
import { RadioGroupIndicator, RadioGroupItem, RadioGroupRoot } from 'reka-ui'

const props = defineProps({
  options: {
    type: Array,
    required: true,
    // [{ value: string, label: string }]
  },
  name: { type: String, default: 'luyun-radio-group' },
  disabled: { type: Boolean, default: false },
  orientation: {
    type: String,
    default: 'vertical',
    validator: (v) => ['vertical', 'horizontal'].includes(v),
  },
})

const model = defineModel({ type: String, default: '' })
</script>

<template>
  <RadioGroupRoot
    v-model="model"
    :name="name"
    :disabled="disabled"
    :orientation="orientation"
    class="luyun-radio-group"
    :class="`luyun-radio-group--${orientation}`"
  >
    <label
      v-for="opt in options"
      :key="String(opt.value)"
      class="luyun-radio-row"
    >
      <RadioGroupItem :value="opt.value" class="luyun-radio">
        <RadioGroupIndicator class="luyun-radio__indicator" />
      </RadioGroupItem>
      <span>{{ opt.label }}</span>
    </label>
  </RadioGroupRoot>
</template>
