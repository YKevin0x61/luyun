<script setup>
import { CheckboxIndicator, CheckboxRoot } from 'reka-ui'

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
    class="recipe-checkbox"
  >
    <CheckboxIndicator class="recipe-checkbox__indicator">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M20 6 9 17l-5-5" />
      </svg>
    </CheckboxIndicator>
  </CheckboxRoot>
</template>
