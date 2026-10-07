<script setup>
/**
 * 推送任务的参数区：由后端注册表给的 `params_schema` + `uischema` 渲染（ADR 0097）。
 *
 * 用 JSON Forms 的三个包（core + vue + vanilla 渲染器）：vanilla 渲染器**不带样式**，
 * 所以 `src/styles/jsonforms.css` 把它的结构映射到页面既有的 `.input` / `.select` /
 * `.form-row`（那一份样式的类是 `input` / `select` / `text-area`，恰好与页面全局样式
 * 同名）。
 *
 * 这个组件不认识任何一类内容类型：字段名、标签、下拉选项、默认值全部来自 props。
 * 换一类内容类型就是换一份 schema/uischema —— 前端零改动。
 */
import { computed, markRaw } from 'vue'
import { JsonForms } from '@jsonforms/vue'
import { vanillaRenderers } from '@jsonforms/vue-vanilla'

import { normalizeControlValues, toJsonFormsUiSchema } from '../../utils/jsonFormsControls'

const props = defineProps({
  schema: { type: Object, default: () => ({ type: 'object', properties: {} }) },
  uischema: { type: Object, default: () => ({ type: 'VerticalLayout', elements: [] }) },
  data: { type: Object, default: () => ({}) },
  enabled: { type: Boolean, default: true },
})
const emit = defineEmits(['update:data'])

// markRaw：渲染器是静态的组件表，别让 JSON Forms 把它塞进响应式状态里（Vue 会就此
// 打印一条「component was made a reactive object」的性能告警）。
const renderers = markRaw(vanillaRenderers)
const formSchema = computed(() => props.schema || { type: 'object', properties: {} })
// 参数说明（schema 的 description）一直显示：门店页面上这些说明就是「这个参数是什么」
// 的唯一解释，藏到聚焦之后等于没有。
const formConfig = { showUnfocusedDescription: true }

const formUiSchema = computed(() => toJsonFormsUiSchema(props.uischema) || { type: 'VerticalLayout', elements: [] })

/** JSON Forms 通过 `change` 事件给出整份数据；修剪成 schema 接受的样子再交给调用方。 */
function onChange(event) {
  emit('update:data', normalizeControlValues((event && event.data) || {}, props.uischema))
}
</script>

<template>
  <div class="wp-params-form">
    <JsonForms
      :data="data"
      :schema="formSchema"
      :uischema="formUiSchema"
      :renderers="renderers"
      :enabled="enabled"
      :config="formConfig"
      @change="onChange"
    />
  </div>
</template>
