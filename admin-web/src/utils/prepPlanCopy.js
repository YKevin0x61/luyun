/** User-facing prep-plan copy. Keep English enums off the chrome.
 *
 *  ADR 0117 之后这一页只剩一个时间口径（今天从现在到打烊），文案表也跟着瘦了：
 *  时间窗、信心、裕度、歇业日那一整批常量随功能一起退场。
 */

export const PREP_PLAN_TITLE = '备货计划'

export const MADE_LABEL = '半成品'
export const BOUGHT_LABEL = '原料'
export const DONE_LABEL = '做完'
export const DONE_SECTION_PREFIX = '已做'
export const COPY_LABEL = '复制清单'
export const COPIED_LABEL = '已复制'
export const EMPTY_LIST_TEXT = '今天不用做。'
export const CLOSED_TEXT = '今天已打烊，明天早上再看。'

export const PICK_STATION_TITLE = '先选你在哪个岗位'
export const PICK_STATION_HINT = '选了只看得到这个岗位的备货项，今天随时能改。'

// 窗口一律从**现在**起算，所以 48 小时不是「今天 + 明天两个整天」，而是滚动的 48 小时；
// 页头那句「现在 → 明天 13:59」也因此会变成「现在 → 后天 13:59」。
export const HORIZON_OPTIONS = [
  { hours: 24, label: '24 小时' },
  { hours: 48, label: '48 小时' },
  { hours: 72, label: '72 小时' },
  { hours: 168, label: '7 天' },
]
export const DEFAULT_HORIZON_HOURS = 24
/** 记住上次选的跨度：师傅切到「7 天」备周末货，下周不该再切一次。 */
export const HORIZON_STORAGE_KEY = 'prepPlanHorizon'

export function horizonLabel(hours) {
  const found = HORIZON_OPTIONS.find((row) => row.hours === Number(hours))
  return found ? found.label : HORIZON_OPTIONS[0].label
}

/** 大标题的前半句：24 小时说「今天」，长跨度直说「未来 48 小时 / 未来 7 天」。 */
export function horizonTitle(hours) {
  const value = Number(hours) || DEFAULT_HORIZON_HOURS
  return value === DEFAULT_HORIZON_HOURS ? '今天' : `未来 ${horizonLabel(value)}`
}

/** 页脚口径：跨度变了括号里那半句得跟着变，其余算法口径不变。 */
export function basisHint(hours) {
  const value = Number(hours) || DEFAULT_HORIZON_HOURS
  const span = `未来 ${horizonLabel(value)}`
  return `参考值 = ${span}要卖多少（近 8 周同星期几的分位量 × 整店水位）；再减掉你看到的实物库存，才是要备的量`
}

/** 加载态：说清正在按多长的窗口算，切换后别让人以为还在算 24 小时。 */
export function loadingText(hours) {
  const value = Number(hours) || DEFAULT_HORIZON_HOURS
  return `正在按现在起的 ${horizonLabel(value)}算…`
}

export const BASIS_HINT = basisHint(DEFAULT_HORIZON_HOURS)
export const LOADING_TEXT = loadingText(DEFAULT_HORIZON_HOURS)

export const FORMULA_HINT =
  '大品取 p70、其余取 p50，都不加安全量——24 小时保质，多做出来就是报废。'
export const MAJOR_TAG = '大品'
export const MISSING_RULE_TEXT = '没有配方系数，算不出建议量'
// 配方条目还在、菜也还在卖，但一条明细量都算不出来（系数被存成 0 / 明细被清空）。
// 这种「静默 0」会真的少备一天货，必须显式喊出来而不是显示 0。
export const BROKEN_RULE_TEXT = '配方接不上这个菜，参考值算不出来，请人工确认'
export const STALE_FALLBACK_TEXT = '（候选日都没量，按近两周平均补的）'
export const NO_HISTORY_TEXT = '近 8 周没有可比的同星期几'
export const RECIPE_FIX_LINK = '配方系数不对？去数据管理改'
export const KIND_FLIP_TO_BOUGHT = '这个其实是原料'
export const KIND_FLIP_TO_MADE = '这个其实是半成品'
export const MANUAL_MARK = '手改'
export const UNDO_LABEL = '撤回'
export const READ_ONLY_HINT = '只读：备货计划由管理端查看。'

export const EXPIRED_HINT = '这批已经超过 24 小时，不能再算在库，请报废'
export const EXPIRED_ACTION = '报废'
export const DEMAND_LABEL = '今天要卖'
export const REMAIN_LABEL = '还要做'

// ── 备货复盘（ADR 0129）──────────────────────────────────────────────────
// 这一页的易错点：一条快照只对应它自己那个 24 小时窗口，绝不能把一天 24 条平均起来。
export const PREP_REVIEW_KICKER = '备货复盘'
export const PREP_REVIEW_TITLE = '那一次给的参考值，事后准不准'
export const PREP_REVIEW_EMPTY =
  '这天还没有快照。整点快照从部署后开始累积，实际销量要等 24 小时窗口结束后才回填。'

// ── 复盘页 · 90 天口径（票 06 前端）─────────────────────────────────────────
// 三张新卡与「为什么不准」都读 `/review/summary`；NULL 表示分母为 0，不拿 0 充数。
export const PREP_REVIEW_METRIC_ABS = '绝对偏差'
export const PREP_REVIEW_METRIC_ABS_HINT = '建议与实际差多少（取绝对值），按实际销量加权'
export const PREP_REVIEW_METRIC_DIR = '方向偏差'
export const PREP_REVIEW_METRIC_DIR_HINT = '正 = 备多了，负 = 备少了'
export const PREP_REVIEW_METRIC_COVERAGE = '数量口径够卖率'
export const PREP_REVIEW_METRIC_COVERAGE_HINT = '按数量算 min(建议, 实际) ÷ 实际，不按品数'

/** 期间小字：换日期时「截至选中日」跟着走，口径固定在 07:00 主口径。 */
export function prepReviewPeriodHint(days, clock) {
  return `近 ${days} 天（截至选中日），按 ${clock} 主口径；分母为 0 显示「—」`
}

export const PREP_REVIEW_WHY_TITLE = '为什么不准'
export const PREP_REVIEW_WHY_HINT = '每组按有数据的天数 n 统计，右侧两列是方向偏差与绝对偏差；n < 5 天只标样本不足，不拿 0 顶替'
export const PREP_REVIEW_SAMPLE_SHORT = '样本不足'
export const PREP_REVIEW_WHY_DIMENSIONS = [
  { key: 'weekday', label: '星期几' },
  { key: 'holiday', label: '节假日' },
  { key: 'weather', label: '天气' },
  { key: 'staff', label: '档口人数' },
  { key: 'note', label: '备注' },
]

export const PREP_REVIEW_COST_TITLE = '多备的代价'
export const PREP_REVIEW_COST_HINT = '多出量 = 次日卖出 + 报废 + 未明；只在「建议 > 实际」的品-天上算'
export const PREP_REVIEW_COST_TOTAL = '多出量'
export const PREP_REVIEW_COST_ROWS = [
  { key: 'next_day_sold', label: '次日卖出' },
  { key: 'wastage', label: '报废' },
  { key: 'unexplained', label: '未明' },
]
export const PREP_REVIEW_COST_ESTIMATED = '估算'
export const PREP_REVIEW_COST_ESTIMATED_HINT = '按期间加权成交均价折出来的钱，不是成本核算'
export const PREP_REVIEW_COST_LOSS_LINK = '去损耗分析'
export const PREP_REVIEW_COST_RECORDED_HINT = (recorded, counted) =>
  `同期登记报废 ${recorded}，其中 ${counted} 落在多备的品-天上`

export function prepReviewPendingHint(pending) {
  return `${pending} 个品-天的次日销量还在等回填`
}

// ── 复盘页 · 今日备注 / 环境信号 ────────────────────────────────────────────
export const PREP_REVIEW_NOTE_LABEL = '备注'
export const PREP_REVIEW_NOTE_EDIT = '改备注'
export const PREP_REVIEW_NOTE_SAVE = '保存'
export const PREP_REVIEW_NOTE_CANCEL = '取消'
export const PREP_REVIEW_NOTE_PREFIX = '备注：'
export const PREP_REVIEW_NOTE_PLACEHOLDER = '团餐、活动、设备故障，一行就够'
export const PREP_REVIEW_NOTE_FAILED = '备注没存上，再试一次'
export const PREP_REVIEW_CONTEXT_HOLIDAY = '节假日'
export const PREP_REVIEW_CONTEXT_WEATHER = '天气'
export const PREP_REVIEW_CONTEXT_STAFF = '在岗'

export function prepReviewContextStaff(total) {
  return `在岗 ${total} 人`
}

// ── 复盘页 · 明细与导出 ─────────────────────────────────────────────────────
export const PREP_REVIEW_DETAIL_HINT =
  '窗口后 = 累计轨迹 − 窗口内实际卖出，只算关闭后新增；退菜是近似值'
export const PREP_REVIEW_TH_SOLD_OUT = '售罄'
export const PREP_REVIEW_TH_AFTER_1H = '窗口后 +1h'
export const PREP_REVIEW_TH_AFTER_2H = '窗口后 +2h'
export const PREP_REVIEW_TH_NEXT_DAY = '次日'
export const PREP_REVIEW_TH_REFUND = '退菜'
export const PREP_REVIEW_REFUND_APPROX = '近似'
export const PREP_REVIEW_EXPORT = '导出 CSV'
export const PREP_REVIEW_EXPORT_FAILED = '导出失败，再试一次'

export const PREP_REVIEW_DIR_OVER = '备多了'
export const PREP_REVIEW_DIR_UNDER = '备少了'
export const PREP_REVIEW_DIR_EVEN = '刚好'
export const PREP_REVIEW_LOADING = '正在读快照…'
export const PREP_REVIEW_LOAD_FAILED = '快照读不出来'
export const PREP_REVIEW_SUMMARY_FAILED = '90 天汇总读不出来'
export const PREP_REVIEW_EXPORT_EMPTY = '当前钟点没有明细可导出'

export const PREP_REVIEW_COST_AMOUNT = '估算金额'
