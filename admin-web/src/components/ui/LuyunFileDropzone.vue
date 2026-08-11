<script setup>
import { ref } from 'vue'
import SvgIcon from '../SvgIcon.vue'

const props = defineProps({
  accept: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: '拖拽文件到此处，或点击选择' },
  hint: { type: String, default: '' },
})

const emit = defineEmits(['change'])

const inputRef = ref(null)
const dragging = ref(false)
const fileName = ref('')

function openPicker() {
  if (props.disabled) return
  inputRef.value?.click()
}

function onInputChange(event) {
  const file = event.target.files?.[0] ?? null
  fileName.value = file?.name ?? ''
  emit('change', file)
}

function onDrop(event) {
  event.preventDefault()
  dragging.value = false
  if (props.disabled) return
  const file = event.dataTransfer?.files?.[0] ?? null
  if (!file) return
  fileName.value = file.name
  emit('change', file)
}

function onDragOver(event) {
  event.preventDefault()
  if (!props.disabled) dragging.value = true
}

function onDragLeave() {
  dragging.value = false
}
</script>

<template>
  <div
    class="luyun-dropzone"
    :class="{ 'is-dragging': dragging, 'is-disabled': disabled, 'has-file': !!fileName }"
    role="button"
    tabindex="0"
    @click="openPicker"
    @keydown.enter.prevent="openPicker"
    @keydown.space.prevent="openPicker"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <input
      ref="inputRef"
      type="file"
      class="luyun-dropzone__input"
      :accept="accept"
      :disabled="disabled"
      @change="onInputChange"
    />
    <SvgIcon name="upload" :size="22" />
    <div class="luyun-dropzone__label">{{ fileName || label }}</div>
    <div v-if="hint && !fileName" class="luyun-dropzone__hint">{{ hint }}</div>
  </div>
</template>
