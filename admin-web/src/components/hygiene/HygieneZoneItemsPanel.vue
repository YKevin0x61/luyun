<script setup>
/**
 * 「工作区 · 日常清单」整块（2026-10-19 拆分）。
 *
 * 桌面端它和「工作区」列表并排（左栏 320、右栏清单）；手机端两栏放不下，改成独立一页
 * （`/workbench/floor/zone-items?zone=<id>`）。两边必须**同一份**清单，否则「换标准图」
 * 「编辑标注」会长出两套行为。所以列表 + 三个动作 + ＋新增检查项 + 删除确认都在这里，
 * 父层只传 `zone`、收 `changed`。
 *
 * 编辑器（`HygieneItemEditor`）**跟着被编辑的那一项**渲染：列表用 `<template v-for>`
 * 在每一项 `li.item-row` 后面紧跟一个 `<li class="item-row is-editor">`，点哪项就在哪项
 * 下面展开 —— 原先把 form 固定挂在列表末尾，手机 8 项时点第 1 项要滚到底才看得到。
 * 「＋ 新增检查项」仍折叠在列表底部，展开的是 `mode="new"` 的同一个编辑器组件。
 */
import { ref, watch } from 'vue'
import { api } from '../../api/client'
import ConfirmDialog from '../../components/admin/ConfirmDialog.vue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import HygieneItemEditor from '../../components/hygiene/HygieneItemEditor.vue'
import { standardImageUrl } from '../../utils/hygieneMarkup'

const props = defineProps({
  /** 当前选中的工作区（`{ id, name, items }`）；null 时显示「先选一个工作区」 */
  zone: { type: Object, default: null },
})
const emit = defineEmits(['changed'])

const errorText = ref('')
const deleteItemTarget = ref(null)

// 正在编辑哪一项（null = 没在编辑列表里的项）；`editorMode`：markup / replace
const editingItemId = ref(null)
const editorMode = ref('markup')
// 「新增检查项」入口展开状态（与列表里的就地编辑器互斥）
const newOpen = ref(false)

// 切区（父层换了 `zone`）就收起编辑器，避免把 A 区的草稿带到 B 区。
watch(() => props.zone && props.zone.id, () => { closeEditor() })

function openEditor(item, mode) {
  newOpen.value = false
  editingItemId.value = item.id
  editorMode.value = mode
  errorText.value = ''
}

function openNew() {
  editingItemId.value = null
  newOpen.value = true
  errorText.value = ''
}

function closeEditor() {
  editingItemId.value = null
  newOpen.value = false
}

/** 编辑器保存成功：收起并让父层重拉 zones。 */
function onSaved() {
  closeEditor()
  emit('changed')
}

async function confirmDeleteItem() {
  const item = deleteItemTarget.value
  deleteItemTarget.value = null
  if (!item) return
  errorText.value = ''
  try {
    await api.delete(`/api/hygiene/admin/items/${item.id}`)
    if (editingItemId.value === item.id) closeEditor()
    emit('changed')
  } catch (err) {
    errorText.value = err.message || '无法删除日常检查项'
  }
}
</script>

<template>
  <section class="wbx-panel">
    <header class="wbx-ph">
      <i class="led ok"></i>
      <h2>{{ zone ? zone.name + ' 日常清单' : '日常清单' }}</h2>
      <span class="wbx-hint">{{ zone ? (zone.items || []).length : 0 }} 项</span>
    </header>

    <p v-if="errorText" class="wbx-error" role="alert">{{ errorText }}</p>

    <template v-if="zone">
      <div v-if="!(zone.items || []).length" class="wbx-empty-bar">这个区还没有带标准图的检查项。</div>
      <ul v-else class="item-list">
        <template v-for="item in zone.items" :key="item.id">
          <li class="item-row">
            <HygieneStandardOverlay
              class="item-thumb"
              :src="standardImageUrl('admin', item, 'thumb')"
              display-variant="thumb"
              :lightbox-src="standardImageUrl('admin', item)"
              :standard-id="item.current_standard_id"
              :markup="item.markup || []"
              :alt="item.name"
            />
            <div class="item-meta">
              <strong>{{ item.name }}</strong>
              <div class="item-actions">
                <button type="button" class="btn btn-sm" @click="openEditor(item, 'markup')">编辑标注</button>
                <button type="button" class="btn btn-sm" @click="openEditor(item, 'replace')">换标准图</button>
                <button
                  type="button"
                  class="btn btn-sm btn-danger"
                  :aria-label="`删除日常检查项 ${item.name}`"
                @click="deleteItemTarget = item"
                >删除</button>
              </div>
            </div>
          </li>
          <!-- 编辑器就地挂在被点的这一项下面（点第 1 项就在第 1 项下面展开） -->
          <li v-if="editingItemId === item.id" class="item-row is-editor">
            <HygieneItemEditor
              :zone="zone"
              :item="item"
              :mode="editorMode"
              @saved="onSaved"
              @cancel="closeEditor"
            />
          </li>
        </template>
      </ul>

      <!-- 新增检查项：默认折叠成一行入口，展开的是同一个编辑器的 mode="new" -->
      <button
        v-if="!newOpen"
        type="button"
        class="wbx-fold-entry"
        @click="openNew"
      >＋ 新增检查项</button>
      <HygieneItemEditor
        v-else
        :zone="zone"
        :item="null"
        mode="new"
        @saved="onSaved"
        @cancel="closeEditor"
      />
    </template>
    <div v-else class="wbx-empty-bar">先选一个工作区。</div>

    <ConfirmDialog
      v-if="deleteItemTarget"
      title="删除日常检查项"
      :message="`删除「${deleteItemTarget.name}」会同时删除该项全部记录（含历史照片），删了不能恢复。`"
      confirm-label="删除"
      danger
      @confirm="confirmDeleteItem"
      @cancel="deleteItemTarget = null"
    />
  </section>
</template>

<style scoped>
/* 清单面板：桌面上是两栏的右栏，手机上是独立页的主体 —— 尺寸按容器自适应。 */
.wbx-panel {
  display: flex; flex-direction: column;
  background: var(--surface-1); border: 1px solid var(--line);
  border-radius: var(--r-2); box-shadow: 0 1px 2px var(--wb-ink-a08); overflow: hidden;
}
.wbx-ph { display: flex; align-items: center; gap: 9px; padding: 11px 14px; border-bottom: 1px solid var(--line); }
.wbx-ph h2 { margin: 0; font: 600 12.5px/1 var(--font-sans); letter-spacing: .06em; color: var(--ink); }
.wbx-hint { margin-left: auto; font: 500 9.5px/1 var(--font-mono); color: var(--ink-faint); }
.wbx-error { margin: 0; padding: 8px 14px 0; font-size: 11px; color: var(--danger-bright); }
.wbx-empty-bar {
  display: flex; align-items: center; gap: 8px;
  min-height: 44px; margin: 0; padding: 10px 14px;
  font-size: 10.5px; color: var(--ink-faint);
}
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.ok { background: var(--ok); }

.item-list { list-style: none; margin: 0; padding: 4px 12px 8px; display: flex; flex-direction: column; }
.item-row {
  display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 10px; align-items: center;
  min-height: 72px; padding: 8px 0; border-bottom: 1px solid var(--wb-ink-a08);
}
.item-row:last-child { border-bottom: 0; }
/* 就地展开的编辑器占满整行，自己负责内边距与分隔线 */
.item-row.is-editor { grid-template-columns: minmax(0, 1fr); padding: 0; border-bottom: 1px solid var(--wb-ink-a08); }
.item-thumb { min-height: 0; width: 56px; }
.item-thumb :deep(.std-frame) { min-height: 0; }
.item-thumb :deep(.std-photo img) { max-height: 56px; }
.item-meta { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; }
.item-meta strong { font-size: 11.5px; font-weight: 600; }
.item-actions { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; align-self: stretch; }
.item-actions .btn { min-height: 44px !important; }

.wbx-fold-entry {
  display: flex; align-items: center; gap: 8px; width: 100%;
  min-height: 48px; padding: 10px 14px; margin-top: auto;
  border: 0; border-top: 1px solid var(--wb-ink-a08); background: transparent;
  color: var(--action); font: 500 11px/1 var(--font-sans); letter-spacing: .04em;
  text-align: left; cursor: pointer;
}
.wbx-fold-entry:hover { background: var(--surface-2); }
.wbx-panel :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }

/* 手机（独立页 / 窄栏）：单列、缩略图更大、动作行等宽 44 */
@media (max-width: 720px) {
  .item-row { grid-template-columns: 1fr; gap: 8px; min-height: 0; padding: 10px 0; }
  .item-thumb { width: 96px; }
  .item-thumb :deep(.std-photo img) { max-height: 96px; }
  .item-meta { width: 100%; }
  .item-actions {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px; width: 100%; align-self: stretch;
  }
  .item-actions .btn { width: 100%; }
  .wbx-fold-entry { min-height: 48px; }
}
</style>
