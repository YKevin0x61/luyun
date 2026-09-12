<script setup>
import { onMounted, ref } from 'vue'
import HygieneReviewPair from '../../components/hygiene/HygieneReviewPair.vue'
import { api } from '../../api/client'
import { teachingShotUrl } from '../../utils/hygieneMarkup'

const boards = ref({ week_start: '', people: [], zones: [] })
const teaching = ref([])
const loading = ref(true)
const errorText = ref('')
const selected = ref(null)

onMounted(refreshPage)

function weekLabel(iso) {
  const raw = String(iso || '')
  const matched = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(raw)
  return matched ? `${matched[1]} ${matched[2]}` : raw
}

function personLabel(row) {
  return row.phone || `员工 ${row.employee_id}`
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
        <h2>红黑榜与卫生教材</h2>
        <p>两张榜只记次数：逾期、驳回、一次通过、实拍。按周一 06:00 切周，不是零点。所有登录员工都能看。教材要超级管理员从已通过的对照里手点，合格图不会自动进库。</p>
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
            :standard-src="teachingShotUrl('admin', selected, 'left')"
            :standard-markup="selected.left_markup || []"
            :standard-alt="selected.left_label"
            :capture-src="teachingShotUrl('admin', selected, 'right')"
            :capture-alt="selected.right_label"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.boards-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 1180px;
}
.roster-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
}
.roster-head h2 { margin: 0 0 6px; font-size: 16px; }
.roster-head p {
  margin: 0;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
  max-width: 56em;
}
.week-line {
  margin: 0;
  color: var(--text-dim);
  font-size: 13px;
}
.roster-error {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 13px;
}
.roster-empty {
  padding: 28px 16px;
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
}
.boards-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  align-items: start;
}
@media (max-width: 900px) {
  .boards-grid { grid-template-columns: 1fr; }
}
.count-list {
  list-style: none;
  margin: 0;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.count-row, .queue-btn {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  background: var(--card2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 8px;
  padding: 10px 12px;
  font-family: inherit;
}
.queue-btn { cursor: pointer; }
.queue-btn.active { border-color: var(--accent); }
.count-row span, .queue-btn span { color: var(--text-dim); font-size: 12px; }
.editor-lead {
  margin: 0;
  padding: 0 12px 8px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.6;
}
.teaching-grid {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 12px;
  align-items: start;
}
@media (max-width: 900px) {
  .teaching-grid { grid-template-columns: 1fr; }
}
.review-body { padding: 12px; }
</style>
