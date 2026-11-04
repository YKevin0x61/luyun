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
export const LOADING_TEXT = '正在按现在到打烊算…'

export const PICK_STATION_TITLE = '先选你在哪个岗位'
export const PICK_STATION_HINT = '选了只看得到这个岗位的备货项，今天随时能改。'

export const BASIS_HINT =
  '参考值 = 未来 24 小时要卖多少（近 8 周同星期几的分位量 × 整店水位）；再减掉你看到的实物库存，才是要备的量'
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
