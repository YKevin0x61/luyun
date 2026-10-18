<script setup>
import ConfirmDialog from '../admin/ConfirmDialog.vue'
import { useStaffLogout } from '../../composables/useStaffLogout'

// 员工端三张页面（今天 / 整月 / 卫生首页）顶栏右上角**共用**的一颗退出（票 10）。
// 逻辑全在 `useStaffLogout`，这里只管「点了先问一句还是直接退」与那个确认框——
// 三页各写一遍退出（登录接口、清上传队列、有照片先确认、退出后回原页）正是要消掉的重复。
// 三页各自已经有顶栏，所以它是一个放进顶栏的按钮，不是再叠一条壳的横栏。
const { loggingOut, confirmOpen, queuedCount, ask, cancel, logout } = useStaffLogout()
</script>

<template>
  <button
    type="button"
    class="staff-exit"
    :disabled="loggingOut"
    @click="ask"
  >{{ loggingOut ? '正在退出…' : '退出' }}</button>
  <ConfirmDialog
    v-if="confirmOpen"
    title="还有照片没传完"
    :message="`还有 ${queuedCount} 张照片在上传队列里。退出登录会清掉它们，需要重新拍。`"
    confirm-label="仍然退出"
    danger
    @confirm="logout"
    @cancel="cancel"
  />
</template>

<style scoped>
/* 靠 `margin-left: auto` 顶到顶栏最右；用它的页面只要把它放在顶栏最后一项即可
   （今天页原来靠 `.tMe` 撑右，票 10 把那边的 auto 让给了这颗按钮）。
   B 案 v2「钢面 · 仪」：方角（--r-1）+ 1px 控件边界（--line-strong）、透明底；
   触控目标 ≥44px（手机端顶栏里也能点中）。 */
.staff-exit {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  min-height: var(--tap);
  padding: 0 14px;
  font-family: var(--font-sans);
  font-size: 11.5px;
  color: var(--ink);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-1);
  background: transparent;
  cursor: pointer;
}

.staff-exit:disabled {
  opacity: .6;
  cursor: default;
}
</style>
