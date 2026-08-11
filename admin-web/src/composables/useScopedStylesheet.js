import { onBeforeUnmount, onMounted } from 'vue'

/**
 * 按需加载/卸载一个 <link rel="stylesheet">，仅在使用该 composable 的组件存活期间生效。
 * 用于复用 recipe.css 等独立页面样式，避免其类名（如 .btn/.card 等）常驻污染全局主题。
 */
export function useScopedStylesheet(href) {
  let linkEl = null
  onMounted(() => {
    linkEl = document.createElement('link')
    linkEl.rel = 'stylesheet'
    linkEl.href = href
    linkEl.dataset.scoped = 'true'
    document.head.appendChild(linkEl)
  })
  onBeforeUnmount(() => {
    linkEl?.remove()
  })
}
