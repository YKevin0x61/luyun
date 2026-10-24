<script setup>
/**
 * 日常检查项的编辑区（2026-10-19 从 `HygieneZoneItemsPanel` 再拆一层）。
 *
 * 为什么再拆：编辑区原来固定在列表**最后**——手机清单 8 项时点第 1 项的「编辑标注」，
 * 面板出现在第 8 项下面，得滚到底才看得见。现在由面板在**被编辑的那一项下面**就地渲染
 * 这个组件；组件自己挂载后 `scrollIntoView({ block: 'center' })`，点完立刻看到editor。
 *
 * 三种模式共用一套状态与动作：
 *   - `markup`：只改标注（PATCH `/items/{id}/standard/markup`），不换图；
 *   - `replace`：换标准图（FormData 进上传队列，`/items/{id}/standard`）；
 *   - `new`：新增检查项（FormData 进上传队列，`/zones/{zoneId}/items`）。
 * 保存成功 emit `saved`（父层收起编辑器并重拉），取消 emit `cancel`。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api/client'
import { useImageUploadQueueStore } from '../../stores/imageUploadQueue'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import {
  createArrowMark,
  createCaptionMark,
  createCircleMark,
  parseMarkup,
  standardImageUrl,
} from '../../utils/hygieneMarkup'

const props = defineProps({
  /** 当前工作区（新增检查项要它的 id 拼上传路径） */
  zone: { type: Object, required: true },
  /** 要编辑的那一项；`mode="new"` 时为 null */
  item: { type: Object, default: null },
  /** 'markup' | 'replace' | 'new' */
  mode: { type: String, default: 'new' },
})
const emit = defineEmits(['saved', 'cancel'])

const root = ref(null)
const itemName = ref('')
const file = ref(null)
const previewUrl = ref('')
const markup = ref([])
const toolMode = ref('circle')
const captionText = ref('')
const arrowStart = ref(null)
const savingMarkup = ref(false)
const editingMarkIndex = ref(-1)
const editingText = ref('')
const errorText = ref('')
const imageUploads = useImageUploadQueueStore()

/** 只改标注、不换图（服务层会插一版新标准，历史冻结标准不动）。 */
const markupEditing = computed(() => props.mode === 'markup')
const replacingItem = computed(() => (props.mode === 'new' ? null : props.item))

const editorSrc = computed(() => {
  if (previewUrl.value) return previewUrl.value
  if (replacingItem.value) return standardImageUrl('admin', replacingItem.value)
  return ''
})

const canSave = computed(() => {
  if (markupEditing.value) return Boolean(replacingItem.value)
  if (!file.value) return false
  if (replacingItem.value) return true
  return Boolean(itemName.value.trim())
})

/** 标题带上项名：编辑标注 · 案板工具盒 / 换标准图 · 案板工具盒 / 新增检查项 */
const title = computed(() => {
  if (props.mode === 'markup') return `编辑标注 · ${props.item ? props.item.name : ''}`.trim()
  if (props.mode === 'replace') return `换标准图 · ${props.item ? props.item.name : ''}`.trim()
  return '新增检查项'
})

onMounted(async () => {
  // 编辑既有项：把名称与已有标注填进来（新增项保持空白）。
  if (props.mode !== 'new' && props.item) {
    itemName.value = props.item.name || ''
    markup.value = parseMarkup(props.item.markup)
  }
  await nextTick()
  // 点哪一项就把它送到视口中间 —— 手机上不用再往下找编辑面板。
  const el = root.value
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'center' })
})

onBeforeUnmount(clearPreview)

function clearPreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
}

function onFile(event) {
  const picked = event.target.files && event.target.files[0]
  clearPreview()
  file.value = picked || null
  previewUrl.value = picked ? URL.createObjectURL(picked) : ''
  arrowStart.value = null
}

function onPoint(point) {
  if (toolMode.value === 'circle') {
    markup.value = [...markup.value, createCircleMark(point.x, point.y)]
    return
  }
  if (toolMode.value === 'caption') {
    markup.value = [...markup.value, createCaptionMark(point.x, point.y, captionText.value)]
    return
  }
  if (!arrowStart.value) {
    arrowStart.value = point
    return
  }
  markup.value = [
    ...markup.value,
    createArrowMark(arrowStart.value.x, arrowStart.value.y, point.x, point.y),
  ]
  arrowStart.value = null
}

function removeMark(index) {
  markup.value = markup.value.filter((_, i) => i !== index)
  if (editingMarkIndex.value === index) cancelCaption()
}

/* 批注可以改文字（2026-10-19：原来只能删掉重画，长批注想改两个字就得重来）。
   圆圈/箭头没有文字，不给入口。 */
function startEditCaption(index) {
  const mark = markup.value[index]
  if (!mark || mark.kind !== 'caption') return
  editingMarkIndex.value = index
  editingText.value = mark.text || ''
}

function cancelCaption() {
  editingMarkIndex.value = -1
  editingText.value = ''
}

function saveCaption() {
  const text = editingText.value.trim()
  const index = editingMarkIndex.value
  if (!text || index < 0) return
  markup.value = markup.value.map((mark, i) => (i === index ? { ...mark, text } : mark))
  cancelCaption()
}

async function saveMarkup() {
  if (savingMarkup.value || !replacingItem.value) return
  savingMarkup.value = true
  errorText.value = ''
  try {
    await api.patch(`/api/hygiene/admin/items/${replacingItem.value.id}/standard/markup`, {
      markup: markup.value,
    })
    emit('saved')
  } catch (err) {
    errorText.value = err.message || '无法保存标准图标注'
  } finally {
    savingMarkup.value = false
  }
}

function saveItem() {
  if (!canSave.value) return
  if (markupEditing.value) {
    void saveMarkup()
    return
  }
  errorText.value = ''
  const replacing = Boolean(replacingItem.value)
  const itemLabel = replacingItem.value ? replacingItem.value.name : itemName.value.trim()
  const form = new FormData()
  form.append('file', file.value)
  form.append('markup', JSON.stringify(markup.value))
  if (!replacing) form.append('name', itemName.value.trim())
  try {
    imageUploads.enqueue({
      path: replacing
        ? `/api/hygiene/admin/items/${replacingItem.value.id}/standard`
        : `/api/hygiene/admin/zones/${props.zone.id}/items`,
      formData: form,
      label: replacing ? `更新标准图 · ${itemLabel}` : `标准图 · ${itemLabel}`,
      detail: file.value.name,
      // 上传成功后再让父层重拉，避免列表先闪一次旧数据；入队后先收起编辑器。
      onSuccess: () => emit('saved'),
      // 建检查项 / 换标准图都是无条件 INSERT：自动重试会重复建行，只允许人工重试。
      autoRetry: false,
    })
    emit('saved')
  } catch (err) {
    errorText.value = err.message || '无法加入上传队列'
  }
}

function markLabel(mark) {
  if (mark.kind === 'circle') return '圆圈'
  if (mark.kind === 'arrow') return '箭头'
  return mark.text ? `批注：${mark.text}` : '批注'
}
</script>

<template>
  <form ref="root" class="item-editor" @submit.prevent="saveItem">
    <h2 class="wbx-subhead"><i class="led sig"></i>{{ title }}</h2>
    <p v-if="markupEditing" class="editor-lead">
      只改标注、不换图：保存后会生成新一版标准图，员工端会提示重新对照；已经交过的记录仍看当时那一版。
    </p>
    <p v-else class="editor-lead">标准图保存后检查项才会出现在员工端。可在图上画圆圈、箭头和批注。</p>

    <p v-if="errorText" class="wbx-error" role="alert">{{ errorText }}</p>

    <label v-if="!markupEditing" class="editor-field">
      检查项名称
      <input
        v-model="itemName"
        class="input"
        type="text"
        maxlength="40"
        :disabled="Boolean(replacingItem)"
        placeholder="比如案板表面"
      >
    </label>
    <label v-if="!markupEditing" class="editor-field">
      标准图
      <input class="input" type="file" accept="image/*" @change="onFile">
    </label>

    <div class="mark-tools">
      <button type="button" class="btn btn-sm" :class="{ 'btn-primary': toolMode === 'circle' }" @click="toolMode = 'circle'">圆圈</button>
      <button type="button" class="btn btn-sm" :class="{ 'btn-primary': toolMode === 'arrow' }" @click="toolMode = 'arrow'; arrowStart = null">箭头</button>
      <button type="button" class="btn btn-sm" :class="{ 'btn-primary': toolMode === 'caption' }" @click="toolMode = 'caption'">批注</button>
      <input
        v-if="toolMode === 'caption'"
        v-model="captionText"
        class="input"
        type="text"
        maxlength="40"
        placeholder="批注文字，再点图放上去"
      >
      <span v-if="toolMode === 'arrow' && arrowStart" class="editor-hint">再点一次画出箭头</span>
    </div>

    <!-- 手机上画布限高，编辑动作条贴在组件底部，保存/取消始终看得到 -->
    <div class="editor-canvas">
      <HygieneStandardOverlay
        :src="editorSrc"
        :standard-id="replacingItem ? replacingItem.current_standard_id : null"
        :markup="markup"
        editable
        :alt="itemName || '标准图预览'"
        @point="onPoint"
      />
    </div>

    <ul v-if="markup.length" class="mark-list">
      <li v-for="(mark, index) in markup" :key="index">
        <template v-if="editingMarkIndex === index">
          <input
            v-model="editingText"
            class="input mark-edit"
            type="text"
            maxlength="40"
            placeholder="批注文字"
            @keyup.enter.prevent="saveCaption"
            @keyup.esc="cancelCaption"
          >
          <span class="mark-acts">
            <button type="button" class="btn btn-sm" @click="cancelCaption">取消</button>
            <button
              type="button"
              class="btn btn-primary btn-sm"
              :disabled="!editingText.trim()"
              @click="saveCaption"
            >保存</button>
          </span>
        </template>
        <template v-else>
          <span class="mark-label">{{ markLabel(mark) }}</span>
          <!-- 长批注不再把「删除标注」挤成两行：文本区可换行、按钮不收缩。 -->
          <span class="mark-acts">
            <button
              v-if="mark.kind === 'caption'"
              type="button"
              class="btn btn-sm"
              @click="startEditCaption(index)"
            >改文字</button>
            <button type="button" class="btn btn-sm" @click="removeMark(index)">删除标注</button>
          </span>
        </template>
      </li>
    </ul>

    <div class="editor-actions">
      <button type="button" class="btn" @click="emit('cancel')">取消</button>
      <button type="submit" class="btn btn-primary" :disabled="!canSave || savingMarkup">
        {{ markupEditing ? (savingMarkup ? '保存中…' : '保存标注') : (replacingItem ? '保存新标准图' : '新增检查项') }}
      </button>
    </div>
  </form>
</template>

<style scoped>
/* 编辑器本体：位置由父层决定（在被编辑项下面），这里只管内部节奏与手机可用性。 */
.item-editor { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px 14px; border-top: 1px solid var(--wb-ink-a08); }
.wbx-subhead { margin: 0; display: flex; align-items: center; gap: 8px; font: 600 11px/1 var(--font-sans); letter-spacing: .18em; text-transform: uppercase; color: var(--ink-faint); }
.led { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: 0 0 6px; background: var(--line-strong); }
.led.sig { background: var(--action); }
.editor-lead { margin: 0; font-size: 10.5px; line-height: 1.6; color: var(--ink-dim); }
.editor-field { display: flex; flex-direction: column; gap: 6px; font-size: 10px; color: var(--ink-dim); }
.editor-hint { font: 500 9.5px/1.5 var(--font-mono); color: var(--ink-faint); }
.editor-actions { display: flex; justify-content: flex-end; gap: 8px; }
.wbx-error { margin: 0; font-size: 10.5px; color: var(--danger-bright); }
.mark-tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.mark-tools .input { min-width: 180px; flex: 1; }
.mark-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.mark-list li {
  display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
  font-size: 10px; color: var(--ink-dim);
}
.mark-label { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; line-height: 1.5; }
.mark-acts { flex: none; display: flex; gap: 6px; }
.mark-acts .btn { white-space: nowrap; padding: 0 10px; }
.mark-edit { flex: 1 1 auto; min-width: 0; }
.item-editor :deep(.btn) { min-height: 44px !important; border-radius: var(--r-1); }

@media (max-width: 720px) {
  .mark-tools { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 2px; }
  .mark-tools .input { min-width: 140px; }
  .mark-acts .btn { font-size: 11px; padding: 0 8px; }
  .editor-canvas { max-height: 45vh; overflow: auto; }
  .editor-actions {
    position: sticky; bottom: 0;
    padding: 8px 0 2px;
    background: var(--surface-1);
    border-top: 1px solid var(--line);
  }
  .editor-actions .btn { flex: 1 1 0; }
}
</style>
