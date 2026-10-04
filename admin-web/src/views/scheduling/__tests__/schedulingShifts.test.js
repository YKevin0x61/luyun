import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../SchedulingShiftsView.vue'), 'utf8')
const shell = readFileSync(join(here, '../SchedulingLayout.vue'), 'utf8')
const calendar = readFileSync(join(here, '../SchedulingCalendarView.vue'), 'utf8')
const copy = readFileSync(join(here, '../../../utils/shiftTable.js'), 'utf8')
const tokens = readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const navBar = readFileSync(join(here, '../../../components/NavBar.vue'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

describe('店长端班次表（票 11）', () => {
  it('借共享样式表的令牌，不进卫生模块', () => {
    // 共享样式表由**壳**加载一份（`SchedulingLayout.vue`）：三个子页各加载一份会挂出
    // 重复的 <link>；壳一层管住，跟卫生管理端一个做法（那边也是 layout 加载、子页不管）。
    expect(shell).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(view).not.toMatch(/useScopedStylesheet\(/)
    expect(view).toMatch(/class="hygiene-admin shifts-page"/)
    expect(view).not.toMatch(/\/api\/hygiene/)
  })

  it('只读排班自己那几条路由', () => {
    // 店长门：全表（含停用 + 用量）、加一条、改名/启停、整表调顺序、删掉建错的。
    expect(view).toMatch(/api\.get\('\/api\/scheduling\/shifts\/manage'\)/)
    expect(view).toMatch(/api\.post\('\/api\/scheduling\/shifts', \{ name \}\)/)
    expect(view).toMatch(/api\.put\(`\/api\/scheduling\/shifts\/\$\{shift\.id\}`/)
    expect(view).toMatch(/api\.put\('\/api\/scheduling\/shifts\/order', \{ ids: next \}\)/)
    expect(view).toMatch(/api\.delete\(`\/api\/scheduling\/shifts\/\$\{target\.id\}`\)/)
    // 页面上真正的请求就这五条，都在排班那扇门里（头注释里提到的别处接口不算）。
    const calls = [...view.matchAll(/api\.(?:get|post|put|delete)\(\s*[`']([^`']*)/g)].map((m) => m[1])
    expect([...new Set(calls)].sort()).toEqual([
      '/api/scheduling/shifts',
      '/api/scheduling/shifts/${shift.id}',
      '/api/scheduling/shifts/${target.id}',
      '/api/scheduling/shifts/manage',
      '/api/scheduling/shifts/order',
    ])
  })

  it('停用与删除分得开：删不掉就把理由写在行上，不是只把按钮变灰', () => {
    // 判据与文案都在 util 里（`shiftTable.test.js` 钉着），页面只用它们。
    expect(view).toMatch(/canDelete\(shift, activeCount\)/)
    expect(view).toMatch(/deleteBlockedReason\(shift, activeCount\)/)
    expect(view).toMatch(/usageLine\(shift\)/)
    expect(view).toMatch(/toggleLabel\(shift\)/)
    expect(view).toMatch(/statusText\(shift\)/)
    // 停用是常规动作（还有人在上时服务端会说清人数），删除要过一次确认框。
    expect(view).toMatch(/@click="toggleActive\(shift\)"/)
    expect(view).toMatch(/@click="deleteTarget = shift"/)
    expect(view).toMatch(/<ConfirmDialog/)
    expect(view).toMatch(/confirm-label="删除"/)
    expect(view).toMatch(/danger/)
    // 那句「还有 N 个人」来自服务端，页面不自己数。
    expect(view).toMatch(/people/)
    // **写失败的那句话必须留在重读之后**：`load()` 开头会把 `errorText` 清掉，
    // 先写后读等于把「还有 3 个人的轮转里排着它」当场抹掉（真出过这个 bug：
    // 被拦下来的人只看到一次页面刷新）。会挨服务端拦的那几个动作都走 `reportFailure`
    // （停用、调顺序、删除、改档位）—— 加一条和改名没这道重读，照旧自己写 `errorText`。
    expect(view).toMatch(/async function reportFailure/)
    expect(view).toMatch(/await load\(\)\n  errorText\.value = message/)
    expect((view.match(/await reportFailure\(err, /g) || []).length).toBe(4)
    expect(view).not.toMatch(/errorText\.value = err\.message[\s\S]{0,80}await load\(\)/)
    expect(view).toMatch(/v-if="errorText"[\s\S]{0,40}role="alert"/)
  })

  it('任一动作在飞就全表按灰：别让人点了没反应，在飞的那条也不许被覆盖', () => {
    // 「有动作在飞」与「哪一行在忙」是两个状态：busyId 是后者，模板的 disabled 要按前者。
    // 以前只灰被点的那一行，别的行的按钮和输入框照常可点 —— 而 `saveName` / `moveShift` /
    // `setDutySlot` 前置有静默 `if (busyId.value) return`：点下去什么都不发生、也没有提示；
    // `toggleActive` 连 guard 都没有，直接把 busyId 覆盖成另一行，前面那条请求从此失去
    // 保护（`finally` 无条件清零，回来时把新动作的槽也清了）。
    expect(view).toMatch(/const busy = computed\(\(\) => busyId\.value !== 0\)/)
    expect(view).not.toMatch(/:disabled="[^"]*busyId === /)
    // 行里每一个写动作的控件都按「有动作在飞」灰：改名保存 / 档位 / ↑ / ↓ / 改名 / 启停 / 删除。
    expect((view.match(/:disabled="[^"]*\bbusy\b[^"]*"/g) || []).length).toBe(7)
    // 每个动作开头都留着同一道 guard（按钮已经灰了，这一道是给键盘/脚本兜底的）。
    expect((view.match(/if \([^)]*busy\.value\) return/g) || []).length).toBe(5)
    expect(view).toMatch(/async function toggleActive\(shift\) \{[\s\S]{0,80}?if \(busy\.value\) return/)
    // 只有自己还占着槽才清零。
    expect((view.match(/if \(busyId\.value === (?:shift|target)\.id\) busyId\.value = 0/g) || []).length).toBe(5)
    expect(view).not.toMatch(/finally \{\s*busyId\.value = 0\s*\}/)
  })

  it('删除按钮照「还有几条在用」灰：最后一个在用的不给删（服务端同一口径）', () => {
    expect(view).toMatch(/data\.active_count/)
    expect(view).toMatch(/canDelete\(shift, activeCount\)/)
    expect(view).toMatch(/deleteBlockedReason\(shift, activeCount\)/)
    expect(copy).toMatch(/export function canDelete\(shift, activeCount = 1\)/)
  })

  it('班次名的上限跟着服务端走，不在页面里写死第二份', () => {
    expect(view).toMatch(/data\.max_name/)
    expect(view).toMatch(/:maxlength="maxName \|\| undefined"/)
    // 页面里不许再出现那个数字（上限只有服务层一份）。
    expect(view).not.toMatch(/maxName = ref\(\d+\)/)
    // 「上移 / 下移」是一次给全的整表重排（服务端的 `reorder_shifts` 口径）。
    expect(view).toMatch(/moveShift\b/)
    expect(copy).toMatch(/export function moveShift/)
  })

  it('不写死班次名：白班夜班是数据，加第三条不用改代码', () => {
    // 整篇查（不是只查带引号的字面量）：模板里写一句 `<span>白班</span>` 也算写死。
    for (const name of ['白班', '夜班', '早班', '中班']) {
      expect(view).not.toContain(name)
    }
    // util 里只有一个例外：卫生档位的固定说法（票 10 —— 服务端的值也是 `day` / `night`，
    // 人话是「白班档」「夜班档」，跟班次叫什么名字无关）。剥掉它们之后，util 里同样一个
    // 班次名都不许有。
    const withoutDutyLabels = copy.replace(/[白夜]班档/g, '')
    for (const name of ['白班', '夜班', '早班', '中班']) {
      expect(withoutDutyLabels).not.toContain(name)
    }
  })

  it('每条班次的卫生档位都看得出、也改得动（票 10）：清空发的是空串', () => {
    // 一条班次挂卫生的哪一档日常检查（`duty_slot`）。三选一的选项和文案只有 util 一份，
    // 页面只 v-for —— 模板里不写死档位说法（后端的 400 那句话也在说同一套词）。
    expect(view).toMatch(/v-for="choice in DUTY_SLOT_CHOICES"/)
    expect(view).toMatch(/:value="dutySlotValue\(shift\)"/)
    expect(view).toMatch(/dutySlotText\(data\.shift\)/)
    expect(copy).toMatch(/export const DUTY_SLOT_CHOICES/)
    expect(copy).toMatch(/value: 'day'/)
    expect(copy).toMatch(/value: 'night'/)
    expect(copy).toMatch(/value: ''/)
    // 停用的班次也留在这一行里、也改得动：它的历史排班还要照这一档交日常
    // （整页没有拿 `is_active` 当 `v-if` 的地方 —— 停用只改标签和颜色）。
    expect(view).toMatch(/class="sDuty"/)
    expect(view).not.toMatch(/v-if="[^"]*is_active/)
    expect(view).toMatch(/@change="setDutySlot\(shift, \$event\.target\.value\)"/)
    // 新动作落在改名那条已有的路由上，body 里只带 `duty_slot`。
    expect(view).toMatch(
      /api\.put\(`\/api\/scheduling\/shifts\/\$\{shift\.id\}`, \{\s*duty_slot: dutySlotPayload\(value\)/,
    )
    // 三种取值只有 util 那一份：页面不许自己写 `duty_slot: null`（那是「这一项不动」，
    // 选了「不出日常」发它等于没改，而页面已经显示成清空了）。
    expect(view).not.toMatch(/duty_slot:\s*null/)
    expect(view).not.toMatch(/duty_slot:\s*['"]/)
    // 写失败沿用这一页的口径：先把服务端那句「卫生档位只能选…」留住，重读之后再说。
    expect(view).toMatch(/async function setDutySlot/)
    expect(view).toMatch(/await reportFailure\(err, '没改成'\)/)
  })

  it('用的每个令牌都在共享样式表里有定义', () => {
    const used = new Set([...view.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((m) => m[1]))
    expect(used.size).toBeGreaterThan(10)
    // 剥掉 CSS 注释再找「声明」：注释里提一句 `--hy-x` 不算定义。
    const css = tokens.replace(/\/\*[\s\S]*?\*\//g, '')
    const missing = [...used].filter((name) => !new RegExp(`${name}\\s*:`).test(css))
    expect(missing).toEqual([])
  })

  it('在管理端有一扇自己的门（前端与后端都登记）', () => {
    expect(router).toMatch(/workbenchHrPage\('\/workbench\/hr\/shifts', 'workbench-hr-shifts'/)
    expect(router).toMatch(/views\/scheduling\/SchedulingShiftsView\.vue/)
    // 管理端的门：不带 `HYGIENE_STAFF_META` 那套员工 meta（没有 staffAuth）。
    expect(router).not.toMatch(/scheduling-shifts[\s\S]{0,200}?staffAuth/)
    // 直连/反代硬导航那条路要认得这个地址（服务端 SPA 白名单）。
    expect(mainPy).toMatch(/SPA_PAGE_ROUTES = \([\s\S]*?"\/workbench\/hr\/shifts"/)
    // 顶栏那条「排班」按前缀亮：进了班次表，导航上还在排班这一档。
    expect(navBar).toMatch(/prefix: '\/workbench'/)
  })

  it('月历页上有一条路走得到这一页（不然没人知道班次能改）', () => {
    expect(calendar).toMatch(/router\.push\('\/workbench\/hr\/shifts'\)/)
    expect(calendar).toMatch(/<b>班次表<\/b>/)
    // 待办那根条的入口没被这次改动碰掉。
    expect(calendar).toMatch(/class="gPend gTodo"/)
  })
})
