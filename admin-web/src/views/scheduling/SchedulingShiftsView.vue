<script setup>
// 超级管理员端「排班 · 班次表」（票 11、票 10）：加一个班次、改名字、调显示顺序、启用停用、
// 标它属于卫生的哪一档日常检查、把刚建错的那一条删掉 —— 都不用改库。
//
// 三件事在这一页上说清楚 —— **代码里说清，界面上的常驻文案刻意压到一行**（2026-10-08
// 用户反馈「文案太多、啰嗦、影响美观」：原来是页头三行 + 卡内两段 + 一整张「停用和删除
// 的区别」卡，而那张卡讲的东西已经在按钮 title、行内 `sWhy`、删除确认框里各出现一次）：
// - **停用不等于删除**：停用之后新排班不再用它，**已经写下的历史排班照旧显示**那个
//   班次（服务层的 `_shifts_for_display` 保证）。所以「停用」是常规动作，「删除」只
//   留给一天班都没排过的那一条 —— 按钮该灰就灰，原因写在旁边。
// - **还有人在上就不给停用**：说清是多少个人、让他们先改规则。那个数字来自服务端
//   （`/shifts/manage` 的 `people`），页面不自己数 —— 数错了超级管理员会照着错的信息做决定。
// - **档位认的是 id 不是名字**（票 10）：这条班次排到的人做卫生哪一档的日常检查，记在
//   这条班次行上（`duty_slot`）。所以这一页要能看清、也能改 —— 超级管理员给班次改名那天，
//   卫生那边不该跟着断（名字是数据，档位才是那根线）。
//
// 班次是**数据**不是常量：这一页按 N 个班次写，加第三个班次之后月历图例、配规则的下拉、
// 员工端那张卡都会自己多出一种班别，不需要改代码（票面的验收项）。
//
// 视觉沿用 `public/hygiene-admin.css` 的深青墨令牌 —— 那是**共享的样式表**，不是卫生
// 模块：排班不 import 卫生的 Python 模块、不挂它的菜单，只是同一套验收台配色。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { WORKBENCH_HR_HOME, workbenchDocumentTitle } from '../../utils/workbenchCopy'
import { api } from '../../api/client'
// 共享样式表由壳加载（`SchedulingLayout.vue`）：三个子页各加载一份会挂出重复的
// <link>，壳一层管住就跟卫生管理端一个做法。
import {
  DUTY_SLOT_CHOICES,
  canDelete,
  deleteBlockedReason,
  dutySlotPayload,
  dutySlotText,
  dutySlotValue,
  SHIFT_COLOR_CHOICES,
  SHIFT_COLOR_NEUTRAL,
  colorOwner,
  moveShift as moveShiftOrder,
  shiftColorClass,
  shiftColorLabel,
  statusText,
  toggleLabel,
  usageLine,
} from '../../utils/shiftTable'


const router = useRouter()

const state = ref('loading') // 'loading' | 'ready' | 'error'
const errorText = ref('')
// 写成功的一句回执。不放进 errorText：它不是错误，但两者在同一块地方显示，
// 谁最后发生谁说话（这里只在动作成功后写，出错时 errorText 会顶掉它）。
const receipt = ref('')
const shifts = ref([])
// 班次名的上限由服务端给（同 `/roster` 的 `max_cycle_days`）：页面不写死第二份，
// 读到之前先按「没有上限」处理（输入框这会儿还没人用得上）。
const maxName = ref(null)
// 还在用的班次条数：删掉最后一条在用的就没人能排班了，删除按钮照它灰着。
const activeCount = ref(0)
const busyId = ref(0)
// 「有动作在飞」和「哪一行在忙」是两件事，得分开放：busyId 记的是后者（只在回执/
// 守卫里定位这次动的是哪一行），模板的 disabled 一律按前者 `busy` 全灰 —— 只要还有
// 一个写请求没回来，别的行就不该装作能点。以前只灰被点的那一行，别的行的按钮和
// 输入框照常可点：`saveName` / `moveShift` / `setDutySlot` 的前置 guard 直接静默
// return（点下去什么都不发生、也没有提示），`toggleActive` 连 guard 都没有，会把
// busyId 覆盖成另一行 —— 前面那条请求从此失去保护，`finally` 再把它一起清掉。
const busy = computed(() => busyId.value !== 0)
const newName = ref('')
const addBusy = ref(false)
const editingId = ref(0)
const editName = ref('')
const deleteTarget = ref(null)

const addPlaceholder = computed(() =>
  maxName.value ? `新班次的名字（最多 ${maxName.value} 个字）` : '新班次的名字'
)

const orderIds = computed(() => shifts.value.map((shift) => shift.id))

async function load() {
  state.value = 'loading'
  errorText.value = ''
  try {
    const data = await api.get('/api/scheduling/shifts/manage')
    shifts.value = data.shifts || []
    if (data.max_name) maxName.value = data.max_name
    activeCount.value = data.active_count || 0
    state.value = 'ready'
  } catch (err) {
    errorText.value = err.message || '班次表读不出来'
    state.value = 'error'
  }
}

/** 写失败：先把服务端那句话留好，重读一次表，**最后**再说出来。
 *
 *  顺序不能反：`load()` 开头会把 `errorText` 清掉（那是给进页面/重读用的），先写
 *  后读等于把「还有 3 个人的轮转里排着它」这句话当场抹掉 —— 被拦下来的人就只看到
 *  一次页面刷新，不知道发生了什么（票面验收 3 在 UI 上就不成立了）。
 */
async function reportFailure(err, fallback) {
  const message = err.message || fallback
  await load()
  errorText.value = message
}

async function addShift() {
  const name = newName.value.trim()
  if (!name || addBusy.value) return
  addBusy.value = true
  errorText.value = ''
  receipt.value = ''
  try {
    const data = await api.post('/api/scheduling/shifts', { name })
    newName.value = ''
    receipt.value = `加好了：${data.shift.name}（排在最后，可以用 ↑ 调位置）`
    await load()
  } catch (err) {
    errorText.value = err.message || '没加成'
  } finally {
    addBusy.value = false
  }
}

function startRename(shift) {
  editingId.value = shift.id
  editName.value = shift.name
}

function cancelRename() {
  editingId.value = 0
  editName.value = ''
}

async function saveName(shift) {
  const name = editName.value.trim()
  if (!name || busy.value) return
  busyId.value = shift.id
  errorText.value = ''
  receipt.value = ''
  try {
    const data = await api.put(`/api/scheduling/shifts/${shift.id}`, { name })
    cancelRename()
    receipt.value = `改好了：${shift.name} → ${data.shift.name}`
    await load()
  } catch (err) {
    errorText.value = err.message || '没改成'
  } finally {
    if (busyId.value === shift.id) busyId.value = 0
  }
}

async function toggleActive(shift) {
  if (busy.value) return
  busyId.value = shift.id
  errorText.value = ''
  receipt.value = ''
  try {
    const data = await api.put(`/api/scheduling/shifts/${shift.id}`, {
      is_active: !shift.is_active,
    })
    receipt.value = data.shift.is_active
      ? `启用了：${data.shift.name} 又能排班了，以前的排班没动`
      : `停用了：${data.shift.name} 不再出现在新排班里，历史排班照旧显示`
    await load()
  } catch (err) {
    // 服务端把「还有几个人在轮转里排着它」说全了，原话转给超级管理员（`reportFailure`
    // 保证重读之后那句话还在）。
    await reportFailure(err, '没改成')
  } finally {
    if (busyId.value === shift.id) busyId.value = 0
  }
}

/** 改档位：三选一里选了什么就发什么，**清空那一项由 `dutySlotPayload` 发成空串**
 *  （发 `null` 后端当成「这一项不动」，页面会显示成清空了而库里没变）。
 *
 *  失败照这一页的口径走 `reportFailure`：服务端对非法值有一句原话（卫生档位只能选…），
 *  要留在重读之后再说。
 */
async function setDutySlot(shift, value) {
  if (busy.value) return
  busyId.value = shift.id
  errorText.value = ''
  receipt.value = ''
  try {
    const data = await api.put(`/api/scheduling/shifts/${shift.id}`, {
      duty_slot: dutySlotPayload(value),
    })
    receipt.value = `改好了：${data.shift.name} 的卫生日常 → ${dutySlotText(data.shift)}`
    await load()
  } catch (err) {
    await reportFailure(err, '没改成')
  } finally {
    if (busyId.value === shift.id) busyId.value = 0
  }
}

/** 改班次颜色（0043）：点了哪个色块就发哪个键，**已被人占用的那一档不可点**。
 *
 *  不可点的判据跟服务端的部分唯一索引同一口径（只数启用中的班次），但真正兜底的是
 *  服务端：它会把占着这档色的班次名回在原话里（`shift_color_taken`）。
 */
async function setColor(shift, key) {
  if (busy.value) return
  // 点的就是现状：不发请求（不发也就不会把回执刷成一条没变化的话）。
  if (key === (shift.color_key || SHIFT_COLOR_NEUTRAL)) return
  busyId.value = shift.id
  errorText.value = ''
  receipt.value = ''
  try {
    const data = await api.put(`/api/scheduling/shifts/${shift.id}`, { color_key: key })
    receipt.value = `改好了：${data.shift.name} 的班次色 → ${shiftColorLabel(data.shift.color_key)}`
    await load()
  } catch (err) {
    await reportFailure(err, '没改成')
  } finally {
    if (busyId.value === shift.id) busyId.value = 0
  }
}

/** 色块的 title / aria-label：被别人占着就说清是谁（同「停用前先看人数」那条口径）。 */
function colorTitle(shift, key) {
  const owner = colorOwner(shifts.value, key, shift.id)
  const label = shiftColorLabel(key)
  return owner ? `${label}：${owner.name} 在用` : label
}

// 色块的底色只从 token 来：`.tone-<键>`（`theme.workbench.css`）把 `--shift-fill` 定成那一档，
// `.sSw-dot` 取它当底色。**不再在模板里动态拼令牌名（`--shift-` + 色键）** —— 那种写法既走
// 内联 style，designGuard 的静态分析也看不见它（会被判成「引用了未定义的令牌」）。

async function moveShift(shift, delta) {
  const index = shifts.value.findIndex((item) => item.id === shift.id)
  // 重排要一次给全（服务端 `reorder_shifts` 的口径）：整表的新顺序由 util 算。
  const next = moveShiftOrder(orderIds.value, index, delta)
  if (!next || busy.value) return
  busyId.value = shift.id
  errorText.value = ''
  receipt.value = ''
  try {
    await api.put('/api/scheduling/shifts/order', { ids: next })
    receipt.value = '顺序调好了'
    await load()
  } catch (err) {
    await reportFailure(err, '没调成')
  } finally {
    if (busyId.value === shift.id) busyId.value = 0
  }
}

async function confirmDelete() {
  const target = deleteTarget.value
  deleteTarget.value = null
  // 确认框可能是上一个动作还在飞的时候打开的：按下去之前再挡一道。
  if (!target || busy.value) return
  busyId.value = target.id
  errorText.value = ''
  receipt.value = ''
  try {
    await api.delete(`/api/scheduling/shifts/${target.id}`)
    receipt.value = `删掉了：${target.name}`
    await load()
  } catch (err) {
    await reportFailure(err, '没删成')
  } finally {
    if (busyId.value === target.id) busyId.value = 0
  }
}

onMounted(() => {
  // 名字与清单里 `/workbench/hr/shifts` 那一行同步。原来这里是裸的 `'班次表'`，于是
  // 同一组的「排班月历」有「· 工作台」后缀、这一页没有（真机走查 N4）。
  document.title = workbenchDocumentTitle('班次表')
  load()
})
</script>

<template>
  <div class="hygiene-admin shifts-page">
    <header class="sTop">
      <p class="sKicker">人事 · 排班</p>
      <div class="sTitleRow">
        <h1>班次表</h1>
        <button class="btn sAction" type="button" @click="router.push(WORKBENCH_HR_HOME)">回到月历</button>
      </div>
      <p class="sSub">改动只影响以后：已经排好的班不动。</p>
    </header>

    <p v-if="state === 'loading'" class="sHint">正在读班次表…</p>

    <template v-else>
      <p v-if="errorText" class="sErr" role="alert">{{ errorText }}</p>
      <p v-else-if="receipt" class="sOk" role="status">{{ receipt }}</p>

      <section class="sCard">
        <div class="sCard-hd">
          <i class="sLed sig"></i>
          <h2>全部班次</h2>
          <span class="sCard-hint">{{ shifts.length }} 个 · 启用 {{ activeCount }}</span>
        </div>

        <div class="sAdd">
          <input
            v-model="newName"
            class="input"
            type="text"
            :maxlength="maxName || undefined"
            :placeholder="addPlaceholder"
            aria-label="新班次的名字"
            @keyup.enter="addShift"
          >
          <button
            class="btn btn-primary"
            type="button"
            :disabled="addBusy || !newName.trim()"
            @click="addShift"
          ><span class="sAdd-full">加一个班次</span><span class="sAdd-short">新建</span></button>
        </div>

        <p v-if="!shifts.length" class="sEmpty">一个班次都没有：先加一个，不然谁都没班可排。</p>
        <template v-else>
          <div class="sTh" aria-hidden="true">
            <span>#</span>
            <span>班次 / 在用</span>
            <span>状态</span>
            <span>卫生日常</span>
            <span class="sTh-act">操作</span>
          </div>
          <ul class="sList">
            <li v-for="(shift, index) in shifts" :key="shift.id" class="sRow" :class="{ off: !shift.is_active }">
              <span class="sOrd">{{ index + 1 }}</span>

              <template v-if="editingId === shift.id">
                <input
                  v-model="editName"
                  class="input sNameInput"
                  type="text"
                  :maxlength="maxName || undefined"
                  :aria-label="`改「${shift.name}」的名字`"
                  @keyup.enter="saveName(shift)"
                  @keyup.esc="cancelRename"
                >
                <span class="sActs">
                  <button class="btn btn-primary" type="button" :disabled="busy || !editName.trim()" @click="saveName(shift)">保存</button>
                  <button class="btn" type="button" @click="cancelRename">取消</button>
                </span>
              </template>

              <template v-else>
                <span class="sMain">
                  <b class="sName">{{ shift.name }}</b>
                  <span class="sUsage">{{ usageLine(shift) }}</span>
                  <!-- 班次色（0043）：8 档 + 中性。被别人占着的那档不可点，title 里点名谁在用；
                       停用的班次保留自己的色，所以它的色块也照常显示选中态。 -->
                  <span class="sSw" role="group" :aria-label="`「${shift.name}」的班次颜色`">
                    <button
                      v-for="choice in SHIFT_COLOR_CHOICES"
                      :key="choice.key"
                      type="button"
                      class="sSw-dot"
                      :class="[`tone-${choice.key}`, { on: (shift.color_key || SHIFT_COLOR_NEUTRAL) === choice.key }]"
                      :title="colorTitle(shift, choice.key)"
                      :aria-label="colorTitle(shift, choice.key)"
                      :disabled="busy || !!colorOwner(shifts, choice.key, shift.id)"
                      @click="setColor(shift, choice.key)"
                    />
                    <button
                      type="button"
                      class="sSw-dot sSw-neutral tone-neutral"
                      :class="{ on: (shift.color_key || SHIFT_COLOR_NEUTRAL) === SHIFT_COLOR_NEUTRAL }"
                      :title="colorTitle(shift, SHIFT_COLOR_NEUTRAL)"
                      aria-label="中性"
                      :disabled="busy"
                      @click="setColor(shift, SHIFT_COLOR_NEUTRAL)"
                    />
                  </span>
                </span>
                <span class="sTag" :class="{ off: !shift.is_active }">{{ statusText(shift) }}</span>
                <!-- 读和写是同一个控件：选中的那一条就是这条班次现在的档位（停用的也能改 ——
                     它的历史排班还要照这档交日常）。选项来自 util，模板里不写死档位说法。 -->
                <label class="sDuty">
                  <span class="sDutyLab">卫生日常</span>
                  <select
                    class="select sDutySel"
                    :value="dutySlotValue(shift)"
                    :disabled="busy"
                    @change="setDutySlot(shift, $event.target.value)"
                  >
                    <option v-for="choice in DUTY_SLOT_CHOICES" :key="choice.value" :value="choice.value">{{ choice.label }}</option>
                  </select>
                </label>
                <span class="sActs">
                  <button
                    class="btn sMini"
                    type="button"
                    title="上移一位"
                    :disabled="index === 0 || busy"
                    @click="moveShift(shift, -1)"
                  >↑</button>
                  <button
                    class="btn sMini"
                    type="button"
                    title="下移一位"
                    :disabled="index === shifts.length - 1 || busy"
                    @click="moveShift(shift, 1)"
                  >↓</button>
                  <button
                    class="btn sMini"
                    type="button"
                    :disabled="busy"
                    @click="startRename(shift)"
                  >改名</button>
                  <button
                    class="btn sMini"
                    type="button"
                    :disabled="busy"
                    @click="toggleActive(shift)"
                  >{{ toggleLabel(shift) }}</button>
                  <button
                    class="btn btn-danger sMini"
                    type="button"
                    :disabled="!canDelete(shift, activeCount) || busy"
                    :title="canDelete(shift, activeCount) ? '删掉这个班次' : deleteBlockedReason(shift, activeCount)"
                    @click="deleteTarget = shift"
                  >删除</button>
                </span>
              </template>

            </li>
          </ul>
          <div class="sNotes">
            <p
              v-for="blocked in shifts"
              v-show="!canDelete(blocked, activeCount)"
              :key="`note-${blocked.id}`"
              class="sWhy"
            >{{ blocked.name }}：{{ deleteBlockedReason(blocked, activeCount) }}</p>
          </div>
        </template>
      </section>

    </template>

    <ConfirmDialog
      v-if="deleteTarget"
      :title="`删除班次「${deleteTarget.name}」`"
      message="删掉之后不能再恢复。只是想让新排班不再用它的话，停用就够了 —— 历史排班照旧显示。"
      confirm-label="删除"
      danger
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<style scoped>
/* B 案 v2 版式（2026-10-17）：页头 kicker + H1 + 右侧读数胶囊；三块读数（mono 数字 + 5 段
   gauge）先摆出来；班次改成「表头 + 行列」结构（行高 44px、状态 = 6px LED + 小字）。
   面板仍是 1px 边框 + --r-2 + --hy-shadow-sm，配色全走语义令牌。 */
.shifts-page {
  display: flex; flex-direction: column; gap: var(--sp-4);
  padding: 18px clamp(16px, 4vw, 32px) calc(40px + var(--safe-b));
  width: 100%; max-width: 1052px; margin: 0 auto; font-family: var(--font-sans);
}
/* 页头 3 行网格：kicker / H1 + 主操作同基线 / 副标题。 */
.sTop {
  display: grid; grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "kick kick" "title action" "sub sub";
  align-items: end; column-gap: var(--sp-4); row-gap: 7px;
}
.sKicker {
  grid-area: kick; margin: 0; font-family: var(--font-sans); font-size: 9.5px; line-height: 1;
  font-weight: 500; letter-spacing: .26em; color: var(--ink-faint);
}
.sTitleRow {
  grid-area: title; display: flex; align-items: baseline; gap: var(--sp-4); min-width: 0;
}
.sTop h1 {
  margin: 0; font-family: var(--font-sans); font-size: 26px; line-height: 1;
  font-weight: 600; letter-spacing: .01em; color: var(--ink);
}
.sAction { margin-left: auto; flex: none; border-radius: var(--r-1); min-height: var(--tap); }
.sSub { grid-area: sub; margin: 0; font-size: var(--fs-12); line-height: 1.6; color: var(--ink-dim); }
.sLed { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; }
.sLed.ok { background: var(--ok); box-shadow: 0 0 0 3px var(--wb-ok-a16); }
.sLed.warn { background: var(--warn); box-shadow: 0 0 0 3px var(--wb-warn-a16); }
.sLed.sig { background: var(--action); box-shadow: 0 0 0 3px var(--wb-tint-14); }
.sHint { margin: 0; font-size: 12.5px; color: var(--ink-dim); }
.sErr {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--wb-danger-a45); background: var(--danger-soft);
  color: var(--danger-bright); font-size: 12.5px; line-height: 1.7;
}
.sOk {
  margin: 0; padding: 9px var(--sp-3); border-radius: var(--r-1);
  border: 1px solid var(--line-strong); background: var(--ok-soft);
  color: var(--ok); font-size: 12.5px; line-height: 1.7;
}
/* 面板（唯一卡片语言）。 */
.sCard {
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: var(--hy-shadow-sm); overflow: hidden;
}
.sCard-hd { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.sCard-hd h2 {
  margin: 0; font-family: var(--font-sans); font-size: 12.5px; line-height: 1;
  font-weight: 600; letter-spacing: .06em; color: var(--ink);
}
.sCard-hint { margin-left: auto; font-family: var(--font-mono); font-size: 9.5px; color: var(--ink-faint); }
.sAdd { display: flex; align-items: center; gap: var(--sp-2); padding: 12px 14px; border-bottom: 1px solid var(--line); }
.sAdd .input { flex: 0 1 320px; }
.sAdd .btn { flex: none; border-radius: var(--r-1); min-height: var(--tap); }
.sEmpty { margin: 0; padding: 14px; font-size: 12px; color: var(--ink-dim); }
/* 表头：小字 + 字距，跟着行的列走。 */
.sTh {
  display: grid; grid-template-columns: 28px minmax(0, 1fr) 64px 200px 300px;
  gap: 10px; padding: 7px 14px; background: var(--surface-2);
  border-bottom: 1px solid var(--line);
  font-family: var(--font-sans); font-size: 8.5px; line-height: 1;
  font-weight: 500; letter-spacing: .2em; color: var(--ink-faint);
}
.sTh-act { text-align: right; }
.sList { list-style: none; margin: 0; padding: 0; }
/* 一条班次 = 一行：# / 名称 · 在用 / 状态 / 卫生日常 / 操作。 */
.sRow {
  display: grid; grid-template-columns: 28px minmax(0, 1fr) 64px 200px 300px;
  gap: 10px; align-items: center; min-height: 56px; padding: 9px 14px;
  border-top: 1px solid var(--wb-ink-a08);
}
.sRow:first-child { border-top: 0; }
/* 班次色块（0043）：18px 方点，选中的那档套一圈 --ink 外环；被人占着的淡出且不可点。
   底色只从 token 来：`.tone-<键>` 把 `--shift-fill` 定成那一档，这里取它（中性档也走
   `.tone-neutral`，所以兜底值与它一致）。这里不写任何具体色值。 */
.sSw { display: flex; align-items: center; gap: 4px; margin-top: 5px; flex-wrap: wrap; }
.sSw-dot {
  width: 18px; height: 18px; padding: 0; border-radius: 5px;
  border: 1px solid var(--shift-edge); cursor: pointer; flex: none;
  background: var(--shift-fill, var(--shift-neutral));
}
.sSw-dot.on { box-shadow: 0 0 0 2px var(--ink); }
.sSw-dot:disabled { opacity: .3; cursor: not-allowed; }
.sSw-neutral { border-style: dashed; border-color: var(--line-strong); }
.sRow:hover { background: var(--surface-2); }
.sRow.off .sName { color: var(--ink-dim); }
.sOrd { text-align: center; font-family: var(--font-mono); font-size: 10px; color: var(--ink-faint); }
.sMain { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.sName { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.sUsage { font-family: var(--font-mono); font-size: 9.5px; line-height: 1.3; color: var(--ink-faint); }
.sNameInput { grid-column: 2 / 4; }
/* 状态 = 6px LED + 9px 小字（不再做胶囊底色）。 */
.sTag {
  display: inline-flex; align-items: center; gap: 5px; white-space: nowrap;
  font-size: 9px; letter-spacing: .06em; color: var(--ok);
}
.sTag::before { content: ""; width: 6px; height: 6px; flex: none; border-radius: 50%; background: currentColor; }
.sTag.off { color: var(--ink-faint); }
.sDuty { display: flex; align-items: center; gap: 6px; min-width: 0; }
.sDutyLab {
  flex: none; font-size: 8.5px; letter-spacing: .06em; color: var(--ink-faint);
}
.sDutySel {
  min-height: var(--tap); padding-top: 2px; padding-bottom: 2px;
  font-family: var(--font-sans); font-size: 11px; color: var(--ink);
  background: var(--surface-2); border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
}
.sActs { display: flex; gap: 4px; justify-content: flex-end; }
.sMini { min-height: var(--tap); padding: 0 10px; font-size: 12px; border-radius: var(--r-1); }
.sNotes { padding: 10px 14px 12px; border-top: 1px solid var(--line); }
.sWhy { margin: 0; font-size: 10px; line-height: 1.7; color: var(--ink-faint); }
.sWhy + .sWhy { margin-top: 2px; }
/* 手机（≤720）：一条班次 = 两行。
   行一：序号 · 名称/用量 · 状态；行二：卫生日常下拉（撑满）＋ 等宽动作条。
   原来这里是三行（名称 / 卫生日常 / 五个宽窄不一的按钮），两个班次就吃掉一整屏。 */
/* 桌面档才有长文案按钮（手机在断点里换成「新建」）。规则必须写在断点之前，
   否则同权重的 display:none 会把断点里的 display:inline 盖掉。 */
.sAdd-short { display: none; }
@media (max-width: 720px) {
  .sAdd-full { display: none; }
  .sAdd-short { display: inline; }
  .sTop { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "kick kick" "title action" "sub sub"; }
  .sTop h1 { font-size: 22px; }
  .sAction { padding: 0 12px; font-size: 12px; }
  .sSub { font-size: 11px; }
  .sTh { display: none; }

  .sCard-hd { padding: 10px 12px; }
  .sAdd { padding: 10px 12px; gap: 6px; }
  .sAdd .input { flex: 1 1 auto; min-width: 0; }
  .sAdd .btn { flex: none; padding: 0 12px; }

  /* 两行网格：第一行（序号 / 名称 / 状态），第二行（卫生日常，跨满）。 */
  .sRow {
    grid-template-columns: 22px minmax(0, 1fr) auto;
    grid-template-areas:
      "ord main tag"
      "duty duty duty"
      "acts acts acts";
    row-gap: 10px; column-gap: 8px;
    padding: 12px;
  }
  .sOrd { grid-area: ord; align-self: start; padding-top: 3px; font-size: 11px; }
  .sMain { grid-area: main; gap: 4px; }
  .sName { font-size: 14px; }
  .sUsage { font-size: 10.5px; }
  .sTag { grid-area: tag; align-self: start; padding-top: 2px; font-size: 9.5px; }
  .sDuty {
    grid-area: duty;
    display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center;
    gap: 8px; padding-top: 2px;
  }
  .sDutyLab { font-size: 9px; letter-spacing: .1em; }
  .sDutySel { width: 100%; font-size: 12.5px; }
  /* 动作条：五个等宽格。删除仍然是最后一格（danger 边框），等宽后不再紧贴「停用」。 */
  .sActs {
    grid-area: acts;
    display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 6px; justify-content: stretch;
  }
  /* 全局在 ≤430px 把 `.btn` 钉成 40px（theme.css 的 !important），这里要的是 44。 */
  .sMini { padding: 0; font-size: 12.5px; min-height: 44px !important; }
  .sAdd .btn { min-height: 44px !important; }
  .sNotes { padding: 8px 12px 10px; }
  .sWhy { font-size: 10px; line-height: 1.6; }
  .sNameInput { grid-area: main / main / duty / duty; }
  .sRow > .sNameInput + .sActs { grid-area: acts; }
}

</style>
