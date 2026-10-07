<script setup>
/**
 * 下拉控件的渲染器：补上 vanilla 渲染器里那个「没有文案的空选项」。
 *
 * @jsonforms/vue-vanilla 的枚举渲染器（`EnumControlRenderer` / `EnumOneofControlRenderer`）
 * 在每个 select 前面硬编码了一个 `<option value="">`，没有 label、语义是「清空」。这对
 * 「日期口径」这类选项没问题（默认值 `today` 命中第二个选项），但档口的「全部（排除
 * 楼面）」const 就是空串 —— 那个硬编码的选项与它 value 相同、又排在前面，浏览器按 value
 * 选中第一个 ⇒ 下拉**显示空白**，挑「全部（排除楼面）」也还是空白（D3）。
 *
 * 这里只改一件事：空值选项由 schema 里那一个的 title 渲染（没有就还是原来那个空选项，
 * 「清空」语义照旧），并且它不再被渲染第二遍。schema / 后端语义一个字没动 —— 空串依旧
 * 表示「全部」。
 */
import { computed, ref } from 'vue'
import {
  rendererProps, useJsonFormsEnumControl, useJsonFormsOneOfEnumControl,
} from '@jsonforms/vue'
import { ControlWrapper, useVanillaControl } from '@jsonforms/vue-vanilla'

import { splitEmptySelectOption } from '../../utils/jsonFormsControls'

const props = defineProps({ ...rendererProps() })

/** 选中的值：schema 里本来就有空值选项时按选项的真实值走，否则保留 vanilla 的「清空」。 */
function adaptTarget(target) {
  if (emptyOption.value.hasEmptyOption) return target.value
  return target.selectedIndex === 0 ? undefined : target.value
}

// 两种枚举形状（`oneOf` / `enum`）各有一套解析器，都是 `useControl` 的薄封装。组合式函数
// 不能条件调用，所以两套都在 setup 里建好，真正用哪一套按 schema 的形状定（注册表给的
// select 一律是 oneOf，`enum` 那一套留给将来）。
const oneOfEnum = useVanillaControl(useJsonFormsOneOfEnumControl(props), adaptTarget)
const plainEnum = useVanillaControl(useJsonFormsEnumControl(props), adaptTarget)

/** 控件作用域上解析好的那份 schema（`props.schema` 是**根** schema，oneOf 在它的
 * `properties.<字段>` 下面，照它判形状会认错）。两套解析器解析出来的是同一份。 */
const controlSchema = computed(() => (oneOfEnum.control.value && oneOfEnum.control.value.schema) || {})
const isOneOf = computed(() => Array.isArray(controlSchema.value.oneOf))
const active = computed(() => (isOneOf.value ? oneOfEnum : plainEnum))
const control = computed(() => active.value.control.value)
const controlWrapper = computed(() => active.value.controlWrapper.value)
const appliedOptions = computed(() => active.value.appliedOptions.value)
/** 样式表与 uischema 的形状无关，两套解析器拿到的是同一份。 */
const styles = oneOfEnum.styles
// 自己管聚焦：两套解析器各有一个自己的 isFocused，跟着哪个走都不对。
const isFocused = ref(false)

const options = computed(() => (control.value && control.value.options) || [])
const emptyOption = computed(() => splitEmptySelectOption(options.value))

function onChange(event) {
  active.value.onChange(event)
}
</script>

<template>
  <ControlWrapper
    v-bind="controlWrapper"
    :styles="styles"
    :is-focused="isFocused"
    :applied-options="appliedOptions"
  >
    <select
      :id="control.id + '-input'"
      :class="styles.control.select"
      :value="control.data"
      :disabled="!control.enabled"
      :autofocus="appliedOptions.focus"
      @change="onChange"
      @focus="isFocused = true"
      @blur="isFocused = false"
    >
      <option key="empty" value="" :class="styles.control.option">{{ emptyOption.emptyLabel }}</option>
      <option
        v-for="optionElement in emptyOption.options"
        :key="optionElement.value"
        :value="optionElement.value"
        :label="optionElement.label"
        :class="styles.control.option"
      ></option>
    </select>
  </ControlWrapper>
</template>
