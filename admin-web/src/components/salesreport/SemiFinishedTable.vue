<script setup>
import { computed, ref } from 'vue'
import { buildSemiReportText, downloadTextFile } from '../../utils/salesReportText'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  semiFinished: { type: Array, default: () => [] },
  reportData: { type: Object, default: null },
})
const emit = defineEmits(['push'])

const keyword = ref('')

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return props.semiFinished
    .map((pos) => ({
      ...pos,
      items: (pos.items || []).filter((item) => !kw || `${pos.position} ${item.semi_name} ${item.unit}`.toLowerCase().includes(kw)),
    }))
    .filter((pos) => pos.items.length > 0)
})

function download() {
  if (!props.reportData) {
    window.alert('请先查询报表')
    return
  }
  const { start, end } = props.reportData.date_range
  downloadTextFile(`半成品用量_${start}_${end}.txt`, buildSemiReportText(props.reportData))
}

function doPrint() {
  if (!props.reportData) {
    window.alert('请先查询报表')
    return
  }
  const win = window.open('', '_blank')
  if (!win) {
    window.alert('浏览器阻止了打印窗口')
    return
  }
  const text = buildSemiReportText(props.reportData)
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  win.document.write(`<pre style="font-family:monospace;white-space:pre-wrap;font-size:14px;line-height:1.6;">${escaped}</pre>`)
  win.document.close()
  win.focus()
  win.print()
}
</script>

<template>
  <div class="table-card">
    <div class="table-card-header">
      <h3><SvgIcon name="factory" :size="15" /> 半成品用量</h3>
      <div class="table-tools">
        <input class="input" v-model="keyword" placeholder="搜索半成品/岗位...">
        <button class="btn btn-sm" @click="download">导出</button>
        <button class="btn btn-sm" @click="doPrint">打印</button>
        <button class="btn btn-sm" @click="emit('push')">推送</button>
      </div>
    </div>
    <div style="flex:1;overflow-y:auto" class="luyun-scrollbar">
      <table class="data-table">
        <thead><tr><th>岗位</th><th>半成品</th><th style="text-align:right">用量</th></tr></thead>
        <tbody>
          <tr v-if="!filtered.length"><td colspan="3" class="empty-state">暂无换算规则，请先添加规则</td></tr>
          <template v-for="pos in filtered" :key="pos.position">
            <tr v-for="item in pos.items" :key="pos.position + item.semi_name">
              <td><span class="badge">{{ pos.position }}</span></td>
              <td>{{ item.semi_name }}</td>
              <td style="text-align:right">{{ item.qty }} {{ item.unit }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
