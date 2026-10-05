/**
 * 行标识：把一行数据翻译成"人能核对的一句话"。
 *
 * 为什么要有这个：删除确认原本只写「确认删除该行（rowid=7）？」——rowid 是数据库主键，
 * 对店长没有任何核对价值（同一列在有的表里是 7、在有的表里是 1,2,3…），而且**它还是
 * 唯一被说出来的信息**：既不说删的是哪一行，也不说删了不可撤销。危险确认框的全部意义
 * 就是让用户在按下之前能确认"我要动的是这一条"，只给主键等于没给。
 *
 * 这里按"这一行有哪些列"来挑要点，而不是按表名写死映射：数据管理页 49 张表共用同一条
 * 确认通道，逐表写映射必然漏（而且新增表不会有维护者想起来补）。挑法：
 *
 * 1. 先取业务列（`DETAIL_COLUMNS` 里的列名按优先级），凑够 `max` 条为止；
 * 2. 补时间列（`created_at` / `order_time` / `sent_at` 之类），它决定"哪一条"；
 * 3. 还凑不够就用表里前几个非空、短、可读的列兜底 —— 空手总比什么都没有强；
 * 4. 最后一定附上主键（`序号 <id>`），它是核对时唯一能对得上表格首列的值。
 */

/**
 * 用来描述"这一行是什么"的候选列，按信息量从高到低。
 *
 * 顺序有讲究：**先"这是哪一条"，再"这一条的某个值"**。数量 / 金额 / 状态这类数值列
 * 只说明"这条记录怎么了"，两条不同记录的数量往往一模一样；确认框里 3 个位置很宝贵，
 * 用在数量上就挤掉了真正能核对身份的桌号与时间。
 */
const DETAIL_COLUMNS = [
  ['dish_name', '菜品'],
  ['name', '名称'],
  ['title', '标题'],
  ['item_name', '项目'],
  ['semi_name', '半成品'],
  ['recipe_name', '配方'],
  ['table_number', '桌号'],
  ['business_flow_id', '流水号'],
  ['station', '档口'],
  ['station_id', '档口'],
  ['username', '用户名'],
  ['label', '标签'],
  ['phone', '手机号'],
  ['slug', '标识'],
  ['key', '配置键'],
  ['status', '状态'],
  ['quantity', '数量'],
  ['amount', '金额'],
  ['total_amount', '总价'],
]

/** 时间列：决定"哪一条"，但排在业务列之后（业务列更好认）。 */
const TIME_COLUMNS = ['created_at', 'order_time', 'occurred_at', 'sent_at', 'updated_at', 'captured_at', 'changed_at']

/** 兜底时跳过的列：主键、长文本、机器字段。 */
const NOISY_COLUMNS = new Set(['id', 'rowid', 'password_hash', 'token_hash', 'session_id', 'content_sha256'])

/** 兜底列的最大长度：超过就该进详情而不是挤进一句话里。 */
const FALLBACK_MAX_CHARS = 24

function pickValue(row, names) {
  for (const name of names) {
    const value = row?.[name]
    if (value === null || value === undefined || value === '') continue
    return { name, value }
  }
  return null
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'object') return JSON.stringify(value)
  const text = String(value)
  // 后端把 ISO 时间原样给过来，确认框里只保留到分钟就够核对了。
  const iso = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
  if (iso) return `${iso[1]} ${iso[2]}`
  return text
}

function truncate(text, max) {
  const value = String(text ?? '')
  return value.length > max ? `${value.slice(0, max)}…` : value
}

/** 只有"这个字段等于某个值"含义的列：换个行也是同样的值，认不出是哪一条。 */
const VALUE_ONLY_LABELS = new Set(['状态', '数量', '金额', '总价'])

/** 名称类列写成「…」，读起来像"这一条"而不是"某个字段等于某值"。 */
const NAME_LIKE_LABELS = new Set(['名称', '菜品', '标题', '配方', '半成品', '项目'])

/** 兜底列：表里前几个非空、短、可读的字段（跳过长文本与机器字段）。 */
function fallbackDetails(row, used) {
  const out = []
  for (const [name, value] of Object.entries(row || {})) {
    if (used.has(name) || NOISY_COLUMNS.has(String(name).toLowerCase())) continue
    if (value === null || value === undefined || value === '' || typeof value === 'object') continue
    const text = formatValue(value)
    if (text.length > FALLBACK_MAX_CHARS) continue
    out.push(truncate(text, FALLBACK_MAX_CHARS))
  }
  return out
}

/**
 * @param {object} row 表格里那一行的原始数据（含 rowid）
 * @param {object} [opts]
 * @param {number} [opts.max] 业务列最多取几条，默认 3
 * @param {boolean} [opts.withKey] 是否附上主键，默认 true
 * @returns {string} 形如 `「宫保鸡丁」· 桌号 A12 · 2026-10-05 12:03（序号 7）`；
 *   行里一个可用字段都没有时退回 `序号 7`；连主键都没有则返回空串。
 */
export function describeRow(row, { max = 3, withKey = true } = {}) {
  const used = new Set()

  const details = []
  for (const [name, label] of DETAIL_COLUMNS) {
    const value = row?.[name]
    if (value === null || value === undefined || value === '') continue
    used.add(name)
    const text = formatValue(value)
    // 名称类列加书名号，读起来像"这一条"，不是"某个字段等于某值"。
    details.push({
      label,
      text: NAME_LIKE_LABELS.has(label) ? `「${text}」` : `${label} ${text}`,
    })
  }

  // 时间列排在哪里，取决于"业务列够不够认出这一条"：
  // - 有身份列（菜品 / 桌号 / 流水号 / 用户名…）→ 已经认得出，时间排在它们后面；
  // - 只有值类列（状态、数量、金额）→ 这些值在表格里到处都是，**时间才是唯一能
  //   定位"哪一条"的**，必须排在它们前面（运行日志这类表就靠时间分条）；
  // - 一个业务列都没有 → 时间是唯一线索。
  const time = pickValue(row, TIME_COLUMNS)
  const timeText = time ? formatValue(time.value) : ''
  if (time) used.add(time.name)

  const leadingDetails = []
  const trailingDetails = []
  for (const detail of details) {
    // 有身份列时值类列完全不要：一个位置留给时间比"数量 2"有用得多。
    (timeText && VALUE_ONLY_LABELS.has(detail.label) ? trailingDetails : leadingDetails).push(detail)
  }

  const parts = []
  for (const detail of leadingDetails) {
    if (parts.length >= max) break
    parts.push(detail.text)
  }
  if (timeText && parts.length < max && !parts.includes(timeText)) parts.push(timeText)
  // 收尾：先补齐值类列，再拿兜底列凑满（时间已经进去了，不再重复）。
  for (const detail of [...trailingDetails.map((d) => d.text), ...fallbackDetails(row, used)]) {
    if (parts.length >= max) break
    if (parts.includes(detail)) continue
    parts.push(detail)
  }

  const key = row?.rowid
  if (withKey && key !== null && key !== undefined && key !== '') {
    parts.push(`序号 ${key}`)
  }
  return parts.join(' · ')
}
