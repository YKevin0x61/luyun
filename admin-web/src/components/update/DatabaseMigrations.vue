<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import ConfirmDialog from '../admin/ConfirmDialog.vue'
import SvgIcon from '../SvgIcon.vue'

// 数据库迁移面板：PG 部署的 schema 变更要靠人应用（migrations/pg/000N_*.sql），
// 而更新作业按设计只管换代码。这里把「有哪些待应用、点一下应用」搬到界面上，
// 免得升级后靠人记得敲 psql——漏了的后果通常不报错，只是一直慢或某个功能悄悄降级。
//
// SQLite 部署不需要它：schema 由应用启动时自愈，接口会明确说明。

const status = ref(null)
const loading = ref(true)
const applying = ref(false)
const errorText = ref('')
const okText = ref('')
const confirmOpen = ref(false)

const supported = computed(() => Boolean(status.value && status.value.supported))
const pending = computed(() => (status.value && status.value.pending) || [])
const applied = computed(() => (status.value && status.value.applied) || [])
const changed = computed(() => (status.value && status.value.changed) || [])
const bootstrapOnly = computed(() => (status.value && status.value.bootstrap_only) || [])
const note = computed(() => (status.value && status.value.note) || '')
const incrementalTotal = computed(() => Number(status.value && status.value.incremental_total) || 0)
const upToDate = computed(() => supported.value && !pending.value.length)
// 包里连一条增量脚本都没有：这跟"真的没有待应用"是两回事，必须说清楚，
// 否则「什么都没检查到」会被读成「已是最新」。
const noScriptsShipped = computed(() => supported.value && incrementalTotal.value === 0)

async function load({ keepError = false } = {}) {
  loading.value = true
  // 默认清掉上一次的报错；但「应用失败 → 重新拉状态」那种调用要保留它，
  // 否则错误刚写进 errorText 就被这里的清空覆盖，界面上一点反馈都没有。
  if (!keepError) errorText.value = ''
  try {
    status.value = await api.get('/api/db-migrations')
  } catch (err) {
    errorText.value = err.message || '无法读取数据库迁移状态'
  } finally {
    loading.value = false
  }
}

function askApply() {
  if (!pending.value.length || applying.value) return
  confirmOpen.value = true
}

async function applyPending() {
  confirmOpen.value = false
  applying.value = true
  errorText.value = ''
  okText.value = ''
  try {
    const result = await api.post('/api/db-migrations/apply')
    const versions = (result.applied || []).map((item) => item.version).join('、')
    okText.value = versions ? `已应用：${versions}` : '没有需要应用的迁移'
  } catch (err) {
    errorText.value = err.message || '应用数据库迁移失败'
  } finally {
    applying.value = false
    // keepError：把上面 catch 写的失败原因留住（load 默认会清空它）。
    await load({ keepError: Boolean(errorText.value) })
  }
}

onMounted(load)
</script>

<template>
  <fieldset class="db-migrations">
    <legend>数据库迁移</legend>

    <div v-if="loading" class="hint">加载中…</div>

    <template v-else-if="status">
      <p v-if="!supported" class="hint section-lead">{{ note }}</p>

      <template v-else>
        <p class="hint section-lead">
          本机是 PostgreSQL 后端：schema 变更不会随重启自动应用，需要在升级后点一次「应用待执行迁移」。
          已应用的记录写在本库里，随时能看出当前到哪一版。
        </p>

        <p v-if="errorText" class="hint is-warn" role="alert">{{ errorText }}</p>
        <p v-if="okText" class="hint" role="status">{{ okText }}</p>

        <div v-if="pending.length" class="migration-pending">
          <p class="hint is-warn">
            有 {{ pending.length }} 条迁移待应用：{{ pending.map((item) => item.filename).join('、') }}
          </p>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="applying"
            @click="askApply"
          >{{ applying ? '应用中…' : '应用待执行迁移' }}</button>
        </div>
        <p v-else-if="noScriptsShipped" class="hint is-warn" data-role="no-migration-scripts">
          本发行包内没有增量迁移脚本（<code>migrations/pg/</code> 里只有 bootstrap 脚本），
          所以这里没有可应用的东西。若你确实发过带数据库变更的版本，请确认迁移脚本
          <strong>已提交</strong>——发行包按 <code>git archive HEAD</code> 打包，未提交的文件不会进包。
        </p>
        <p v-else-if="upToDate" class="hint">数据库 schema 已是最新（{{ applied.length }} 条迁移已应用）。</p>

        <p v-if="changed.length" class="hint is-warn">
          这些迁移应用之后文件又被改动过：{{ changed.map((item) => item.filename).join('、') }}。
          面板不会自动重跑它们，请人工确认是否需要新的迁移文件。
        </p>

        <details v-if="applied.length" class="migration-applied">
          <summary>
            <SvgIcon name="chevron-right" :size="14" class="migration-summary__icon" />
            <span>已应用的迁移（{{ applied.length }}）</span>
          </summary>
          <ul>
            <!-- 只写 filename：文件名本身就带版本前缀（0002_xxx.sql），再单独排一列
                 版本号会显示成「0002 0002_xxx.sql」。filename 缺失（老记录）才退回版本号。 -->
            <li v-for="item in applied" :key="item.version">{{ item.filename || item.version }}</li>
          </ul>
        </details>

        <details v-if="bootstrapOnly.length" class="migration-bootstrap">
          <summary>
            <SvgIcon name="chevron-right" :size="14" class="migration-summary__icon" />
            <span>不会自动执行（含清库语句）</span>
          </summary>
          <p class="hint">
            这些脚本含 DROP TABLE，只用于初次建立数据库，面板永久排除它们：
            {{ bootstrapOnly.map((item) => item.filename).join('、') }}
          </p>
        </details>
      </template>
    </template>

    <ConfirmDialog
      v-if="confirmOpen"
      title="应用数据库迁移"
      :message="`将按顺序执行 ${pending.length} 条迁移：${pending.map((item) => item.filename).join('、')}。它们都是可重复执行的幂等 DDL，但请确认当前没有正在进行的发版操作。`"
      confirm-label="应用"
      @confirm="applyPending"
      @cancel="confirmOpen = false"
    />
  </fieldset>
</template>

<style scoped>
/* 面板内部的样式必须写在这里，不能指望父组件 SetupView.vue 的 <style scoped> ——
   那条规则只覆盖到子组件的**根元素**（这一个 fieldset），面板里的 legend / p / ul /
   details 都不带父组件的 scope 属性。缺了下面这一块，整块面板就退回浏览器默认排版
   （16px 白字正文、原生三角、默认圆点列表），和同一页相邻分节的字号/字色明显不是一套。
   数值一律对齐 SetupView.vue 里的同名规矩（legend、.hint、.release-more）。 */
.db-migrations legend {
  font-size: 12px; font-weight: 700; color: var(--text-dim);
  padding: 0 8px; letter-spacing: 0.5px;
}

.db-migrations .hint {
  font-size: 11px; color: var(--text-dim); margin-top: 4px; line-height: 1.5; opacity: 0.85;
}
.db-migrations .hint.is-warn { color: var(--yellow); opacity: 1; }
.db-migrations .section-lead { margin: 0 0 12px; }

.migration-pending .btn { margin-top: 8px; }

.migration-applied,
.migration-bootstrap { margin-top: 10px; }
.migration-applied > summary,
.migration-bootstrap > summary {
  display: flex; align-items: center; gap: 6px; min-height: 36px;
  cursor: pointer; font-size: 12px; font-weight: 600; color: var(--text-dim);
  list-style: none;
}
.migration-applied > summary::-webkit-details-marker,
.migration-bootstrap > summary::-webkit-details-marker { display: none; }
.migration-applied > summary:focus-visible,
.migration-bootstrap > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.migration-summary__icon { color: var(--text-dim); transition: transform 0.15s ease; }
.migration-applied[open] > summary,
.migration-applied[open] .migration-summary__icon,
.migration-bootstrap[open] > summary,
.migration-bootstrap[open] .migration-summary__icon { color: var(--text); }
.migration-applied[open] .migration-summary__icon,
.migration-bootstrap[open] .migration-summary__icon { transform: rotate(90deg); }
@media (prefers-reduced-motion: reduce) {
  .migration-summary__icon { transition: none; }
}

/* 已应用清单是一串文件名流水账：等宽、紧凑，不要浏览器默认的圆点缩进
   （19 条时要一眼扫完，不该被 40px 的项目符号缩进挤掉宽度）。 */
.migration-applied ul { list-style: none; margin: 2px 0 0; padding: 0; }
.migration-applied li {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px; line-height: 1.9; color: var(--text-dim);
  overflow-wrap: anywhere;
}
</style>
