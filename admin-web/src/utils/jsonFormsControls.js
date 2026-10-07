/**
 * 后端 uischema 与 JSON Forms（vanilla 渲染器）之间的那一层翻译。
 *
 * 注册表（`services/wecom_push_topics.py`）用 `options.control` 声明控件类型 ——
 * 「时间用 time 控件、档口用下拉」是后端知识（ADR 0097），前端照着渲染就行。但
 * vanilla 渲染器不认这个名字：它按 `options.format` / schema 的 `enum` / `oneOf`
 * 挑渲染器。所以这里把后端那套名字翻成渲染器认的形状。
 *
 * **加一类内容类型不需要动这个文件**：只要它用的控件还是这几类（时间 / 下拉 / 文本），
 * 翻译规则就够用。哪天后端引入一种这里没有的控件类型，才需要在这里补一行映射 ——
 * 那是渲染器的能力，不是某一类内容的知识。
 */

/** 后端声明的控件类型（与 `services/wecom_push_topics.py` 的 CONTROL_* 一致）。 */
export const CONTROL_TIME = 'time'
export const CONTROL_SELECT = 'select'
export const CONTROL_TEXT = 'text'

/**
 * 控件类型 → JSON Forms 认的 `options.format`。
 *
 * 只有时间需要显式翻译：下拉靠 schema 的 `oneOf`、文本框靠 `type: 'string'`，
 * 渲染器自己能认出来。
 */
const FORMAT_BY_CONTROL = {
  [CONTROL_TIME]: 'time',
}

/**
 * 把整份 uischema 翻成 JSON Forms 用的那一份（返回新对象，不改传进来的数据 ——
 * 那是 `/meta` 给的注册表内容，别就地改掉）。
 */
export function toJsonFormsUiSchema(uischema) {
  if (!uischema || !Array.isArray(uischema.elements)) return uischema
  return { ...uischema, elements: uischema.elements.map(toJsonFormsElement) }
}

function toJsonFormsElement(element) {
  if (!element || typeof element !== 'object') return element
  const control = element.options ? element.options.control : undefined
  if (!control) return element
  const options = { ...element.options }
  delete options.control
  const format = FORMAT_BY_CONTROL[control]
  if (format) options.format = format
  return { ...element, options }
}

/**
 * 把下拉的选项拆成「空值那一个」与「其余」。
 *
 * vanilla 渲染器给每个 select 前面都硬编码了一个 `<option value="">`（没有文案，语义是
 * 「清空」）。可档口的「全部（排除楼面）」const 恰好就是空串：于是同一个下拉里出现两个
 * `value=""` 的选项，浏览器选中第一个（空文案）⇒ **默认显示空白**，从下拉里挑「全部
 * （排除楼面）」也还是空白（D3）。
 *
 * 所以这里把 schema 里那个空值选项拎出来：渲染器用它自己的 title 去渲染第一个选项，
 * 循环里不再重复渲染一份。空串仍然表示「全部」（后端语义一个字没动），只是**看得见**了。
 *
 * `hasEmptyOption` 也给渲染器用：schema 里本来就有空值选项时，选中它要按选项的真实值
 * （空串）回传，而不是 vanilla 那条「selectedIndex === 0 就当清空」的老规矩。
 */
export function splitEmptySelectOption(options) {
  const list = Array.isArray(options) ? options : []
  const emptyIndex = list.findIndex((option) => String((option && option.value) ?? '') === '')
  if (emptyIndex < 0) return { hasEmptyOption: false, emptyLabel: '', options: list }
  const empty = list[emptyIndex] || {}
  return {
    hasEmptyOption: true,
    emptyLabel: String(empty.label ?? ''),
    options: list.filter((_, index) => index !== emptyIndex),
  }
}

/** `HH:MM:SS` → `HH:MM`（原生 time 控件在有的环境里会带上秒）。 */
const TIME_WITH_SECONDS = /^(\d{2}:\d{2}):\d{2}$/

/** uischema 里声明成 time 控件的字段名。 */
export function timeControlFields(uischema) {
  return (uischema && Array.isArray(uischema.elements) ? uischema.elements : [])
    .filter((element) => element && element.options && element.options.control === CONTROL_TIME)
    .map((element) => String(element.scope || '').split('/').pop())
    .filter(Boolean)
}

/**
 * 把控件给的参数修剪成后端 schema 接受的样子。
 *
 * 目前只有一件事：原生 `<input type="time">` 在有的环境里给出 `HH:MM:SS`，而后端的
 * 推送时间规则是严格的 `HH:MM`（`^([01]\d|2[0-3]):[0-5]\d$`）—— 不修剪的话店长一
 * 保存就是 400。修剪只发生在**后端声明为 time 控件**的字段上，别的字段一个字不动。
 *
 * **没有任何字段需要修剪时必须原样返回传进来的那个对象**（而不是复制一份）：JSON
 * Forms 把父组件传进去的 `data` 原样存进内部状态，`change` 事件回传的就是这个引用；
 * 父组件把它写回参数、引用没变，就不会再触发一轮 dispatch。反过来（每次都返回新
 * 对象）会让 prop 变化 → 重新 dispatch `UPDATE_DATA` → 又回调 `change` → 再复制，
 * 直到 Vue 报 `Maximum recursive updates exceeded`。
 */
export function normalizeControlValues(data, uischema) {
  const values = data || {}
  let trimmed = null
  for (const field of timeControlFields(uischema)) {
    const value = values[field]
    if (typeof value !== 'string') continue
    const matched = TIME_WITH_SECONDS.exec(value)
    if (!matched) continue
    if (!trimmed) trimmed = { ...values }
    trimmed[field] = matched[1]
  }
  return trimmed || values
}
