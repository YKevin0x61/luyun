<script setup>
import { computed } from 'vue'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  total: { type: Number, default: 0 },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 50 },
  summary: { type: Object, default: null },
  // '' = 全部；'co' = 只看退单；'present' = 只看赠单。
  kind: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['page-change', 'kind-change'])

const TABS = [
  { value: '', label: '全部' },
  { value: 'co', label: '退单' },
  { value: 'present', label: '赠单' },
]

// 顶部两个汇总块分别来自 summary.co / summary.present。
const coSummary = computed(() => props.summary?.co || {})
const presentSummary = computed(() => props.summary?.present || {})

const pageCount = computed(() => Math.max(1, Math.ceil((props.total || 0) / props.pageSize)))

// 接口返回的 coStatus 只用于展示，不做筛选：1 在服务端没有附加条件（等于全部退单），
// 2/3/4 才是真正区分出来的阶段，0 是没做分类的历史数据。
const CO_STATUS_LABELS = {
  0: '退单',
  1: '预结后',
  2: '传菜后',
  3: '结算后',
  4: '返位后',
}

function statusLabel(item) {
  // 赠单没有「预结后 / 传菜后」这类阶段，类型列已经写明，这里留空。
  if (item.kind === 'present') return '-'
  return CO_STATUS_LABELS[Number(item.co_status) || 0] || '退单'
}

function kindLabel(item) {
  return item.kind === 'present' ? '赠单' : '退单'
}

// refund_time 是 `2026-11-13T19:08:45+08:00`。同一天里只显示时分秒，跨天补月日。
function formatTime(value) {
  const text = String(value || '')
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/)
  if (!match) return text || '-'
  return `${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]}`
}

function money(value) {
  return Number(value || 0).toFixed(2)
}

// 窄屏只显示时分秒（当天绝大多数行同一天），完整时间放 title。
function shortTime(value) {
  const text = String(value || '')
  const idx = text.indexOf('T')
  return idx >= 0 ? text.slice(idx + 1, idx + 9) : text.slice(-8)
}

function changePage(next) {
  const target = Math.min(Math.max(1, next), pageCount.value)
  if (target !== props.page) emit('page-change', target)
}
</script>

<template>
  <div class="table-card refund-table">
    <div class="table-card-header">
      <h3>
        <SvgIcon name="receipt" :size="15" /> 异常明细
        <span class="refund-source">来源：商龙异常汇总（约 5 分钟同步）</span>
      </h3>
      <div class="refund-summary">
        <div class="refund-tabs">
          <button
            v-for="tab in TABS"
            :key="tab.value"
            class="btn btn-sm"
            :class="{ 'btn-primary': kind === tab.value }"
            @click="emit('kind-change', tab.value)"
          >{{ tab.label }}</button>
        </div>
        <template v-if="summary">
          <span class="badge">
            退单 {{ coSummary.bill_count ?? 0 }} 单 / {{ coSummary.line_count ?? 0 }} 行 ·
            {{ Number(coSummary.total_qty || 0) }} 份 · ¥{{ money(coSummary.total_money) }}
          </span>
          <span class="badge">
            赠单 {{ presentSummary.bill_count ?? 0 }} 单 / {{ presentSummary.line_count ?? 0 }} 行 ·
            {{ Number(presentSummary.total_qty || 0) }} 份 · ¥{{ money(presentSummary.total_money) }}
          </span>
        </template>
        <span v-else class="badge">汇总加载失败</span>
      </div>
    </div>

    <div class="refund-table-body luyun-scrollbar">
      <table class="data-table">
        <thead>
          <tr>
            <th>类型</th>
            <th>时间</th>
            <th>桌台</th>
            <th class="col-bs">单号</th>
            <th>菜品</th>
            <th class="num">份数</th>
            <th class="num">金额</th>
            <th class="col-reason">原因</th>
            <th class="col-operator">操作人</th>
            <th class="col-status">阶段</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="error">
            <td colspan="10" class="empty-state">{{ error }}</td>
          </tr>
          <tr v-else-if="loading">
            <td colspan="10" class="empty-state">加载中...</td>
          </tr>
          <tr v-else-if="!items.length">
            <td colspan="10" class="empty-state">这段时间没有记录</td>
          </tr>
          <template v-else>
            <tr v-for="item in items" :key="item.id">
            <td><span class="badge" :class="{ 'badge-present': item.kind === 'present' }">{{ kindLabel(item) }}</span></td>
            <td :title="formatTime(item.refund_time)">
              <span class="time-full">{{ formatTime(item.refund_time) }}</span>
              <span class="time-short">{{ shortTime(item.refund_time) }}</span>
            </td>
            <td><span class="badge">{{ item.point_name || '-' }}</span></td>
            <td class="mono col-bs">{{ item.bs_code || '-' }}</td>
            <td class="dish-name" :title="item.item_name">{{ item.item_name }}</td>
            <td class="num">{{ Number(item.qty || 0) }}</td>
            <td class="num">¥{{ money(item.money) }}</td>
            <td class="reason col-reason" :title="item.reason">{{ item.reason || '-' }}</td>
            <td class="col-operator">{{ item.operator || '-' }}</td>
              <td class="col-status"><span class="badge">{{ statusLabel(item) }}</span></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div class="pagination" v-if="total > pageSize">
      <span>共 {{ total }} 条，第 {{ Math.min(page, pageCount) }}/{{ pageCount }} 页</span>
      <button class="btn btn-sm" :disabled="page <= 1" @click="changePage(1)">首页</button>
      <button class="btn btn-sm" :disabled="page <= 1" @click="changePage(page - 1)">上一页</button>
      <button class="btn btn-sm" :disabled="page >= pageCount" @click="changePage(page + 1)">下一页</button>
      <button class="btn btn-sm" :disabled="page >= pageCount" @click="changePage(pageCount)">末页</button>
    </div>
  </div>
</template>

<style scoped>
/* 这张卡在 two-col 下面整宽，不跟左右两栏抢高度。 */
.refund-table-body {
  flex: 1;
  overflow-y: auto;
  max-height: 420px;
}

.refund-table th.num,
.refund-table td.num {
  text-align: right;
  width: 64px;
}

.refund-table {
  height: auto;
  flex: none;
}
.refund-source {
  font-size: 10px;
  color: var(--text-dim);
  font-weight: 400;
  margin-left: 10px;
  padding-left: 10px;
  border-left: 1px solid var(--border);
}
.refund-summary {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}
/* 全部 / 退单 / 赠单：点一下只看一类，汇总块始终展示两类总量。 */
.refund-tabs {
  display: inline-flex;
  gap: 4px;
}
.refund-table .badge-present {
  border-color: var(--accent);
  color: var(--accent);
}
.refund-table .time-short {
  display: none;
}

.refund-table .mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--text-dim);
}
/* 窄屏：单号、原因、操作人、阶段先收起，留下「谁退的什么、几份、多少钱」。
   九列在 390px 上只能横滚，手机上根本看不到份数与金额（用户常在手机看图）。 */
@media (max-width: 720px) {
  .refund-table .col-bs,
  .refund-table .col-reason,
  .refund-table .col-operator,
  .refund-table .col-status {
    display: none;
  }
  .refund-table-body {
    max-height: none;
  }
  /* 同一天的行占多数，日期留在 tooltip；表内只留时分秒省宽度。 */
  .refund-table .time-short {
    display: inline;
  }
  .refund-table .time-full {
    display: none;
  }
  .refund-table .data-table th,
  .refund-table .data-table td {
    padding-left: 6px;
    padding-right: 6px;
  }
  /* R34：来源说明别在手机上消失，单独占一行。 */
  .refund-source {
    display: block;
    margin-left: 0;
    padding-left: 0;
    border-left: 0;
  }
  .refund-summary {
    justify-content: flex-start;
  }
  .refund-table .dish-name {
    max-width: 104px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .refund-table th.num,
  .refund-table td.num {
    width: 52px;
  }
}

/* 原因列可能很长（「客人误点/多点[退菜]」这类多原因拼接），限宽 + 省略号。 */
.refund-table .reason {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
