<script setup>
// 「无权访问」页（票 03）：有会话、但这一页不是这个身份的 —— 说清这一页属于哪种身份，
// 给一颗回自己首页的按钮。
//
// 它是**越权落点**，不是登录落点：一个会话都没有时守卫仍然送 `/login?next=<原目标>`
// （登录之后还回得去），只有「有会话但身份不匹配」才落这里。旧行为正是这里要消掉的：
// 员工输 `/workbench/floor/daily` 会被静默换成员工首页，`?next=` 也丢了，用户看不到任何解释。
//
// 页面本身**不做授权判断**（拦截在守卫的 `meta.audience` 与各接口的 401）—— 它只回答
// 「这页是谁的」与「你回哪儿」。目标那一页的身份取自页面清单（唯一来源），不是我在这里
// 再写一份路径正则。
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useScopedStylesheet } from '../../composables/useScopedStylesheet'
import { pageRow, pageTitle } from '../../router/pageRoutes.js'
import { isLoggedIn } from '../../utils/authStatus'
import { hygieneDocumentTitle } from '../../utils/hygieneCopy'
import { staffRequest, staffSessionState } from '../../utils/hygieneStaff'
import { STAFF_ENTRY_PATH } from '../../utils/staffPaths'
import { WORKBENCH_TITLE } from '../../utils/workbenchCopy'

useScopedStylesheet('/hygiene-admin.css')

const route = useRoute()
const router = useRouter()

document.title = hygieneDocumentTitle(pageTitle('/workbench/forbidden'))

/** 被拦下来的目标（守卫带过来的 `?next=`）：只看路径部分，query / hash 不参与判定。 */
const target = computed(() => {
  const raw = route.query.next
  const next = Array.isArray(raw) ? raw[0] : raw
  if (typeof next !== 'string' || !next.startsWith('/')) return ''
  return next.split(/[?#]/)[0]
})

/** 这一页属于哪种身份 —— 从页面清单里那一行读，不猜路径。 */
const owner = computed(() => {
  const row = pageRow(target.value)
  if (!row) return '这个地址不是工作台里的页面。'
  if (row.audience === 'admin') {
    // 店里那个「管理员」（员工账号 + 卫生权限=管理员）能调验收与整改单的接口，但这一页
    // 是店长在电脑端的全店面板 —— 两边都对，所以别对他说"你是员工、没你的事"
    // （角色审查 F-03：旧文案正是这么说的）。把他该去哪儿说清楚。
    if (identity.value === 'staff-admin') {
      return `「${row.title}」是店长在电脑端用的页面。你的复核工作在手机端「卫生」页里做。`
    }
    return `「${row.title}」是店长（超级管理员）用的页面。`
  }
  if (row.audience === 'staff') return `「${row.title}」是员工手机端的页面，只在员工身份下打开。`
  // 目标页两种身份都能看（`both`）：走到这里说明拦它的不是身份这一条，别乱认领。
  return `「${row.title}」这一页需要的身份和当前会话对不上。`
})

/** 我此刻是什么身份 —— 与守卫同一套口径（管理端会话优先，再看员工会话）。
 *  `staff-admin` 不是第四种会话，是**员工会话里的卫生权限档**（现场复核档）：界面层一直
 *  看不见它（角色审查 F-01），而这个页面恰好是"必须解释身份"的地方，所以在这里问一次。 */
const identity = ref(null) // null | 'admin' | 'staff' | 'staff-admin'

onMounted(async () => {
  if (await isLoggedIn()) {
    identity.value = 'admin'
    return
  }
  if ((await staffSessionState()) === 'unauthenticated') {
    identity.value = null
    return
  }
  identity.value = 'staff'
  // 拿不到就问不出来 —— 这里**不做授权判断**（拦截在守卫与接口），只影响措辞，
  // 所以失败就当他普通员工，不弹错、不拦人。
  // 判据是**管理权限开关**（2026-10-05 起）：`permission` 那个标签不再代表能力，
  // 只看它会把"标签是管理员、一个勾都没给"的人说成有复核工作（正好说反）。
  try {
    const me = await staffRequest('/api/hygiene/staff/me', { timeoutMs: 4000 })
    const caps = (me && me.employee && me.employee.admin_caps) || []
    if (Array.isArray(caps) && caps.length > 0) identity.value = 'staff-admin'
  } catch (err) {
    /* 断网或超时：保持 'staff' */
  }
})

const identityLabel = computed(() => {
  if (identity.value === 'admin') return '超级管理员'
  if (identity.value === 'staff-admin') return '管理员（员工账号 · 现场复核）'
  if (identity.value === 'staff') return '员工'
  return '未登录'
})

/** 「回我首页」的去向：员工回「今天」，超级管理员回工作台首页（票 06 会把它做成两种
 *  身份各自的首页）。会话已经没了就回登录页 —— 落点由那份判据说了算，不在这里写死。 */
const homeTo = computed(() => {
  if (identity.value === 'admin') return '/workbench'
  if (identity.value) return STAFF_ENTRY_PATH
  return '/login'
})

const homeLabel = computed(() => {
  if (identity.value === 'admin') return '回工作台首页'
  if (identity.value) return '回我的首页'
  return '去登录'
})

function goHome() {
  // `replace`：别让浏览器后退又弹回这一页。
  router.replace(homeTo.value)
}
</script>

<template>
  <div class="hygiene-admin wb-forbid">
    <div class="wb-forbid-card" role="alert">
      <p class="wb-forbid-brand">{{ WORKBENCH_TITLE }}</p>
      <h1 class="wb-forbid-title">无权访问</h1>
      <p class="wb-forbid-why">{{ owner }}</p>
      <p class="wb-forbid-who">你当前的身份：{{ identityLabel }}</p>
      <button class="wb-forbid-btn" type="button" @click="goHome">{{ homeLabel }}</button>
    </div>
  </div>
</template>

<style scoped>
.wb-forbid {
  align-items: center;
  justify-content: center;
  padding: 24px 18px;
}
.wb-forbid-card {
  width: min(420px, 100%);
  padding: 26px 22px 24px;
  background: var(--hy-surface);
  border: 1px solid var(--hy-line);
  border-radius: var(--hy-radius-lg);
  box-shadow: var(--hy-shadow-md);
  text-align: center;
}
.wb-forbid-brand {
  margin: 0 0 14px;
  font-family: var(--font-song); font-size: 12px;
  letter-spacing: .3em; color: var(--hy-faint);
}
.wb-forbid-title {
  margin: 0 0 12px;
  font-family: var(--font-song); font-size: 21px;
  letter-spacing: .1em; color: var(--hy-ink);
}
.wb-forbid-why { margin: 0 0 8px; font-size: 14px; color: var(--hy-ink); }
.wb-forbid-who { margin: 0 0 20px; font-size: 12px; color: var(--hy-muted); }
.wb-forbid-btn {
  font: inherit; font-size: 14px; cursor: pointer;
  color: var(--hy-mint-ink); background: var(--hy-mint);
  border: 1px solid var(--hy-mint-line); border-radius: 999px;
  padding: 9px 22px;
}
.wb-forbid-btn:hover { background: var(--hy-mint-bright); }
</style>
