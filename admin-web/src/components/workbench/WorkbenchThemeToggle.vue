<script setup>
/** 工作台皮肤开关（2026-10-17 双主题）：桌面顶栏一颗、手机 ⋮ 菜单里一项。
 *
 *  两套主题在令牌层各定义一次，这个按钮只翻 `useWorkbenchTheme` 的单例状态。
 *  图标语义按**当前生效的那一档**画（深色时显示月亮、点下去换浅色），不按"目标"画 ——
 *  按目标画的话，按钮看起来像在说"你现在是浅色"，读起来是反的。
 */
import { computed } from 'vue'
import { useWorkbenchTheme } from '../../composables/useWorkbenchTheme'

const { isDark, toggle } = useWorkbenchTheme()
const label = computed(() => (isDark.value ? '切换到浅色模式' : '切换到深色模式'))
</script>

<template>
  <button class="wb-theme" type="button" :aria-label="label" :title="label" @click="toggle">
    <svg v-if="isDark" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">
      <circle cx="12" cy="12" r="4.1" />
      <path d="M12 2.8v2.1M12 19.1v2.1M2.8 12h2.1M19.1 12h2.1M5.5 5.5l1.5 1.5M17 17l1.5 1.5M18.5 5.5 17 7M7 17l-1.5 1.5" />
    </svg>
    <svg v-else viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
      <path d="M20.2 14.3A8.1 8.1 0 0 1 9.7 3.8 8.4 8.4 0 1 0 20.2 14.3Z" />
    </svg>
  </button>
</template>

<style scoped>
.wb-theme {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  /* 触控下限 44px（designGuard 只拦 min-height < 44）。 */
  width: 44px;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border: 0;
  border-radius: var(--r-1, 4px);
  background: none;
  color: var(--ink-dim);
  cursor: pointer;
}
.wb-theme:hover { background: var(--surface-2); color: var(--ink); }
</style>
