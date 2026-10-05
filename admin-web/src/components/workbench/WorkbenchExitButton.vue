<script setup>
import ConfirmDialog from '../admin/ConfirmDialog.vue'
import { useWorkbenchLogout } from '../../composables/useWorkbenchLogout'

// 工作台那几个外壳（工作台 / 人事 / 现场）共用的退出入口（票 06；票 10 收敛之后
// 它与配方阅读面那颗 `components/recipe/RecipeExitButton.vue` 走的是同一个动作）。
//
// 逻辑全在 `composables/useWorkbenchLogout.js`（身份 → 哪条出口的唯一映射），这里只管
// 那颗按钮与员工那侧的「还有照片没传完」确认框。**改退出行为去改那一个文件** ——
// 三颗按钮（工作台壳 / 员工端三页 / 配方阅读面）都只是皮。
//
// 它长得跟员工端那颗 `components/staff/StaffExitButton.vue` 一样是有意的：同一个动作在
// 工作台里应该长同一个样子。两处的差别只在「谁负责确认框」—— 员工端那三页有自己的顶栏、
// 直接挂那颗；工作台这几个壳要按身份分派，所以挂这一颗。
const { ask, exit, confirmOpen, queuedCount, cancel, loggingOut } = useWorkbenchLogout()
</script>

<template>
  <button
    type="button"
    class="wb-exit"
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
/* 用它的壳只要把它放在顶栏最后一项即可（跟员工端那颗同一个尺寸与色调）。 */
.wb-exit {
  flex: 0 0 auto;
  font-size: 11.5px;
  color: var(--hy-muted);
  border: 1px solid var(--hy-line);
  border-radius: 999px;
  padding: 4px 10px;
  background: var(--hy-surface-2);
  cursor: pointer;
}

.wb-exit:hover { color: var(--hy-seal-bright); border-color: var(--hy-seal-line); }
.wb-exit:disabled { opacity: .6; cursor: default; }

/* 触控下限（B6）：手机上原来 45×26 —— 顶栏里最小的目标之一，还紧挨着导航胶囊。
   抬到 44px（宽也补到 44，字少也不会变成一个细条）；桌面档保持那颗小胶囊。 */
@media (max-width: 720px) {
  .wb-exit { min-height: 44px; min-width: 44px; padding: 4px 12px; }
}
</style>
