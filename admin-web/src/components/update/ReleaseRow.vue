<script setup>
import { computed } from 'vue'
import StatusPill from '../ui/StatusPill.vue'

// 正式发行版目录的一行：默认列出的最新几个与「展开」区共用同一份标记，
// 避免两份 tr 各写一遍后漂移（窄屏裁列规则也只需维护一处）。
const props = defineProps({
  release: { type: Object, required: true },
  installedTag: { type: String, default: '' },
  latestTag: { type: String, default: '' },
  degraded: { type: Boolean, default: false },
  canApply: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})

defineEmits(['apply'])

/** 身份异常时「当前」不可信（本机 tag 可能读错），不给这个标记。 */
const isInstalled = computed(
  () => !props.degraded && !!props.installedTag && props.release.tag === props.installedTag,
)
const isLatest = computed(() => props.release.tag === props.latestTag)
</script>

<template>
  <tr>
    <td class="mono">{{ release.tag }}</td>
    <td>{{ release.name || '—' }}</td>
    <td>{{ String(release.published_at || '').replace('T', ' ').slice(0, 19) || '—' }}</td>
    <td class="release-actions-cell">
      <div class="release-actions">
        <StatusPill v-if="isInstalled" tone="ok" label="当前" />
        <template v-else>
          <StatusPill v-if="isLatest" tone="info" label="最新" />
          <!-- 自检未通过由目录级提示统一说明，不在每一行重复一遍 -->
          <button
            v-if="canApply"
            type="button"
            class="btn btn-primary btn-sm"
            :disabled="busy"
            @click="$emit('apply', release.tag)"
          >应用此版本</button>
        </template>
      </div>
    </td>
  </tr>
</template>
