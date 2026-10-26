<script setup>
/**
 * 销售报表里的「换算规则」只读面。
 *
 * 维护在「后勤 → 换算规则」（`/workbench/kitchen/prep-rules`）。这里只回答「这道菜用多少料」，
 * 不给改：改系数要动备货建议量，那是维护页的事，报表页顺手改容易改错。
 *
 * 数据复用 `/api/semi-rules/board`（同一份分组口径：外卖变体折进同一道菜、多料并排），
 * 但 UI 用报表页自己的表格样式 —— 维护页那套是工作台主题的 token，塞进后台页面会掉色。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'

const loading = ref(true)
const errorText = ref('')
const groups = ref([])
const search = ref('')

const filteredGroups = computed(() => {
  const kw = search.value.trim().toLowerCase()
  if (!kw) return groups.value
  return groups.value.filter((g) => {
    const hay = [g.main_name, ...(g.variants || []).map((v) => `${v.dish_name} ${v.prep_item_name || v.semi_name}`)]
      .join(' ')
      .toLowerCase()
    return hay.includes(kw)
  })
})

/** 一行 = 一条规则（多料 / 多渠道各占一行），跟维护页的分组口径一致 */
const rows = computed(() => {
  const out = []
  for (const g of filteredGroups.value) {
    for (const v of g.variants || []) {
      out.push({ ...v, main_name: g.main_name, station: g.station })
    }
  }
  return out
})

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    const data = await api.get('/api/semi-rules/board')
    if (data.success) {
      groups.value = (data.groups || []).filter((g) => g.has_rules)
    }
  } catch (e) {
    errorText.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="rr">
    <div class="rr-bar">
      <input v-model="search" class="rr-search" placeholder="搜菜品 / 备货品…" aria-label="搜索菜品或备货品">
      <span class="rr-count">{{ rows.length }} 条规则 · {{ filteredGroups.length }} 道菜</span>
      <span class="rr-hint">只在「后勤 → 换算规则」里改</span>
    </div>

    <p v-if="loading" class="rr-empty">加载中…</p>
    <p v-else-if="errorText" class="rr-empty">{{ errorText }}</p>
    <p v-else-if="!rows.length" class="rr-empty">没有匹配的规则</p>
    <div v-else class="rr-wrap">
      <table class="rr-table">
        <thead>
          <tr>
            <th>菜品</th>
            <th>备货品</th>
            <th>系数</th>
            <th>单位</th>
            <th>岗位</th>
            <th>渠道</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>
              {{ r.main_name }}
              <em v-if="r.dish_name !== r.main_name" class="rr-var">{{ r.dish_name }}</em>
            </td>
            <td>{{ r.prep_item_name || r.semi_name }}</td>
            <td class="rr-fac">× {{ r.factor }}</td>
            <td>{{ r.unit || '—' }}</td>
            <td>{{ r.prep_item_position || r.position || '—' }}</td>
            <td class="rr-ch">{{ r.is_takeout ? '外卖' : '堂食' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.rr { display: flex; flex-direction: column; gap: 10px; }
.rr-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.rr-search {
  width: 260px;
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 7px 11px;
  color: var(--text);
  font-size: 12.5px;
  font-family: inherit;
  outline: none;
}
.rr-search::placeholder { color: var(--text-dim); }
.rr-count { font-size: 12px; color: var(--text-dim); }
.rr-hint { font-size: 11px; color: var(--text-dim); margin-left: auto; }
.rr-empty { padding: 24px 0; font-size: 12px; color: var(--text-dim); }
.rr-wrap {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: auto;
  max-height: 560px;
}
.rr-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.rr-table thead th {
  position: sticky;
  top: 0;
  background: var(--card2);
  color: var(--text-dim);
  font-weight: 500;
  font-size: 11.5px;
  text-align: left;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.rr-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}
.rr-table tbody tr:last-child td { border-bottom: 0; }
.rr-table tbody tr:hover td { background: var(--card2); }
.rr-fac { color: var(--accent); font-variant-numeric: tabular-nums; white-space: nowrap; }
.rr-ch { color: var(--text-dim); font-size: 11.5px; }
.rr-var { display: block; font-style: normal; font-size: 10.5px; color: var(--text-dim); margin-top: 1px; }
</style>
