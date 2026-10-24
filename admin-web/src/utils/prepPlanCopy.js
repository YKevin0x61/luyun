/** User-facing prep-plan copy. Keep English enums off the chrome.
 *
 *  ADR 0117 之后这一页只剩一个时间口径（今天从现在到打烊），文案表也跟着瘦了：
 *  时间窗、信心、裕度、歇业日那一整批常量随功能一起退场。
 */

export const PREP_PLAN_TITLE = '备货计划'

export const MADE_LABEL = '厨房做的'
export const BOUGHT_LABEL = '要买的'
export const DONE_LABEL = '做完'
export const DONE_SECTION_PREFIX = '已做'
export const COPY_LABEL = '复制清单'
export const COPIED_LABEL = '已复制'
export const EMPTY_LIST_TEXT = '今天不用做。'
export const CLOSED_TEXT = '今天已打烊，明天早上再看。'
export const LOADING_TEXT = '正在按现在到打烊算…'

export const PICK_STATION_TITLE = '先选你在哪个档口'
export const PICK_STATION_HINT = '选了只看得到这个档口的备货项，今天随时能改。'

export const BASIS_HINT = '建议量 = 近 4 周同星期几均量 × 整店水位 + 本品残差缓冲'
export const FORMULA_HINT =
  '均量只是基准：缓冲按这个品历史上「均量估偏了多少」的 9 折分位来加，目标是九成的日子够卖。'
export const MISSING_RULE_TEXT = '没有配方系数，算不出建议量'
export const NO_HISTORY_TEXT = '近 4 周没有可比的同星期几'
export const RECIPE_FIX_LINK = '配方系数不对？去数据管理改'
export const KIND_FLIP_TO_BOUGHT = '这个其实是买的'
export const KIND_FLIP_TO_MADE = '这个其实是厨房做的'
export const MANUAL_MARK = '手改'
export const UNDO_LABEL = '撤回'
export const READ_ONLY_HINT = '只读：备货计划由管理端查看。'
