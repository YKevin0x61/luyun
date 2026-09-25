<script setup>
// 店长端「排班 · 班次表」（票 11）：加一个班次、改名字、调显示顺序、启用停用、
// 把刚建错的那一条删掉 —— 都不用改库。
//
// 两件事在这一页上说清楚：
// - **停用不等于删除**：停用之后新排班不再用它，**已经写下的历史排班照旧显示**那个
//   班次（服务层的 `_shifts_for_display` 保证）。所以「停用」是常规动作，「删除」只
//   留给一天班都没排过的那一条 —— 按钮该灰就灰，原因写在旁边。
// - **还有人在上就不给停用**：说清是多少个人、让他们先改规则。那个数字来自服务端
//   （`/shifts/manage` 的 `people`），页面不自己数 —— 数错了店长会照着错的信息做决定。
//
// 班次是**数据**不是常量：这一页按 N 个班次写，加第三个班次之后月历图例、配规则的下拉、
// 员工端那张卡都会自己多出一种班别，不需要改代码（票面的验收项）。
//
// 视觉沿用 `public/hygiene-admin.css` 的深青墨令牌 —— 那是**共享的样式表**，不是卫生
// 模块：排班不 import 卫生的 Python 模块、不挂它的菜单，只是同一套验收台配色。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import { api } from '../../api/client'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import {
  canDelete,
  deleteBlockedReason,
  moveShift as moveShiftOrder,
  statusText,
  toggleLabel,
  usageLine,
} from '../../utils/shiftTable'

useScopedStylesheet('/hygiene-admin.css')

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
  if (!name || busyId.value) return
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
    busyId.value = 0
  }
}

async function toggleActive(shift) {
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
    // 服务端把「还有几个人在轮转里排着它」说全了，原话转给店长（`reportFailure`
    // 保证重读之后那句话还在）。
    await reportFailure(err, '没改成')
  } finally {
    busyId.value = 0
  }
}

async function moveShift(shift, delta) {
  const index = shifts.value.findIndex((item) => item.id === shift.id)
  // 重排要一次给全（服务端 `reorder_shifts` 的口径）：整表的新顺序由 util 算。
  const next = moveShiftOrder(orderIds.value, index, delta)
  if (!next || busyId.value) return
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
    busyId.value = 0
  }
}

async function confirmDelete() {
  const target = deleteTarget.value
  deleteTarget.value = null
  if (!target) return
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
    busyId.value = 0
  }
}

onMounted(() => {
  document.title = '班次表'
  load()
})
</script>

<template>
  <div class="hygiene-admin shifts-page">
    <header class="sTop">
      <div>
        <p class="sKicker">排班</p>
        <h1>班次表</h1>
        <p class="sSub">
          这里改的是数据：加一个班次、改个名字、调个位置、停用一条。批过的排班不会跟着变
          —— 停用只是让新排班不再用它。
        </p>
      </div>
      <button class="btn" type="button" @click="router.push('/scheduling')">回到月历</button>
    </header>

    <p v-if="state === 'loading'" class="sHint">正在读班次表…</p>

    <template v-else>
      <p v-if="errorText" class="sErr" role="alert">{{ errorText }}</p>
      <p v-else-if="receipt" class="sOk" role="status">{{ receipt }}</p>

      <section class="sCard">
        <div class="sCard-hd">
          <h2>全部班次</h2>
          <span class="sCount">{{ shifts.length }}</span>
        </div>
        <p class="sLead">
          顺序就是月历图例、当天名单、员工卡片上的顺序（↑ ↓ 调）。停用的班次留在这里：
          它的历史排班还要照着它显示。
        </p>

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
          >加一个班次</button>
        </div>

        <p v-if="!shifts.length" class="sEmpty">一个班次都没有：先加一个，不然谁都没班可排。</p>
        <ul v-else class="sList">
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
                <button class="btn btn-primary" type="button" :disabled="busyId === shift.id || !editName.trim()" @click="saveName(shift)">保存</button>
                <button class="btn" type="button" @click="cancelRename">取消</button>
              </span>
            </template>

            <template v-else>
              <span class="sName">{{ shift.name }}</span>
              <span class="sTag" :class="{ off: !shift.is_active }">{{ statusText(shift) }}</span>
              <span class="sUsage">{{ usageLine(shift) }}</span>
              <span class="sActs">
                <button
                  class="btn sMini"
                  type="button"
                  title="上移一位"
                  :disabled="index === 0 || busyId === shift.id"
                  @click="moveShift(shift, -1)"
                >↑</button>
                <button
                  class="btn sMini"
                  type="button"
                  title="下移一位"
                  :disabled="index === shifts.length - 1 || busyId === shift.id"
                  @click="moveShift(shift, 1)"
                >↓</button>
                <button
                  class="btn sMini"
                  type="button"
                  :disabled="busyId === shift.id"
                  @click="startRename(shift)"
                >改名</button>
                <button
                  class="btn sMini"
                  type="button"
                  :disabled="busyId === shift.id"
                  @click="toggleActive(shift)"
                >{{ toggleLabel(shift) }}</button>
                <button
                  class="btn btn-danger sMini"
                  type="button"
                  :disabled="!canDelete(shift, activeCount) || busyId === shift.id"
                  :title="canDelete(shift, activeCount) ? '删掉这个班次' : deleteBlockedReason(shift, activeCount)"
                  @click="deleteTarget = shift"
                >删除</button>
              </span>
            </template>

            <span v-if="!canDelete(shift, activeCount)" class="sWhy">{{ deleteBlockedReason(shift, activeCount) }}</span>
          </li>
        </ul>
      </section>

      <section class="sCard">
        <div class="sCard-hd">
          <h2>停用和删除的区别</h2>
        </div>
        <p class="sLead">
          停用：新排班不再用它（规则里挑不到，展开也不再写），已经写下的那些天照旧显示它。
          还有人的轮转里排着它时会拦下来，并告诉你有多少个人 —— 先把他们的规则改掉。
        </p>
        <p class="sLead">
          删除：只给一天班都没排过、也没人在用的那一条（建错了的）。排过班的删不掉 ——
          删了那些天在月历上就找不到班次了；最后一个在用的班次也删不掉（删完谁都没班可排），
          那种情况先加一个或启用一个别的。
        </p>
      </section>
    </template>

    <ConfirmDialog
      v-if="deleteTarget"
      :title="`删除班次「${deleteTarget.name}」`"
      message="这个班次一天班都没排过、也没人在用，删掉之后不能再恢复。只是想让新排班不再用它的话，停用就够了 —— 历史排班照旧显示。"
      confirm-label="删除"
      danger
      @confirm="confirmDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<style scoped>
/* 版式照待办页（票 08/09）：深青墨底 + 一张卡一个话题。令牌来自 /hygiene-admin.css。 */
.shifts-page { padding: 18px 16px 40px; max-width: 780px; margin: 0 auto; }
.sTop { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 14px; }
.sTop h1 { margin: 2px 0 4px; font-size: 20px; color: var(--hy-ink); }
.sKicker { margin: 0; font-size: 11px; letter-spacing: .18em; color: var(--hy-faint); }
.sSub { margin: 0; font-size: 12px; line-height: 1.7; color: var(--hy-muted); }
.sTop .btn { margin-left: auto; flex: none; }
.sHint { margin: 0; font-size: 12.5px; color: var(--hy-muted); }
.sErr {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid rgba(255, 138, 128, .35); background: rgba(255, 138, 128, .12);
  color: var(--hy-coral); font-size: 12.5px; line-height: 1.7;
}
.sOk {
  margin: 0 0 12px; padding: 9px 12px; border-radius: var(--hy-radius-md);
  border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
  color: var(--hy-mint); font-size: 12.5px; line-height: 1.7;
}
.sCard {
  background: var(--hy-surface-2); border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg); padding: 14px 14px 12px;
}
.sCard + .sCard { margin-top: 12px; }
.sCard-hd { display: flex; align-items: center; gap: 8px; }
.sCard-hd h2 { margin: 0; font-size: 14px; color: var(--hy-ink); }
.sCount {
  margin-left: auto; font-family: var(--font-mono); font-size: 12px; color: var(--hy-faint);
}
.sLead { margin: 8px 0 0; font-size: 12px; line-height: 1.8; color: var(--hy-muted); }
.sEmpty { margin: 10px 0 0; font-size: 12.5px; color: var(--hy-muted); }
.sAdd { display: flex; gap: 8px; margin-top: 10px; }
.sAdd .input { flex: 1; }
.sAdd .btn { flex: none; }
.sList { list-style: none; margin: 10px 0 0; padding: 0; }
.sRow {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 9px 0; border-top: 1px solid var(--hy-line);
}
.sRow:first-child { border-top: 0; }
.sRow.off .sName { color: var(--hy-muted); }
.sOrd {
  width: 20px; flex: none; text-align: center; font-family: var(--font-mono);
  font-size: 11.5px; color: var(--hy-faint);
}
.sName { font-size: 13.5px; color: var(--hy-ink); }
.sNameInput { flex: 1; min-width: 120px; }
.sTag {
  padding: 1px 8px; border-radius: 999px; font-size: 11px;
  color: var(--hy-mint); border: 1px solid var(--hy-mint-line); background: var(--hy-mint-soft);
}
.sTag.off {
  color: var(--hy-faint); border-color: var(--hy-line-strong); background: transparent;
}
.sUsage { font-size: 11.5px; color: var(--hy-muted); }
.sActs { margin-left: auto; display: flex; gap: 6px; flex: none; }
.sMini { min-height: 30px; padding: 0 10px; font-size: 12px; }
.sWhy { flex-basis: 100%; font-size: 11px; line-height: 1.7; color: var(--hy-faint); }
</style>
