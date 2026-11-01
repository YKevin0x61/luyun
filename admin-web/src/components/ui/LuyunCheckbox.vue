<script setup>
import { computed } from 'vue'
import { CheckboxIndicator, CheckboxRoot } from 'reka-ui'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  id: { type: String, default: undefined },
  // 半选（票 08）：群组行「组里只有一部分渠道被单独订了」时用它。reka 的 CheckboxRoot
  // 认 `'indeterminate'` 这个值，点一下从半选变成全选（true），所以这里只代理它、
  // 不另造一套状态。
  indeterminate: { type: Boolean, default: false },
})

const model = defineModel({ type: Boolean, default: false })

const rendered = computed({
  get: () => (props.indeterminate ? 'indeterminate' : model.value === true),
  set: (value) => { model.value = value === true },
})
</script>

<template>
  <CheckboxRoot
    :id="id"
    v-model="rendered"
    :disabled="disabled"
    class="luyun-checkbox"
  >
    <CheckboxIndicator class="luyun-checkbox__indicator">
      <SvgIcon name="check" :size="12" />
    </CheckboxIndicator>
  </CheckboxRoot>
</template>
