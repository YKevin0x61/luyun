<script setup>
import { computed } from 'vue'
import SvgIcon from '../SvgIcon.vue'
import StatusPill from '../ui/StatusPill.vue'

// 「系统更新」顶部总览：一句话状态 + 版本对照 + 检测入口。
// 未通过自检 / 身份异常 / 有更新这三种情况在同一处给出，避免操作者在几个
// fieldset 之间来回找结论。
const props = defineProps({
  versionCheck: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  updateAvailable: { type: Boolean, default: false },
  statusSummary: { type: String, default: '' },
  /** degraded_reason 的人话解释；由调用方用 degradedReasonLabel 转换。 */
  degradedReason: { type: String, default: '' },
})

defineEmits(['refresh'])

const tone = computed(() => {
  if (!props.versionCheck) return 'neutral'
  if (props.versionCheck.catalogue_ok === false) return 'warn'
  if (props.versionCheck.degraded) return 'warn'
  if (props.updateAvailable) return 'info'
  return 'ok'
})

const deployModeText = computed(() => {
  const vc = props.versionCheck
  if (!vc?.deploy_mode) return '—'
  if (vc.deploy_mode !== 'docker') return vc.deploy_mode
  return `${vc.deploy_mode}（${vc.docker_container || '未设 LUYUN_DOCKER_CONTAINER'}）`
})
</script>

<template>
  <section class="update-overview" aria-labelledby="update-overview-title">
    <div class="update-overview__head">
      <h2 id="update-overview-title" class="update-overview__title">
        <SvgIcon name="package" :size="15" />版本状态
      </h2>
      <StatusPill :tone="tone" :label="versionCheck ? statusSummary : '尚未检测'" />
      <button
        type="button"
        class="btn update-overview__refresh"
        :disabled="loading"
        @click="$emit('refresh')"
      >
        <SvgIcon name="refresh-cw" :size="14" />
        {{ loading ? '检测中…' : '检测版本' }}
      </button>
    </div>

    <dl v-if="versionCheck" class="update-overview__facts">
      <div>
        <dt>当前安装</dt>
        <dd>{{ versionCheck.installed_tag || '（无版本清单）' }}</dd>
      </div>
      <div>
        <dt>APP_VERSION</dt>
        <dd>{{ versionCheck.app_version || '—' }}</dd>
      </div>
      <div>
        <dt>最新正式版</dt>
        <dd>{{ versionCheck.latest_tag || '（无）' }}</dd>
      </div>
      <div>
        <dt>部署模式</dt>
        <dd>{{ deployModeText }}</dd>
      </div>
    </dl>

    <div v-if="versionCheck?.catalogue_ok === false" class="update-overview__alert is-warn">
      读取 GitHub 发行目录失败（断网、API 限流或仓库不可达），本次结果不反映有没有更新。
      可稍后重新检测；若常被限流，可在下方「GitHub 连接」里填一个只读 Token。
    </div>
    <div v-else-if="versionCheck?.degraded" class="update-overview__alert is-warn">
      本机已装身份异常<template v-if="degradedReason">：{{ degradedReason }}</template>。
      版本对比可能不准确；若本机已是运行实例且自检通过，可在下方发行版目录里「应用此版本」切到正式发行包。
    </div>
    <p v-else-if="versionCheck && updateAvailable" class="update-overview__alert is-info">
      发现更新：<span class="mono">{{ versionCheck.installed_tag }}</span> →
      <span class="mono">{{ versionCheck.latest_tag }}</span>
    </p>
    <p v-else-if="versionCheck" class="update-overview__alert is-dim">
      当前已对齐最新正式发行版。仍可选择较旧 tag 回滚。
    </p>
  </section>
</template>

<style scoped>
.update-overview {
  padding: 14px 16px;
  margin-bottom: 16px;
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.update-overview__head { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; }
.update-overview__title { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 15px; }
.update-overview__refresh {
  margin-left: auto; min-height: 40px; padding: 0 14px; font-size: 13px; justify-content: center;
}
.update-overview__refresh:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.update-overview__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 4px 18px;
  margin: 12px 0 0;
  font-size: 12px;
}
.update-overview__facts > div { display: flex; gap: 8px; min-width: 0; }
.update-overview__facts dt { color: var(--text-dim); white-space: nowrap; }
.update-overview__facts dd {
  margin: 0 0 0 auto; min-width: 0; text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--text); overflow-wrap: anywhere;
}

.update-overview__alert {
  margin: 12px 0 0; padding: 8px 10px; border-radius: 8px;
  font-size: 12px; line-height: 1.6;
}
.update-overview__alert.is-warn {
  background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.32); color: #fde68a;
}
.update-overview__alert.is-info {
  background: rgba(59, 130, 246, 0.12); border: 1px solid rgba(59, 130, 246, 0.28); color: #93c5fd;
}
.update-overview__alert.is-dim { padding: 0; color: var(--text-dim); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

@media (max-width: 560px) {
  .update-overview__refresh { margin-left: 0; width: 100%; }
}
</style>
