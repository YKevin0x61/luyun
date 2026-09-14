<script setup>
import { onMounted, ref } from 'vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import { api } from '../../api/client'
import { useHygieneRealtime } from '../../composables/useHygieneRealtime'
import { teachingShotUrl } from '../../utils/hygieneMarkup'

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
  const raw = String(iso || '')
  const matched = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw)
  return matched ? `${matched[1]} ${matched[2]}` : raw
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
    <div class="card roster-head">
      <div>
        <p class="hy-eyebrow">Boards · 周次公示</p>
        <h1>红黑榜与卫生教材</h1>
        <p>按本周次数公示，不折算评分。</p>
        <details class="rule-help">
          <summary>规则说明</summary>
          <p>两张榜只记录逾期、驳回、一次通过和实拍次数，按周一 06:00 切周。所有登录员工都能查看。卫生教材由超级管理员从已通过的对照中手动标记。</p>
        </details>
      </div>
      <button type="button" class="btn" :disabled="loading" @click="refreshPage">刷新</button>
    </div>

    <p v-if="errorText" class="roster-error" role="alert">{{ errorText }}</p>
    <p class="week-line">本周 {{ weekLabel(boards.week_start) }} 起</p>

    <div class="boards-grid">
      <div class="table-card">
        <div class="table-card-header">
          <h3>人的红黑榜 <span>{{ boards.people.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!boards.people.length" class="roster-empty">这一周还没有人的次数。</div>
        <ul v-else class="count-list">
          <li v-for="row in boards.people" :key="row.employee_id" class="count-row">
            <strong>{{ personLabel(row) }}</strong>
            <span>实拍 {{ row['实拍'] }} · 驳回 {{ row['驳回'] }} · 一次通过 {{ row['一次通过'] }} · 逾期 {{ row['逾期'] }}</span>
          </li>
        </ul>
      </div>

      <div class="table-card">
        <div class="table-card-header">
          <h3>卫生责任区红黑榜 <span>{{ boards.zones.length }}</span></h3>
        </div>
        <div v-if="loading" class="roster-empty">正在加载…</div>
        <div v-else-if="!boards.zones.length" class="roster-empty">这一周还没有卫生责任区的次数。</div>
        <ul v-else class="count-list">
          <li v-for="row in boards.zones" :key="row.zone_id" class="count-row">
            <strong>{{ row.zone_name }}</strong>
            <span>逾期 {{ row['逾期'] }}</span>
          </li>
        </ul>
      </div>
    </div>

    <div class="table-card teaching-card">
      <div class="table-card-header">
        <h3>卫生教材 <span>{{ teaching.length }}</span></h3>
      </div>
      <p class="editor-lead">从日常验收或专项前后对照点「标为卫生教材」。这里只列出已经手点过的例子。</p>
      <div v-if="!teaching.length" class="roster-empty">还没有卫生教材。</div>
      <div v-else class="teaching-grid">
        <ul class="count-list">
          <li v-for="row in teaching" :key="row.id">
            <button
              type="button"
              class="queue-btn"
              :class="{ active: selected && selected.id === row.id }"
              @click="openTeaching(row)"
            >
              <strong>{{ row.title }}</strong>
              <span>{{ row.left_label }} / {{ row.right_label }}</span>
            </button>
          </li>
        </ul>
        <div v-if="selected" class="review-body">
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
    </div>
  </div>
</template>

<style scoped>
.week-line {
  margin: 2px 0;
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--hy-faint);
  font-size: .72rem;
  letter-spacing: .18em;
  text-transform: uppercase;
}
.week-line::before,
.week-line::after {
  content: '';
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--hy-line), transparent);
}
.boards-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  align-items: start;
}
@media (max-width: 900px) {
  .boards-grid { grid-template-columns: 1fr; }
}
.count-list {
  list-style: none;
  margin: 0;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.teaching-grid {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}
@media (max-width: 900px) {
  .teaching-grid { grid-template-columns: 1fr; }
}
</style>
