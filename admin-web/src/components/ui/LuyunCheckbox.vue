<script setup>
import { CheckboxIndicator, CheckboxRoot } from 'reka-ui'
import SvgIcon from '../SvgIcon.vue'

defineProps({
  disabled: { type: Boolean, default: false },
  id: { type: String, default: undefined },
})

const model = defineModel({ type: Boolean, default: false })
</script>

<template>
  <CheckboxRoot
    :id="id"
    v-model="model"
    :disabled="disabled"
    class="luyun-checkbox"
  >
    <CheckboxIndicator class="luyun-checkbox__indicator">
      <SvgIcon name="check" :size="12" />
    </CheckboxIndicator>
  </CheckboxRoot>
</template>
