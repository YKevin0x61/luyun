/**
 * 单行横向滚动容器（全局导航 tab 条 / 配置页分节导航）的共用行为。
 *
 * 这几条都是**"滚动容器"这类控件特有的、看不见的坑**，出错时页面不报错、只是
 * 让人用不下去，所以集中在这里并留下原因：
 *
 * 1. 把当前项滚进视野。`/logs` 在 390 下位于 382–432px（视口 390px），active 那格
 *    自己在屏幕外——用户看不出"我现在在哪一页"，因为高亮项根本没显示。路由换页时
 *    容器不会自己动，必须显式滚。
 * 2. 溢出提示。容器 `scrollbar-width: none`（滚动条是故意藏起来的，否则在窄栏里
 *    很丑），于是"右边还有内容"这件事没有任何线索：`/settings` 的 7 个分节里就有
 *    4 个完全落在屏幕外，看起来像"只有 3 个分节"。两端加渐隐遮罩，只在真的还有
 *    内容时才出现。
 * 3. 滚动位置要跟着内容与容器尺寸变化重算：窄屏转屏、字体加载完、分节文案变长都会
 *    改变 `scrollWidth`，只在 `scroll` 事件里算会漏掉这些情况。
 *
 * 当前项是**滚动那一刻现查 DOM**（`.active`）而不是拿一个模板 ref 存着：vue 的函数
 * ref 只在挂载/卸载时回调，路由切换只改 `:class` 不会重新回调，存下来的引用会一直
 * 指向上一页那一格——于是"把当前项滚进视野"变成"把上一页滚进视野"。现查 DOM 没有
 * 这个滞后。
 */

import { onBeforeUnmount, onMounted, ref } from 'vue'

/** 判定"贴边"的容差：亚像素布局常留下 0.5px 的残余滚动量。 */
const EDGE_EPSILON = 1

/** 当前项的标记类：与 `:class="{ active: ... }"` 用的是同一个。 */
const ACTIVE_SELECTOR = '.active'

export function createScrollHints() {
  const scrollerRef = ref(null)
  const atStart = ref(true)
  const atEnd = ref(true)
  let resizeObserver = null

  function syncScrollHints() {
    const el = scrollerRef.value
    if (!el) return
    const max = el.scrollWidth - el.clientWidth
    atStart.value = el.scrollLeft <= EDGE_EPSILON
    // max <= 0 表示压根没溢出，两端都算贴边（遮罩不该出现）。
    atEnd.value = max <= EDGE_EPSILON || el.scrollLeft >= max - EDGE_EPSILON
  }

  /** 把当前项居中滚进视野。`behavior` 交给调用方定（首屏用 auto，免得进场时抖）。 */
  function scrollActiveIntoView(behavior = 'smooth') {
    const el = scrollerRef.value
    if (!el) return
    const active = el.querySelector(ACTIVE_SELECTOR)
    // jsdom 与老 WebView 可能没有 scrollIntoView：没有就只更新提示状态，不报错。
    if (active && typeof active.scrollIntoView === 'function') {
      active.scrollIntoView({ behavior, block: 'nearest', inline: 'center' })
    }
    syncScrollHints()
  }

  onMounted(() => {
    syncScrollHints()
    const el = scrollerRef.value
    if (!el) return
    el.addEventListener('scroll', syncScrollHints, { passive: true })
    // 内容或容器尺寸变化也要重算：转屏、字体、分节文案变长都会改变可滚动范围。
    if (typeof ResizeObserver === 'function') {
      resizeObserver = new ResizeObserver(syncScrollHints)
      resizeObserver.observe(el)
      // `children` 是 HTMLCollection（老 WebView 里不可迭代），先转数组再遍历。
      for (const child of Array.from(el.children)) resizeObserver.observe(child)
    }
    window.addEventListener('resize', syncScrollHints)
  })

  onBeforeUnmount(() => {
    scrollerRef.value?.removeEventListener('scroll', syncScrollHints)
    resizeObserver?.disconnect()
    resizeObserver = null
    window.removeEventListener('resize', syncScrollHints)
  })

  return { scrollerRef, atStart, atEnd, syncScrollHints, scrollActiveIntoView }
}
