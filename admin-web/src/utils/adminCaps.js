/** 员工的管理权限开关（2026-10-05 用户裁定：由超级管理员在花名册里逐项放权）。
 *
 * ## 键名是前后端**逐字相同的契约**
 *
 * 后端那一份在 `services/identity/capabilities.py`（`CAPABILITIES` / `CAPABILITY_LABELS` /
 * `CAPABILITY_NOTES`）。下面 `ADMIN_CAP_DEFS` 里的 `key` 与它的顺序必须与那份 Python
 * **逐字一致** —— 改键名或改顺序要**两边一起改**（`tests/test_hygiene_admin_caps.py` 会读
 * 本文件与那份比对）。线上只走键名（英文下划线风格），中文只用于界面显示。
 *
 * ## 为什么判据从「档位」换成「开关」
 *
 * 原来只有一个档位：`hygiene_employees.permission`（`普通员工` / `管理员`），勾上「管理员」
 * 就一次拿到三项能力（日常验收、专项验收、整改单）——表达不出"只该判日常、不该开整改单"
 * 这种组合。现在每一项能力一个开关，**判据一律看 `admin_caps`**。
 *
 * ## 为什么 `permission` 那一列还留着
 *
 * 它不是判据了，但仍然是界面上唯一说得清「这个人是普通员工还是管理员」的**人话标签**
 * （花名册的下拉、员工端「我的」都在显示它）。升级当场行为不变：迁移 `0015` 把升级前的
 * 「管理员」回填成 `daily_review` + `deep_review` + `fix` 这三项，之后动的每一个勾都直接
 * 决定能不能做那件事，不再有"标签给了、开关没给"的中间态。
 */

/** 十项能力：`key` 是契约键名，`label` / `note` 只用于界面显示，顺序 = 花名册里的显示顺序。 */
export const ADMIN_CAP_DEFS = [
  { key: 'daily_review', label: '日常验收', note: '判别人交的日常检查，看原图与标准图对照' },
  { key: 'deep_review', label: '专项验收', note: '判专项卫生的前后对照' },
  { key: 'fix', label: '整改单', note: '开整改单、验收或驳回整改单' },
  { key: 'attire', label: '仪容仪表', note: '看与判仪容打卡，维护仪容标准' },
  { key: 'standard', label: '标准图管理', note: '换标准图、改标注、导出整套标准' },
  { key: 'zone', label: '工作区与检查项', note: '加/改/删工作区与检查项' },
  { key: 'roster', label: '花名册与排班', note: '审批入职、停用、改权限与班次' },
  { key: 'boards', label: '红黑榜与教材', note: '标记合格对照、发红黑榜、编教材' },
  { key: 'clock', label: '时限设置', note: '设日常与专项的时限' },
  { key: 'data', label: '数据与归档', note: '看历史记录、导出归档、清理数据' },
]

/** 键名清单（顺序 = 契约）：`normalizeCaps` 的排序与后端 `dump_caps` 的写法都以它为准。 */
export const ADMIN_CAPABILITIES = ADMIN_CAP_DEFS.map((item) => item.key)

/** 键 → 中文标签。 */
export const ADMIN_CAP_LABELS = Object.fromEntries(
  ADMIN_CAP_DEFS.map((item) => [item.key, item.label]),
)

/** 键 → 一句话说明（界面上给管理员看的那句）。 */
export const ADMIN_CAP_NOTES = Object.fromEntries(
  ADMIN_CAP_DEFS.map((item) => [item.key, item.note]),
)

const KNOWN_CAPS = new Set(ADMIN_CAPABILITIES)

/** 后端 `parse_caps` 吃的是库里那一列的 JSON 文本；万一某条接口把原文发出来也认。
 *  按 `JSON.parse` 的规范它先 `ToString` 入参，所以 undefined / 对象 / 坏格式都在这里
 *  解析失败 → 空数组（fail-closed）。 */
function jsonArrayOrEmpty(raw) {
  try {
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/**
 * 把任意来源的一组管理权限归一成**只留认识的键、去重、按声明顺序**的字符串数组。
 *
 * 与后端 `parse_caps` / `dump_caps` 同口径：**认不出的键一律丢掉、坏数据当空**（fail-closed，
 * 这一组是权限，宁可少给一项也不能因为格式坏了就默认放行）。花名册的草稿、员工端的判据
 * 都先过它一道，页面各处拿到的因此永远是同一个形状。
 */
export function normalizeCaps(raw) {
  // 正常形状是数组（接口发下来的就是 JSON 数组）；不是数组的交给 `jsonArrayOrEmpty`。
  const list = Array.isArray(raw) ? raw : jsonArrayOrEmpty(raw)
  const picked = new Set()
  for (const item of list) {
    // 跟后端 `parse_caps` 一样按字符串认键（`str(item).strip()`）：数字、null 这些都不是键。
    const key = item === null || item === undefined ? '' : String(item).trim()
    if (KNOWN_CAPS.has(key)) picked.add(key)
  }
  return ADMIN_CAPABILITIES.filter((key) => picked.has(key))
}

/**
 * 这个人有没有这一项（后端 `has_cap` 的前端版）。
 *
 * 页面上的判据统一走它：**不要再写 `employee.permission === '管理员'`** —— 那个档位现在只
 * 是显示用的人话标签，同一档的两个人可以有完全不同的开关，拿标签判会放行没给的那件事。
 */
export function hasCap(caps, key) {
  if (!key) return false
  return normalizeCaps(caps).includes(key)
}
