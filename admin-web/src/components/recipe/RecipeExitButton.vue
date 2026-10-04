<script setup>
import ConfirmDialog from '../admin/ConfirmDialog.vue'
import { useWorkbenchLogout } from '../../composables/useWorkbenchLogout'

// 配方阅读面（列表 / 详情 / 打印 / 印码）顶栏的退出入口（票 10，spec 故事 47）。
//
// 这四个页面是**沉浸页**、不套工作台外壳（`WorkbenchLayout` 那条窄栏上没有它们），
// 所以外壳上那颗退出按钮它们够不着 —— 扫码进来的厨师与店长都进得来、却退不出去。
//
// **动作与工作台那颗是同一个**：`composables/useWorkbenchLogout.js` 按此刻的身份派发
// （扫码进来的厨师是员工会话 → 员工登出，队列里有照片先问一句；店长是管理端会话 →
// 客户端登出）。差别只在皮：这一颗用的是配方页那套 `/recipe.css` 令牌
// （`--jade` / `--muted`），工作台那套 `--hy-*` 令牌在这几页里没有定义。
//
// 两处都挂在 `no-print` 的容器里（顶栏 / 打印工具栏），所以退出按钮不会被打进 A4。
const { ask, exit, confirmOpen, queuedCount, cancel, loggingOut } = useWorkbenchLogout()
</script>

<template>
  <button
    type="button"
    class="recipe-exit"
    :disabled="loggingOut"
    @click="ask"
  >{{ loggingOut ? '正在退出…' : '退出' }}</button>
  <ConfirmDialog
    v-if="confirmOpen"
    title="还有照片没传完"
    :message="`还有 ${queuedCount} 张照片在上传队列里。退出登录会清掉它们，需要重新拍。`"
    confirm-label="仍然退出"
    danger
    @confirm="exit"
    @cancel="cancel"
  />
</template>

<style scoped>
/* 与顶栏那几颗 `site-nav-link` 同一身（尺寸、圆角、悬停底色都照它们），
   区别只是它是按钮不是链接。 */
.recipe-exit {
  padding: .42rem .8rem;
  border: 0;
  border-radius: 999px;
  font: inherit;
  font-size: .86rem;
  font-weight: 700;
  color: var(--muted, #6b6358);
  background: none;
  cursor: pointer;
  transition: color .15s, background .15s;
}

.recipe-exit:hover,
.recipe-exit:focus-visible {
  color: var(--jade-ink, #15392a);
  background: var(--jade-soft, #eef4f0);
}

.recipe-exit:disabled {
  opacity: .6;
  cursor: default;
}

/* 窄屏上顶栏那几颗 `site-nav-link` 等宽铺开（recipe.css 第 13 节），退出跟着它们走，
   别在这一行里缩成一个小胶囊。 */
@media (max-width: 640px) {
  .recipe-exit {
    flex: 1;
  }
}
</style>
