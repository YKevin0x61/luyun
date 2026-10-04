<script setup>
// 工作台身份切换器（票 04）：顶栏上那两格 —— 此刻以谁的身份在看。
//
// 两档的名字是**领域词表口径**：管理端那个共享账号叫「超级管理员」，不叫「管理员」——
// 「管理员」在词表里是花名册上某个真人的卫生权限档位，同名会打架。员工那一档带出他
// 自己的姓名（拿不到名字时只写「员工」，不显示空括号）。
//
// 它只读 `stores/workbenchIdentity.js`（探针 → 定档 → 记忆）与自己的渲染，**不做权限
// 判断**：切换只换视图与导航面，页面能不能打开仍是守卫与服务端页面墙的事。
//
// 独立组件、不依赖工作台外壳的任何东西：票 05 / 06 的另两个外壳（人事现场、首页）直接
// 复用同一颗，不用各写一遍。
import { computed, onMounted } from 'vue'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from '../../utils/workbenchIdentity'

const store = useWorkbenchIdentityStore()

// 自己负责开场的那次探针：外壳只挂这一颗组件，不用额外配「先刷新再渲染」。已经探过就
// 不重复探（store 是单例，同一次页面加载里几个外壳共用一份结论）。
onMounted(() => {
  if (!store.probed) store.refresh()
})

/** 两格的数据，顺序固定：超级管理员在前。 */
const options = computed(() => [
  {
    key: IDENTITY_ADMIN,
    label: '超级管理员',
    hint: '共享账号',
    on: store.identity === IDENTITY_ADMIN,
    enabled: store.canUseAdmin,
  },
  {
    key: IDENTITY_STAFF,
    label: '员工',
    // 姓名拿不到就整条不渲染 —— 界面上宁可只写「员工」，也不要一个空括号。
    hint: store.staffName ? `（${store.staffName}）` : '',
    on: store.identity === IDENTITY_STAFF,
    enabled: store.canUseStaff,
  },
])

function pick(option) {
  if (!option.enabled) return
  store.switchTo(option.key)
}
</script>

<template>
  <div class="wb-id" role="group" aria-label="工作台身份">
    <button
      v-for="option in options"
      :key="option.key"
      class="wb-id-opt"
      :class="{ 'is-on': option.on }"
      :aria-pressed="option.on"
      :disabled="!option.enabled"
      type="button"
      @click="pick(option)"
    >
      <span class="wb-id-name">{{ option.label }}</span>
      <span v-if="option.hint" class="wb-id-hint">{{ option.hint }}</span>
    </button>

    <!-- 降级提示：只在**发生降级的那一刻**出现（记忆的是超级管理员、那个会话失效、
         员工会话还在），一句话、不阻塞，关掉或切档之后就不再显示。 -->
    <p v-if="store.notice" class="wb-id-notice" role="status">
      <span>{{ store.notice }}</span>
      <button class="wb-id-dismiss" type="button" aria-label="知道了" @click="store.dismissNotice()">×</button>
    </p>
  </div>
</template>

<style scoped>
.wb-id { display: flex; align-items: center; gap: 6px; position: relative; }
.wb-id-opt {
  display: inline-flex; align-items: baseline; gap: 2px;
  font: inherit; font-size: 12px; line-height: 1.2; cursor: pointer;
  color: var(--hy-muted); background: var(--hy-surface-2);
  border: 1px solid var(--hy-line); border-radius: 999px;
  padding: 4px 12px;
}
.wb-id-opt:hover:not(:disabled) { color: var(--hy-ink); border-color: var(--hy-line-strong); }
/* 当前这一档：实心薄荷，与导航里「当前组」同一套令牌。 */
.wb-id-opt.is-on {
  color: var(--hy-mint-ink); background: var(--hy-mint);
  border-color: var(--hy-mint-line);
}
/* 会话不在了的那一档：灰掉、点不动 —— 但不藏起来，用户看得见自己在哪一档。 */
.wb-id-opt:disabled { opacity: .45; cursor: not-allowed; }
.wb-id-hint { font-size: 11px; opacity: .8; }
.wb-id-notice {
  display: flex; align-items: center; gap: 6px;
  margin: 0 0 0 4px; font-size: 12px; color: var(--hy-ink);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line-strong);
  border-radius: 999px; padding: 3px 6px 3px 12px;
}
.wb-id-dismiss {
  font: inherit; font-size: 13px; line-height: 1; cursor: pointer;
  color: var(--hy-muted); background: none; border: 0; padding: 2px 6px;
}
.wb-id-dismiss:hover { color: var(--hy-ink); }
</style>
