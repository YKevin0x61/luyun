<script setup>
import { onMounted, ref } from 'vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { teachingShotUrl } from '../../utils/hygieneMarkup'
import { formatHygieneStamp } from '../../utils/hygieneTime'

const boards = ref({ week_start: '', people: [], zones: [] })
const teaching = ref([])
const loading = ref(true)
const errorText = ref('')
const selected = ref(null)

onMounted(refreshPage)

useHygieneRealtime({
  id: 'hygiene-admin-boards',
  resources: ['boards', 'teaching'],
  pull: refreshPage,
})

function weekLabel(iso) {
  return formatHygieneStamp(iso)
}

function personLabel(row) {
  return row.name || row.phone || `员工 ${row.employee_id}`
}

async function refreshPage() {
  loading.value = true
  errorText.value = ''
  try {
    const [boardData, teachingData] = await Promise.all([
      api.get('/api/hygiene/admin/boards'),
      api.get('/api/hygiene/admin/teaching'),
    ])
    boards.value = boardData
    teaching.value = teachingData.items || []
  } catch (err) {
    errorText.value = err.message || '无法加载红黑榜'
  } finally {
    loading.value = false
  }
}

function openTeaching(row) {
  selected.value = row
}
</script>

<template>
  <div class="boards-page">
    <header class="wbx-head">
      <div class="wbx-head-main">
        <p class="wbx-kicker">BOARDS · 周次公示</p>
        <h1 class="wbx-title">红黑榜与卫生教材</h1>
      </div>
      <div class="wbx-pills">
        <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
      </div>
      <p class="wbx-sub">按本周次数公示，不折算评分。</p>
      <details class="rule-help">
        <summary>规则说明</summary>
        <p>两张榜只记录逾期、驳回、一次通过和实拍次数，按周一 06:00 切周。所有登录员工都能查看。卫生教材由超级管理员从已通过的对照中手动标记。换区指员工当天自己把工作区从 A 改成 B 的次数：换区本身允许（临时换岗），列出来是为了让实拍数字可解释，不是扣分项。</p>
      </details>
    </header>

    <p v-if="errorText" class="wbx-error" role="alert">{{ errorText }}</p>
    <p class="week-line">本周 {{ weekLabel(boards.week_start) }} 起</p>

    <div class="wbx-cols">
      <section class="wbx-panel">
        <header class="wbx-ph">
          <i class="led ok"></i>
          <h2>人的红黑榜</h2>
          <span class="wbx-hint">{{ boards.people.length }} 人</span>
        </header>
        <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
        <div v-else-if="errorText" class="wbx-empty-bar">加载失败，点上方「刷新」重试。</div>
        <div v-else-if="!boards.people.length" class="wbx-empty-bar">这一周还没有人的次数。</div>
        <ul v-else class="wbx-rows">
          <li v-for="row in boards.people" :key="row.employee_id" class="wbx-row is-person">
            <div class="wbx-row-main">
              <strong>{{ personLabel(row) }}</strong>
              <span class="wbx-row-sub">实拍 {{ row['实拍'] }} · 驳回 {{ row['驳回'] }} · 一次通过 {{ row['一次通过'] }} · 逾期 {{ row['逾期'] }}</span>
            </div>
            <span v-if="row['换区']" class="wbx-state is-warn"><i class="led warn"></i>换区 {{ row['换区'] }}</span>
            <span v-else class="wbx-state is-ok"><i class="led ok"></i>无换区</span>
          </li>
        </ul>
      </section>

      <section class="wbx-panel">
        <header class="wbx-ph">
          <i class="led warn"></i>
          <h2>卫生工作区红黑榜</h2>
          <span class="wbx-hint">{{ boards.zones.length }} 区</span>
        </header>
        <div v-if="loading" class="wbx-empty-bar">正在加载…</div>
        <div v-else-if="errorText" class="wbx-empty-bar">加载失败，点上方「刷新」重试。</div>
        <div v-else-if="!boards.zones.length" class="wbx-empty-bar">这一周还没有卫生工作区的次数。</div>
        <ul v-else class="wbx-rows">
          <li v-for="row in boards.zones" :key="row.zone_id" class="wbx-row is-zone">
            <div class="wbx-row-main">
              <strong>{{ row.zone_name }}</strong>
              <span class="wbx-row-sub">逾期 {{ row['逾期'] }}</span>
            </div>
            <span class="wbx-state" :class="row['逾期'] ? 'is-bad' : 'is-ok'">
              <i class="led" :class="row['逾期'] ? 'bad' : 'ok'"></i>
              {{ row['逾期'] ? '有逾期' : '正常' }}
            </span>
          </li>
        </ul>
      </section>
    </div>

    <section v-if="teaching.length" class="wbx-panel">
      <header class="wbx-ph">
        <i class="led sig"></i>
        <h2>卫生教材</h2>
        <span class="wbx-hint">{{ teaching.length }} 例</span>
      </header>
      <p class="wbx-lead">从日常验收或专项前后对照点「标为卫生教材」。这里只列出已经手点过的例子。</p>
      <div class="wbx-teach">
        <ul class="wbx-rows">
          <li v-for="row in teaching" :key="row.id" class="wbx-row is-teach">
            <button
              type="button"
              class="wbx-tbtn"
              :class="{ 'is-on': selected && selected.id === row.id }"
              @click="openTeaching(row)"
            >
              <strong>{{ row.title }}</strong>
              <span>{{ row.left_label }} / {{ row.right_label }}</span>
            </button>
          </li>
        </ul>
        <div v-if="selected" class="wbx-review">
          <HygieneReviewPair
            :left-label="selected.left_label"
            :right-label="selected.right_label"
            :standard-src="teachingShotUrl('admin', selected, 'left', 'preview')"
            :original-standard-src="teachingShotUrl('admin', selected, 'left')"
            :standard-markup="selected.left_markup || []"
            :standard-alt="selected.left_label"
            :capture-src="teachingShotUrl('admin', selected, 'right', 'preview')"
            :original-capture-src="teachingShotUrl('admin', selected, 'right')"
            :capture-alt="selected.right_label"
          />
        </div>
      </div>
    </section>
    <p v-else-if="!loading" class="wbx-empty-bar">还没有卫生教材。店长在验收时点「标为卫生教材」就会出现在这里。</p>
  </div>
</template>

<style scoped>
/* B 案 v2 + v3 布局纪律：页头右手只留刷新；两榜严格 1:1、底边对齐；
   教材空态收成横条；行高 56–64。 */
.boards-page {
  display: flex; flex-direction: column; gap: 14px;
  padding-bottom: calc(40px + var(--safe-b));
}
.wbx-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 16px; align-items: end; }
.wbx-head-main { grid-column: 1; grid-row: 1; }
.wbx-kicker { margin: 0 0 7px; font: 500 9.5px/1 var(--font-sans); letter-spacing: .26em; color: var(--ink-faint); }
.wbx-title { margin: 0; font: 600 26px/1.1 var(--font-sans); letter-spacing: .01em; color: var(--ink); }
.wbx-pills { grid-column: 2; grid-row: 1; justify-self: end; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wbx-sub { grid-column: 1 / -1; grid-row: 2; margin: 8px 0 0; font-size: 11.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-head .rule-help { grid-column: 1 / -1; grid-row: 3; margin-top: 6px; }
.wbx-error { margin: 0; font-size: 11px; color: var(--danger-bright); }
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.ok { background: var(--ok); }
.led.warn { background: var(--warn); }
.led.bad { background: var(--danger); }
.led.sig { background: var(--action); }
.week-line {
  margin: 0; display: flex; align-items: center; gap: 12px;
  font: 500 9.5px/1 var(--font-sans); letter-spacing: .22em; text-transform: uppercase; color: var(--ink-faint);
}
.week-line::before, .week-line::after { content: ''; flex: 1; height: 1px; background: var(--line); }

.wbx-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: stretch; }
.wbx-panel {
  display: flex; flex-direction: column;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08); overflow: hidden;
}
.wbx-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbx-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-empty-bar {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
  background: var(--surface-1); border: 1px dashed var(--line-strong); border-radius: var(--r-1);
}
.wbx-panel .wbx-empty-bar { border: 0; border-radius: 0; background: transparent; min-height: 52px; }
.wbx-rows { list-style: none; margin: 0; padding: 0; flex: 1 1 auto; }
.wbx-row {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px;
  min-height: 56px; padding: 8px 14px; border-bottom: 1px solid var(--wb-ink-a08);
}
.wbx-row:last-child { border-bottom: 0; }
.wbx-row:hover { background: var(--surface-2); }
.wbx-row-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.wbx-row-main strong { font-size: 12px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wbx-row-sub { font: 500 10px/1.5 var(--font-mono); color: var(--ink-faint); }
.wbx-state { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; letter-spacing: .06em; color: var(--ink-dim); white-space: nowrap; }
.wbx-state.is-warn { color: var(--warn); }
.wbx-state.is-ok { color: var(--ok); }
.wbx-state.is-bad { color: var(--danger-bright); }

.wbx-lead { margin: 0; padding: 10px 14px 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-dim); }
.wbx-teach { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 14px; padding: 10px 14px 14px; align-items: start; }
.wbx-tbtn {
  display: flex; flex-direction: column; gap: 4px; width: 100%; min-height: 48px;
  padding: 9px 12px; text-align: left; cursor: pointer;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-1); color: var(--ink);
}
.wbx-tbtn strong { font-size: 11.5px; font-weight: 600; }
.wbx-tbtn span { font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-tbtn.is-on { border-color: var(--action); box-shadow: inset 3px 0 0 var(--action); }
.wbx-review { min-width: 0; }
.wbx-teach .wbx-rows { padding: 0; }
.wbx-teach .wbx-row.is-teach { grid-template-columns: 1fr; padding: 5px 0; border-bottom: 0; min-height: 0; }
.boards-page :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }
@media (max-width: 900px) {
  .wbx-cols { grid-template-columns: 1fr; }
  .wbx-teach { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .wbx-head { grid-template-columns: minmax(0, 1fr); }
  .wbx-pills { grid-column: 1; grid-row: 2; justify-self: start; margin-top: 10px; }
  .wbx-sub { grid-row: 3; }
  .wbx-head .rule-help { grid-row: 4; }
  .wbx-title { font-size: 22px; }
}
</style>
