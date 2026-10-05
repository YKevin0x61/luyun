<script setup>
// 工作台身份（票 04）：顶栏上那一个"我是谁"。
//
// ## 为什么只显示**当前**身份（2026-10-05 用户裁定）
//
// 原来两档恒显（「超级管理员 共享账号」+「员工 余威威」）。那是为店里那台**共用电脑**设计
// 的（ADR 0091 的双会话场景）—— 但卫生这套的设计前提是「卫生活在员工自己手机上」
// （ADR 0037）：员工手机上只有员工会话，管理那一档**永远是灰的**，却占着 132px（390 宽屏的
// 34%，比当前身份那 107px 还宽），点它还会弹一句"这台设备上没有管理端会话"。
// 员工每天看这一行二十次，三分之一是一个跟他无关、点了还挨一句解释的按钮。
//
// 现在：**一行只显示当前身份**（一个点 + 名字），另一档只在**这台设备上真的有两个会话**
// 时才出现 —— 而且收进一个点开的菜单里，不再并排占位。判据是"可用档位数量"，不是猜：
// 只有一个可用档位时按钮不可点、也没有箭头（一个点开是空的菜单比不显示更糟）。
//
// 两档的名字是**领域词表口径**：管理端那个共享账号叫「超级管理员」，不叫「管理员」——
// 「管理员」在词表里是花名册上某个真人的卫生权限档位，同名会打架。员工那一档带出他
// 自己的姓名（拿不到名字时只写「员工」，不显示空括号）。
//
// 它只读 `stores/workbenchIdentity.js`（探针 → 定档 → 记忆）与自己的渲染，**不做权限
// 判断**：切换只换视图与导航面，页面能不能打开仍是守卫与服务端页面墙的事。
import { computed, onMounted, ref } from 'vue'
import { useWorkbenchIdentityStore } from '../../stores/workbenchIdentity'
import { IDENTITY_ADMIN, IDENTITY_STAFF } from '../../utils/workbenchIdentity'

const store = useWorkbenchIdentityStore()

// 自己负责开场的那次探针：外壳只挂这一颗组件，不用额外配「先刷新再渲染」。已经探过就
// 不重复探（store 是单例，同一次页面加载里几个外壳共用一份结论）。
onMounted(() => {
  if (!store.probed) store.refresh()
})

/** 两档的数据，顺序固定：超级管理员在前。 */
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

/** 这台设备上真的能用的档位。探针没回来之前是空的 —— 宁可先不渲染，也不画一颗假的。 */
const available = computed(() => options.value.filter((option) => option.enabled))

/** 此刻显示的那一档：**当前身份，且它真的可用**。
 *
 *  为什么必须带上 `enabled`：这台设备可能记着「员工」而员工会话已经没了（店里那台共用
 *  电脑上很常见）—— 旧结构里那种情况会画出一颗亮着但点不动的档位，用"禁用态"表达；
 *  现在一行只显示一个身份，再把一个**它其实进不去**的身份写成"我是谁"就是撒谎了。
 *  退回唯一可用的那一档；一个可用档位都没有时不渲染（见模板上的 `v-if`）。 */
const current = computed(
  () => options.value.find((option) => option.on && option.enabled) || available.value[0] || null,
)

/** 另一档：只有在**两个档位都可用**时才有值，也就是说菜单只在那个场景下出现。 */
const other = computed(() => {
  if (available.value.length < 2) return null
  return available.value.find((option) => option.key !== current.value?.key) || null
})

const open = ref(false)

function toggleMenu() {
  if (!other.value) return
  open.value = !open.value
}

function choose(option) {
  open.value = false
  store.switchTo(option.key)
}

/** 说明位：**降级提示**（store 的一次性 notice —— 记忆的是超级管理员、那个会话失效、
 *  员工会话还在，于是自动降级并说一声）。它出现时那一档刚失效，用户还没开始点。 */
const statusText = computed(() => store.notice || '')
</script>

<template>
  <div v-if="current" class="wb-id">
    <button
      class="wb-id-current"
      :class="{ 'is-switchable': Boolean(other) }"
      :aria-expanded="other ? String(open) : undefined"
      :aria-haspopup="other ? 'true' : undefined"
      :aria-label="other ? `工作台身份：${current.label}${current.hint}，可切换` : undefined"
      type="button"
      @click="toggleMenu"
    >
      <span class="wb-id-dot" aria-hidden="true"></span>
      <span class="wb-id-name">{{ current.label }}</span>
      <span v-if="current.hint" class="wb-id-hint">{{ current.hint }}</span>
      <span v-if="other" class="wb-id-caret" aria-hidden="true">▾</span>
    </button>

    <!-- 菜单只在**这台设备上真有两个会话**时才有（见 `other` 的注释）。 -->
    <div v-if="open && other" class="wb-id-menu" role="menu">
      <button class="wb-id-menu-item" type="button" role="menuitem" @click="choose(other)">
        <span>切到 {{ other.label }}</span>
        <span v-if="other.hint" class="wb-id-menu-hint">{{ other.hint }}</span>
      </button>
    </div>

    <p v-if="statusText" class="wb-id-notice" role="status">
      <span>{{ statusText }}</span>
      <button class="wb-id-dismiss" type="button" aria-label="知道了" @click="store.dismissNotice()">×</button>
    </p>
  </div>
</template>

<style scoped>
.wb-id { display: flex; align-items: center; gap: 6px; position: relative; flex: 0 0 auto; }

/* 当前身份**不是一颗胶囊**：一个薄荷点 + 名字（可切换时再加一个箭头）。
 *
 * 顶栏这一行只剩两样东西（我是谁 / 退出），两个都做"文字级"的重量，左右才对称 ——
 * 原来左边一颗 132px 的描边胶囊、右边一颗 49px 的，中间空 80px，看着一头沉。
 * 可点区域仍然守住 44px（B6 的账）：`min-height` 撑开，视觉上却没有边框与底色。 */
.wb-id-current {
  display: inline-flex; align-items: center; gap: 7px;
  min-height: 44px; padding: 0 8px;
  font: inherit; font-size: 13.5px; color: var(--hy-ink);
  background: none; border: 0; border-radius: 8px;
  /* 名字不逐字换行（票 12 收的 O3）：顶栏变窄时它是最后一个该被压缩的东西。 */
  white-space: nowrap;
}
.wb-id-current.is-switchable { cursor: pointer; }
.wb-id-current.is-switchable:hover { background: var(--hy-surface-2); }
.wb-id-dot {
  width: 7px; height: 7px; border-radius: 50%; flex: 0 0 auto;
  background: var(--hy-mint);
}
.wb-id-hint { font-size: 12.5px; color: var(--hy-faint); }
.wb-id-caret { color: var(--hy-faint); font-size: 10px; line-height: 1; }

/* 菜单：从当前身份下方展开，只在那一个场景里出现。 */
.wb-id-menu {
  position: absolute; top: calc(100% + 4px); left: 0; z-index: 60;
  min-width: 176px; padding: 5px;
  background: var(--hy-surface); border: 1px solid var(--hy-line-strong);
  border-radius: 12px; box-shadow: var(--hy-shadow-md);
}
.wb-id-menu-item {
  display: flex; align-items: center; gap: 8px; width: 100%; min-height: 40px;
  padding: 8px 10px; border: 0; border-radius: 8px; background: none;
  font: inherit; font-size: 13.5px; color: var(--hy-ink); text-align: left; cursor: pointer;
}
.wb-id-menu-item:hover { background: var(--hy-surface-2); }
.wb-id-menu-hint { margin-left: auto; font-size: 11.5px; color: var(--hy-faint); }

.wb-id-notice {
  display: flex; align-items: center; gap: 6px;
  margin: 0 0 0 6px; font-size: 12px; color: var(--hy-ink);
  background: var(--hy-surface-2); border: 1px solid var(--hy-line-strong);
  border-radius: 999px; padding: 3px 6px 3px 12px;
}
.wb-id-dismiss {
  font: inherit; font-size: 13px; line-height: 1; cursor: pointer;
  color: var(--hy-muted); background: none; border: 0; padding: 2px 6px;
}
.wb-id-dismiss:hover { color: var(--hy-ink); }
</style>
