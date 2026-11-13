<script setup>
/**
 * 多选控件（复选框组）的渲染器：运营日报的「勾选日报内容」用它（ADR 0144）。
 *
 * 后端把这种字段声明成 `type: 'array'` + `x-ui-control: 'checkbox-group'`，选项在
 * `items.oneOf` 里。vanilla 自带的 `EnumArrayRenderer` 也能画，但它**不渲染字段标题与
 * 说明**、每个复选框还套着全局 `.input` 的 34px 输入框外观 —— 九个模块排下来是一片
 * 没名字的方框。这里换掉它：标题 / 说明走 `ControlWrapper`（和下拉、文本框同一套），
 * 每个选项用自己的 `.wp-checkbox-group__item`。
 *
 * 勾选结果按选项在 schema 里的顺序排好再回传：出站行里的模块顺序因此稳定，后端
 * `normalize_modules` 不必替前端擦屁股。
 */
import { computed } from 'vue'
import { rendererProps, useJsonFormsControl } from '@jsonforms/vue'
import { ControlWrapper, useVanillaControl } from '@jsonforms/vue-vanilla'

import LuyunCheckbox from '../ui/LuyunCheckbox.vue'

const props = defineProps({ ...rendererProps() })

const vanilla = useVanillaControl(useJsonFormsControl(props))
const control = vanilla.control
const controlWrapper = vanilla.controlWrapper
const appliedOptions = vanilla.appliedOptions
const styles = vanilla.styles
const handleChange = vanilla.handleChange

/** `items.oneOf` → `[{ value, label }]`，顺序即后端注册表的声明顺序。 */
const options = computed(() => {
  const items = (control.value && control.value.schema && control.value.schema.items) || {}
  if (!Array.isArray(items.oneOf)) return []
  return items.oneOf.map((option) => ({ value: option.const, label: option.title || option.const }))
})

const selected = computed(() => {
  const data = control.value && control.value.data
  return new Set(Array.isArray(data) ? data : [])
})

const selectedCount = computed(
  () => options.value.filter((option) => selected.value.has(option.value)).length,
)

function toggle(value) {
  if (!control.value.enabled) return
  const list = Array.isArray(control.value.data) ? [...control.value.data] : []
  const index = list.indexOf(value)
  if (index >= 0) {
    // 一个都不勾等于「什么都不发」——任务会照样入队，然后发送时渲染出一张只有标题的
    // 图。与其让店长事后才发现，不如在这里拦住最后一次取消。
    if (selectedCount.value <= 1) return
    list.splice(index, 1)
  } else {
    list.push(value)
  }
  const order = options.value.map((option) => option.value)
  list.sort((left, right) => order.indexOf(left) - order.indexOf(right))
  handleChange(control.value.path, list)
}
</script>

<template>
  <ControlWrapper
    v-bind="controlWrapper"
    :styles="styles"
    :is-focused="false"
    :applied-options="appliedOptions"
  >
    <div class="wp-checkbox-group" role="group" :aria-label="controlWrapper.label">
      <label
        v-for="option in options"
        :key="option.value"
        class="wp-checkbox-group__item"
        :class="{ 'is-disabled': !control.enabled }"
      >
        <LuyunCheckbox
          :model-value="selected.has(option.value)"
          :disabled="!control.enabled"
          @update:model-value="toggle(option.value)"
        />
        <span class="wp-checkbox-group__label">{{ option.label }}</span>
      </label>
    </div>
  </ControlWrapper>
</template>
