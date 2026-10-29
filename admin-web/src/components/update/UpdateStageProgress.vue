<script setup>
import { computed } from 'vue'
import { formatTs } from '../../utils/backupProgress'
import { stepSymbol, updateDownloadView, updateStageSteps } from '../../utils/updateProgress'

// 更新作业阶段进度：把后端「当前 stage」还原成一条固定步骤链。
// 状态用符号 + 文字表达（✓ / ● / ○ / ✗），颜色只是辅助；失败时只用作业里确实
// 落下的时间戳判断哪些步骤完成过，不假装知道失败发生在哪一步。
const props = defineProps({
  job: { type: Object, default: null },
})

const view = computed(() => updateStageSteps(props.job))
// 下载发行包是唯一会持续几分钟、又是弱网门店最常卡住的一步：单独给速率与进度。
const download = computed(() => updateDownloadView(props.job))

/** 备份快照时间戳不是 ISO（20260919-0359），原样展示；其余按统一格式截断。 */
function formatAt(value) {
  if (!value) return ''
  return /^\d{8}-\d{4}$/.test(value) ? value : formatTs(value)
}
</script>

<template>
  <div class="stages" :class="`is-${view.outcome}`">
    <ol class="stages__list" :aria-label="`更新阶段：${view.summary}`">
      <li
        v-for="item in view.items"
        :key="item.key"
        class="stage"
        :class="`is-${item.state}`"
      >
        <span class="stage__symbol" aria-hidden="true">{{ stepSymbol(item.state) }}</span>
        <span class="stage__label">{{ item.label }}</span>
        <span v-if="item.at" class="stage__at">{{ formatAt(item.at) }}</span>
      </li>
    </ol>
    <div v-if="download" class="download">
      <p class="download__head">
        <span class="download__title">发行包下载进度</span>
        <span v-if="download.percent !== null" class="download__percent">{{ download.percent }}%</span>
      </p>
      <!-- 原生 <progress>：宽度交给浏览器按 value/max 渲染，源码里不需要内联宽度
           （设计护栏禁止新增 style= 绑定）。没有总量时 value 为 null = 不确定态。 -->
      <progress
        class="download__bar"
        :class="{ 'is-indeterminate': download.indeterminate }"
        :value="download.percent === null ? null : download.percent"
        max="100"
        :aria-label="download.ariaText"
      ></progress>
      <p class="download__stats">
        <span v-if="download.totalText">{{ download.doneText }} / {{ download.totalText }}</span>
        <span v-else>已下载 {{ download.doneText }}</span>
        <span v-if="download.rateText" class="download__rate">{{ download.rateText }}</span>
        <span v-if="download.attempt" class="download__attempt">第 {{ download.attempt }} 次尝试</span>
      </p>
    </div>
    <p class="stages__summary">{{ view.summary }}</p>
  </div>
</template>

<style scoped>
.stages { display: grid; gap: 8px; }
.stages__list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 6px;
  margin: 0; padding: 0; list-style: none;
}
.stage {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 12px;
  background: rgba(10, 13, 22, 0.55);
  border: 1px solid var(--border);
}
.stage__symbol { font-size: 13px; font-weight: 700; line-height: 1; color: var(--text-dim); }
.stage__label { color: var(--text-dim); min-width: 0; }
.stage__at {
  grid-column: 1 / -1;
  font-size: 11px; color: var(--text-dim);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-wrap: anywhere;
}

.stage.is-done { border-color: rgba(34, 197, 94, 0.3); }
.stage.is-done .stage__symbol,
.stage.is-done .stage__label { color: var(--green); }

.stage.is-active {
  border-color: rgba(99, 102, 241, 0.5);
  background: rgba(99, 102, 241, 0.14);
}
.stage.is-active .stage__symbol,
.stage.is-active .stage__label { color: var(--text); font-weight: 700; }
.stage.is-active .stage__symbol { color: var(--accent); }

.stage.is-failed {
  border-color: rgba(239, 68, 68, 0.45);
  background: rgba(239, 68, 68, 0.12);
}
.stage.is-failed .stage__symbol,
.stage.is-failed .stage__label { color: #fca5a5; font-weight: 700; }

.stages__summary {
  margin: 0; font-size: 12px; line-height: 1.6; color: var(--text-dim);
}
.stages.is-failed .stages__summary { color: #fca5a5; }
.stages.is-unhealthy .stages__summary { color: var(--yellow); }
.stages.is-succeeded .stages__summary { color: var(--green); }

/* 下载发行包：弱网门店最常卡住的一步，单独给速率与真实进度。 */
.download {
  display: grid;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--card);
}
.download__head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 8px; margin: 0;
}
.download__title { font-size: 12px; font-weight: 700; color: var(--text); }
.download__percent {
  font-size: 12px; font-weight: 700; color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.download__bar {
  display: block; width: 100%; height: 6px;
  border: 0; border-radius: 999px; overflow: hidden;
  background: var(--border); appearance: none;
  accent-color: var(--accent); color: var(--accent);
}
.download__bar::-webkit-progress-bar { background: var(--border); border-radius: 999px; }
.download__bar::-webkit-progress-value { background: var(--accent); border-radius: 999px; }
.download__bar::-moz-progress-bar { background: var(--accent); border-radius: 999px; }
.download__bar.is-indeterminate {
  background-image: linear-gradient(
    90deg, var(--border) 0 40%, var(--accent) 40% 60%, var(--border) 60% 100%
  );
  background-size: 250% 100%;
  animation: download-sweep 1.4s linear infinite;
}
.download__bar.is-indeterminate::-webkit-progress-bar { background: transparent; }
@keyframes download-sweep {
  from { background-position: 100% 0; }
  to { background-position: -100% 0; }
}
.download__stats {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin: 0; font-size: 11px; color: var(--text-dim);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-variant-numeric: tabular-nums;
}
.download__rate { color: var(--text); font-weight: 600; }
.download__attempt { color: var(--yellow); }

@media (prefers-reduced-motion: reduce) {
  .stage { transition: none; }
  .download__bar.is-indeterminate { animation: none; }
}
</style>
