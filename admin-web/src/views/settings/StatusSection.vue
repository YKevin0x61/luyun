<script setup>
// ============================================================================
// 状态节（原「系统健康状态」，只读）
//
// 契约（ADR 0099；唯一一份接口定义在 ./sectionContract.js）
//   props  active: Boolean
//   翻成 true 时本节自取数据（5 个只读健康端点）。
//   emits  无 —— 本节不与壳交换状态。
//   inject 无：本节没有写操作，也不弹窗，因此**不渲染就地提示条**（其余四节各有一条）：
//          「重新检查」的加载与失败反馈由 HealthSummary 用 error 属性渲染在本节内，
//          不另加一条永远为空的提示条。
//
// 文案规则（ADR 0099）：默认态只放结论 + 关键数值 + 风险/空态反馈；机制解释（这一节
// 怎么聚合、图表怎么画、报告文件在哪）一律进默认折叠的 <details>。同一份数据不在默认态
// 出现两次：版本只留在总览条（就绪卡里那份是第三处副本，删掉）、空闲只在量表上（它自己的
// 刻度与脚注已经写了）、报告路径只在折叠里（「对账报告」组）。
//
// 内容归属：本节只搬自原 SetupView.vue 的 health 一节，图表组件与数据来源未动。
// ============================================================================
import { computed, watch } from 'vue'
import SvgIcon from '../../components/SvgIcon.vue'
import StatusPill from '../../components/ui/StatusPill.vue'
import HealthSummary from '../../components/system/HealthSummary.vue'
import BulletGauge from '../../components/system/BulletGauge.vue'
import UsageBar from '../../components/system/UsageBar.vue'
import ReadinessGrid from '../../components/system/ReadinessGrid.vue'
import ReconcileProgress from '../../components/system/ReconcileProgress.vue'
import { useSystemHealth } from '../../composables/useSystemHealth'
import { formatTs } from '../../utils/backupProgress'
import { formatCount, formatMb } from '../../utils/systemHealthFormat'

const props = defineProps({
  /** 本分节是否为当前分节（壳给）；翻成 true 时自取数据。 */
  active: { type: Boolean, default: false },
})

const {
  sysHealthLoading,
  sysHealthError,
  sysHealthProbe,
  sysHealthProcess,
  loadSysHealth,
  sysHealthProbeDbLabel,
  sysHealthProbeStatusLabel,
  sysHealthReady,
  sysHealthReadyChecks,
  sysHealthReadyDetails,
  sysHealthReadyHasFailure,
  sysHealthOverallLabel,
  sysHealthOverallPillClass,
  sysHealthUptimeLabel,
  sysHealthVersion,
  sysHealthDisk,
  sysHealthDiskGauge,
  sysHealthMemoryGauge,
  sysHealthFailureGauge,
  sysHealthCounts,
  sysHealthScraperHealth,
  sysHealthReconcileProgress,
  sysHealthRawFacts,
} = useSystemHealth({})

// 自取数据：原 switchSection 的 health 一支。
watch(
  () => props.active,
  (on) => {
    if (on) loadSysHealth()
  },
  { immediate: true },
)

// ===== 两张依赖组件卡：数据全部来自上面这一份 /api/healthz 响应，零新增请求 =====
// 两段的口径不同，排查时要先看这一条：
//   db 段是**实跑**探测——healthz 每次调用都执行 db_manager.health_check()，重连窗口内
//   直接报 'reconnecting'；redis 段只读总线上的现成状态位（RedisBus.enabled/connected），
//   不做主动 ping，所以可能滞后。
// 严重度也因此不同：数据库不可用是门店停摆级问题（error 态是红，且后端 status 会变
// 'degraded'，按既有 worse() 规则进总体结论）；Redis 掉线只是跨进程 nudge 失效——
// 本地派发不经过总线，采集/打印/KDS 照常，所以只标黄、不进总体结论。

/** 数据库四态：healthy / reconnecting / uninitialized / error（含 'error: …' 前缀）。 */
const dbState = computed(() => {
  const raw = sysHealthProbe.value?.db
  if (typeof raw !== 'string' || !raw) return null
  if (raw === 'healthy') return { key: 'healthy', tone: 'ok', label: '已连接', level: 'ok' }
  if (raw === 'reconnecting') return { key: 'reconnecting', tone: 'warn', label: '重连中', level: 'warning' }
  if (raw === 'uninitialized') return { key: 'uninitialized', tone: 'neutral', label: '未初始化', level: 'unknown' }
  // health_check 自己吞下异常时报裸 'error'（db_core/stats.py），healthz 兜底拼的是
  // 'error: <异常>'——两种都是连接失败，都得是红的，不能落进「未知」。
  if (raw === 'error' || raw.startsWith('error:')) {
    return { key: 'error', tone: 'error', label: '连接失败', level: 'critical' }
  }
  return { key: 'unknown', tone: 'neutral', label: '未知', level: 'unknown' }
})

/** error 态要摊开的原文：sysHealthProbeDbLabel 在非 healthy/uninitialized 时就是原始串，直接复用。
 *  截断到 80 字（约三行）：给排查一个线索就够，全文在折叠的「访问探针」组里一条不丢。 */
const DB_ERROR_MAX = 80
const dbErrorText = computed(() => {
  const raw = sysHealthProbeDbLabel.value || ''
  return raw.length > DB_ERROR_MAX ? `${raw.slice(0, DB_ERROR_MAX)}…` : raw
})

/**
 * 卡片描边档位：与「数据库 / Redis」两张卡同一套语法（`.health-card.is-warning` /
 * `.is-critical`，样式在下面的 scoped 里）。**只做视觉，不新算任何阈值** ——
 *   磁盘 / 内存 / 采集失败：直接用各自量表已有的 `level`（阈值判定在
 *   utils/systemHealthCharts.js 里，本次一字未改）；
 *   就绪检查：用 `sysHealthReady.ready`（与顶部总体结论同一判据）与
 *   `sysHealthReadyHasFailure`（卡片自己已经按它渲染「未通过」）。
 * 「数据量」与「对账进度」两张卡没有档位来源（`buildCountBars` 与 `buildReconcileProgress`
 * 都不产出 level，scraper-health 也没给漏单率阈值），所以不描边 —— 给它们编一个阈值
 * 属于新增判据，本票不做。
 */
function tierClass(level) {
  return level === 'warning' || level === 'critical' ? `is-${level}` : ''
}

const readinessTier = computed(() => {
  if (!sysHealthReady.value) return 'unknown'
  if (sysHealthReady.value.ready === false) return 'critical'
  return sysHealthReadyHasFailure.value ? 'warning' : 'ok'
})

/** Redis 三态：已连接 / 已掉线 / 未启用（两个只读状态位，没有中间态）。 */
const redisState = computed(() => {
  const seg = sysHealthProbe.value?.redis
  if (!seg || typeof seg !== 'object') return null
  if (seg.connected) return { key: 'connected', tone: 'ok', label: '已连接', level: 'ok' }
  if (seg.configured) return { key: 'offline', tone: 'warn', label: '已掉线', level: 'warning' }
  return { key: 'unconfigured', tone: 'neutral', label: '未启用', level: 'unknown' }
})
</script>

<template>
  <div class="settings-section">
    <!-- 总览条：结论 + 版本 + 运行时长 + 重新检查。加载反馈是按钮上的「检查中…」，
         失败反馈是条内那行「部分检查不可用：…」，都在触发它的按钮这一块里。 -->
    <HealthSummary
      :pill-class="sysHealthOverallPillClass"
      :overall-label="sysHealthOverallLabel"
      :version="sysHealthVersion"
      :uptime-label="sysHealthUptimeLabel"
      :loading="sysHealthLoading"
      :error="sysHealthError"
      @refresh="loadSysHealth"
    />

    <div class="health-cards">
      <section class="health-card" :class="tierClass(sysHealthDiskGauge.level)" aria-labelledby="health-card-disk">
        <h3 id="health-card-disk" class="health-card__title">
          <SvgIcon name="database" :size="14" />磁盘水位
        </h3>
        <BulletGauge
          title="磁盘空闲"
          :level="sysHealthDiskGauge.level"
          :level-label="sysHealthDiskGauge.levelLabel"
          :pct="sysHealthDiskGauge.pct"
          :zones="sysHealthDiskGauge.zones"
          :marker-pct="sysHealthDiskGauge.markerPct"
          :value-text="sysHealthDiskGauge.valueText"
          :threshold-text="sysHealthDiskGauge.thresholdText"
          :caption="sysHealthDiskGauge.caption"
          :aria-label="sysHealthDiskGauge.ariaLabel"
        />
        <dl v-if="sysHealthDisk" class="health-facts">
          <div><dt>挂载点</dt><dd>{{ sysHealthDisk.path || '—' }}</dd></div>
        </dl>
        <p v-else class="hint">未获取到磁盘明细。</p>
        <!-- 风险：只剩能用不下的空间时才说，正常态不占地方。 -->
        <p v-if="sysHealthDiskGauge.level === 'critical'" class="health-warn is-critical">
          磁盘严重不足，采集会失败，请立即清理。
        </p>
        <p v-else-if="sysHealthDiskGauge.level === 'warning'" class="health-warn is-warning">
          磁盘剩余空间偏低，建议清理或扩容。
        </p>
      </section>

      <section class="health-card" :class="tierClass(sysHealthMemoryGauge.level)" aria-labelledby="health-card-memory">
        <h3 id="health-card-memory" class="health-card__title">
          <SvgIcon name="bar-chart" :size="14" />内存用量
        </h3>
        <BulletGauge
          title="进程内存（RSS）"
          :level="sysHealthMemoryGauge.level"
          :level-label="sysHealthMemoryGauge.levelLabel"
          :pct="sysHealthMemoryGauge.pct"
          :zones="sysHealthMemoryGauge.zones"
          :marker-pct="sysHealthMemoryGauge.markerPct"
          :value-text="sysHealthMemoryGauge.valueText"
          :threshold-text="sysHealthMemoryGauge.thresholdText"
          :caption="sysHealthMemoryGauge.caption"
          :peak="sysHealthMemoryGauge.peak"
          :aria-label="sysHealthMemoryGauge.ariaLabel"
        />
        <dl v-if="sysHealthProcess?.memory" class="health-facts">
          <div><dt>峰值内存</dt><dd>{{ formatMb(sysHealthProcess.memory.peak_memory_mb) }}</dd></div>
          <div><dt>上次内存清理</dt><dd>{{ formatTs(sysHealthProcess.memory.last_cleanup) || '—' }}</dd></div>
          <div><dt>GC 次数</dt><dd>{{ formatCount(sysHealthProcess.memory.gc_collections) }}</dd></div>
        </dl>
        <p v-else class="hint">未获取到进程内存信息。</p>
      </section>

      <section class="health-card" :class="tierClass(sysHealthFailureGauge.level)" aria-labelledby="health-card-failures">
        <h3 id="health-card-failures" class="health-card__title">
          <SvgIcon name="alert-triangle" :size="14" />采集失败
        </h3>
        <BulletGauge
          title="API 失败次数"
          :level="sysHealthFailureGauge.level"
          :level-label="sysHealthFailureGauge.levelLabel"
          :pct="sysHealthFailureGauge.pct"
          :zones="sysHealthFailureGauge.zones"
          :marker-pct="sysHealthFailureGauge.markerPct"
          :value-text="sysHealthFailureGauge.valueText"
          :threshold-text="sysHealthFailureGauge.thresholdText"
          :caption="sysHealthFailureGauge.caption"
          :aria-label="sysHealthFailureGauge.ariaLabel"
        />
        <dl v-if="sysHealthScraperHealth" class="health-facts">
          <div><dt>营业日</dt><dd>{{ sysHealthScraperHealth.biz_date || '—' }}</dd></div>
          <div><dt>最后采集</dt><dd>{{ formatTs(sysHealthScraperHealth.last_scrape_at) || '—' }}</dd></div>
          <div><dt>待结配送单</dt><dd>{{ formatCount(sysHealthScraperHealth.delivery_bills_pending) }}</dd></div>
        </dl>
        <p v-else class="hint">未获取到采集健康数据。</p>
      </section>

      <section class="health-card" aria-labelledby="health-card-counts">
        <h3 id="health-card-counts" class="health-card__title">
          <SvgIcon name="layout-grid" :size="14" />数据量
        </h3>
        <div v-if="sysHealthCounts.length" class="health-usage">
          <UsageBar
            v-for="c in sysHealthCounts"
            :key="c.key"
            :label="c.label"
            :display="c.display"
            :pct="c.pct"
            :aria-label="c.ariaLabel"
          />
        </div>
        <p v-else class="hint">未获取到数据量统计。</p>
      </section>

      <!-- 数据库：从「就绪检查」卡里那行同源 facts 提升成独立卡（同一事实只出现一处）。
           db 段是实跑探测，四种取值各有结论；error 态只摊开截断后的原文，全文在折叠里。 -->
      <section
        class="health-card"
        :class="dbState ? `is-${dbState.level}` : ''"
        aria-labelledby="health-card-db"
      >
        <div class="health-card__head">
          <h3 id="health-card-db" class="health-card__title">
            <SvgIcon name="database" :size="14" />数据库
          </h3>
          <StatusPill v-if="dbState" :tone="dbState.tone" :label="dbState.label" dot />
        </div>
        <p class="hint">每次检查都实跑一次连库探测。</p>
        <p v-if="dbState?.key === 'reconnecting'" class="hint is-warn">
          连库请求会被中间件挡下返回 503，恢复后自动放行。
        </p>
        <p v-else-if="dbState?.key === 'error'" class="hint is-error">原始异常：{{ dbErrorText }}</p>
        <p v-else-if="!dbState" class="hint">未获取到数据库状态。</p>
      </section>

      <section class="health-card" :class="tierClass(readinessTier)" aria-labelledby="health-card-readiness">
        <h3 id="health-card-readiness" class="health-card__title">
          <SvgIcon name="check-circle" :size="14" />就绪检查
        </h3>
        <ReadinessGrid
          :items="sysHealthReadyChecks"
          :details="sysHealthReadyDetails"
          :has-failure="sysHealthReadyHasFailure"
        />
        <dl v-if="sysHealthProbe" class="health-facts">
          <div><dt>探针结论</dt><dd>{{ sysHealthProbeStatusLabel }}</dd></div>
        </dl>
      </section>

      <!-- Redis 总线：只读状态位、不做主动 ping；掉线只影响跨进程 nudge，
           本地派发照常，所以标黄即止（不进总体结论）。 -->
      <section
        class="health-card"
        :class="redisState ? `is-${redisState.level}` : ''"
        aria-labelledby="health-card-redis"
      >
        <div class="health-card__head">
          <h3 id="health-card-redis" class="health-card__title">
            <SvgIcon name="zap" :size="14" />Redis 总线
          </h3>
          <StatusPill v-if="redisState" :tone="redisState.tone" :label="redisState.label" dot />
        </div>
        <p class="hint">只读状态位，不主动 ping。</p>
        <p v-if="redisState?.key === 'offline'" class="hint is-warn">跨进程实时更新失效，本机功能不受影响。</p>
        <p v-else-if="redisState?.key === 'unconfigured'" class="hint">
          只在未起订阅任务时出现，正常部署启动期就拦住。
        </p>
        <p v-else-if="!redisState" class="hint">未获取到 Redis 状态。</p>
      </section>

      <section class="health-card" aria-labelledby="health-card-reconcile">
        <h3 id="health-card-reconcile" class="health-card__title">
          <SvgIcon name="refresh-cw" :size="14" />对账进度
        </h3>
        <ReconcileProgress :state="sysHealthReconcileProgress" />
        <dl v-if="sysHealthScraperHealth" class="health-facts">
          <div><dt>最后对账</dt><dd>{{ formatTs(sysHealthScraperHealth.last_reconcile?.at) || '—' }}</dd></div>
          <div><dt>漏单数量</dt><dd>{{ formatCount(sysHealthScraperHealth.last_reconcile?.missed_qty) }}</dd></div>
          <div>
            <dt>漏单率</dt>
            <dd>
              {{ typeof sysHealthScraperHealth.last_reconcile?.miss_rate_pct === 'number'
                ? `${sysHealthScraperHealth.last_reconcile.miss_rate_pct}%` : '—' }}
            </dd>
          </div>
        </dl>
      </section>
    </div>

    <!-- 明细一律在这里：原始指标、分区清单、对账报告文件、启动标识与时间戳一条不丢。 -->
    <details v-if="sysHealthRawFacts.length" class="section-help">
      <summary>
        <SvgIcon name="chevron-right" :size="14" class="section-help__icon" />
        <span>全部原始指标</span>
      </summary>
      <div
        v-for="g in sysHealthRawFacts"
        :key="g.key"
        class="section-help__group"
      >
        <h4>{{ g.title }}</h4>
        <dl class="health-facts">
          <div v-for="item in g.items" :key="`${g.key}-${item.k}`">
            <dt>{{ item.k }}</dt><dd>{{ item.v }}</dd>
          </div>
        </dl>
      </div>
    </details>
  </div>
</template>

<style scoped>
.hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; opacity: 0.85; overflow-wrap: anywhere; }
.hint.is-warn { color: var(--yellow); opacity: 1; }
.hint.is-error { color: #fca5a5; opacity: 1; }

/* ===== 健康面板：卡片 = 结论（量表）+ 关键数值 + 风险/空态 ===== */
.health-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.health-card {
  display: flex; flex-direction: column; gap: 8px;
  min-width: 0;
  padding: 12px 14px 14px;
  background: rgba(10, 13, 22, 0.5);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.health-card__title {
  display: flex; align-items: center; gap: 6px;
  margin: 0; font-size: 12px; font-weight: 700; letter-spacing: 0.4px; color: var(--text-dim);
}
/* 依赖组件卡（数据库 / Redis 总线）：标题 + 结论胶囊同一行；胶囊的文字与色彩是并列的两条通道。 */
.health-card__head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
/* 档位描边：八张卡同一套（数据库 / Redis 由 t10 加，磁盘 / 内存 / 采集失败 / 就绪检查
   由 t16 补齐）—— 同一网格里「这张卡不正常」只有这一种表达，不再有的卡描边、有的卡只用
   下面的 .health-warn 色块。档位全部来自既有 level / ready 判定，本次没有新阈值。 */
.health-card.is-warning { border-color: rgba(245, 158, 11, 0.35); }
.health-card.is-critical { border-color: rgba(239, 68, 68, 0.4); }

/* 明细一律「标签 + 等宽数字」，标签与值都能断行而不是撑出横向滚动。 */
.health-facts { display: grid; gap: 4px; margin: 0; font-size: 11px; }
.health-facts > div { display: flex; justify-content: space-between; gap: 10px; min-width: 0; }
.health-facts dt { color: var(--text-dim); min-width: 0; overflow-wrap: anywhere; }
.health-facts dd {
  margin: 0; min-width: 0; text-align: right; color: var(--text);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow-wrap: anywhere;
}
/* 卡片里的标签都很短，保持一行且不参与收缩（收缩会让它比文字还窄、文字压到值上）；
   折叠区的键可能是整条路径（分区清单），必须能折行——390px 下让 dt 折行，是这套
   facts 网格不横向溢出的关键。 */
.health-card .health-facts dt { white-space: nowrap; flex: 0 0 auto; }

.health-warn { margin: 0; padding: 8px 10px; border-radius: 8px; font-size: 11px; line-height: 1.5; }
.health-warn.is-critical { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.health-warn.is-warning { background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.3); color: #fde68a; }

.health-usage { display: flex; flex-direction: column; gap: 8px; }

/* 机制解释的折叠块：默认收起，皮与其余分节的 .section-help 同一套（chevron + 展开旋转 90°）。 */
.section-help {
  padding: 10px 14px;
  background: rgba(10, 13, 22, 0.5);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 12px;
}
.section-help > summary {
  display: flex; align-items: center; gap: 6px; min-height: 36px;
  cursor: pointer; font-weight: 600; color: var(--text-dim);
  list-style: none;
}
.section-help > summary::-webkit-details-marker { display: none; }
.section-help > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.section-help__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.section-help[open] > summary,
.section-help[open] .section-help__icon { color: var(--text); }
.section-help[open] .section-help__icon { transform: rotate(90deg); }
.section-help__group { margin-top: 10px; }
.section-help__group h4 { margin: 0 0 6px; font-size: 11px; letter-spacing: 0.4px; color: var(--text-dim); }

@media (prefers-reduced-motion: reduce) {
  .section-help__icon { transition: none; }
}

@media (max-width: 700px) {
  .health-cards { grid-template-columns: minmax(0, 1fr); }
}

/* 窄屏触控尺寸：折叠区的开合把手与密码框的显隐开关都只有 32/36 与 22/24px 高，
 * 手指点不中。只在 ≤430px 抬到 44 / 40px —— 桌面那一档是鼠标点，密度维持原样。
 * （`.btn` / `.btn-sm` / 勾选行的下限在 styles/theme.css 里，这里只补分节自己的。） */
@media (max-width: 430px) {
  .section-help > summary { min-height: 44px; }
}
</style>
