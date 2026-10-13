<script setup>
import { computed } from 'vue'
import { VueDatePicker } from '@vuepic/vue-datepicker'
// 日历弹层的中文（2026-10-13 用户裁定「日期格式要中国本土化」）：不传 locale 时
// vue-datepicker 默认英文 —— 月份是 `Oct`、星期是 `Su Mo Tu…`，跟整页中文打架
// （输入框里那串 `yyyy-MM-dd` 由 formats 控制，跟这个 locale 是两件事）。
// `date-fns` 是 vue-datepicker 自己的依赖，这里在 package.json 里显式声明，不靠提升。
import { zhCN } from 'date-fns/locale'

const props = defineProps({
  placeholder: { type: String, default: '选择日期' },
  disabled: { type: Boolean, default: false },
  dark: { type: Boolean, default: true },
  minDate: { type: [String, Date], default: undefined },
  maxDate: { type: [String, Date], default: undefined },
  ariaLabel: { type: String, default: '选择日期' },
  /** 透传给组件内那个 `<input>` 的 id（2026-10-13）。
   *
   *  页面上的 `<label for="...">` 与测试里的 `#id` 选择器要够得着**组件内部**的控件；
   *  没有它，标签点不动、`#id` 也选不中（注册页当初就是因为这个把 `<label>` 退成了
   *  `<span>`）。走 vue-datepicker 的 `inputAttrs` —— 直接写在组件上是落在根 div 上的。
   */
  inputId: { type: String, default: undefined },
  /** 月份模式（2026-10-13）：台账那种「按月份筛」用它。
   *
   *  原生 `<input type=month>` 跟 date 一个毛病 —— 显示格式由**浏览器界面语言**决定
   *  （英文机上是 `---------- ----`，比 date 还难看）。这个模式下 v-model 是 `yyyy-MM`。
   */
  monthPicker: { type: Boolean, default: false },
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
// 月份模式下进出的都是 `yyyy-MM`（不补 `-01`）：台账那边读的就是这个形状。
const formats = computed(() => ({
  input: props.monthPicker ? 'yyyy-MM' : 'yyyy-MM-dd',
}))
const modelType = computed(() => (props.monthPicker ? 'yyyy-MM' : 'yyyy-MM-dd'))
const timeConfig = { enableTimePicker: false }

const ariaLabels = computed(() => ({
  input: props.ariaLabel || props.placeholder || '选择日期',
  clearInput: `清除${props.ariaLabel || '日期'}`,
  toggleOverlay: `打开${props.ariaLabel || '日期选择器'}`,
  calendarIcon: `打开${props.ariaLabel || '日期选择器'}`,
  menu: props.ariaLabel || '日期选择器',
}))

/** 只有给了 `inputId` 才往内部 input 上挂属性；没给就别塞一份空对象。 */
const inputAttrs = computed(() => (props.inputId ? { id: props.inputId } : undefined))
</script>

<template>
  <VueDatePicker
    v-model="inner"
    :model-type="modelType"
    :formats="formats"
    :time-config="timeConfig"
    :month-picker="monthPicker"
    :disabled="disabled"
    :placeholder="placeholder"
    :min-date="minDate"
    :max-date="maxDate"
    :dark="dark"
    :locale="zhCN"
    :input-attrs="inputAttrs"
    :aria-labels="ariaLabels"
    teleport="body"
    auto-apply
    class="luyun-date-picker"
  />
</template>
