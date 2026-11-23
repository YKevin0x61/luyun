<script setup>
/**
 * 采购岗位下拉。
 *
 * 系统原生的 <select> 弹层跟 luyun 的纸色主题完全不是一套（灰底、蓝选），
 * 还把焦点边框叠成双层；这里用按钮 + 浮层自己画，键盘、触摸和读屏都按 listbox 走。
 *
 * - allowAll：主页面用，第一项是「全部岗位」（只浏览）；提交弹窗不用。
 * - inline：弹窗里用。弹窗自己 overflow:auto，绝对定位的浮层会被裁掉，改成往下撑开。
 */
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  positions: { type: Array, default: () => [] },
  allowAll: { type: Boolean, default: false },
  allLabel: { type: String, default: '全部岗位' },
  placeholder: { type: String, default: '请选择岗位' },
  ariaLabel: { type: String, default: '选择岗位' },
  inline: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  id: { type: String, default: undefined },
})

const emit = defineEmits(['update:modelValue'])

const uid = useId()
const triggerId = computed(() => props.id || `${uid}-trigger`)
const menuId = `${uid}-menu`
const optionId = (index) => `${uid}-option-${index}`

const rootRef = ref(null)
const triggerRef = ref(null)
const menuRef = ref(null)
const open = ref(false)
const activeIndex = ref(0)
const dropUp = ref(false)

const options = computed(() => {
  const list = props.positions.map((position) => ({
    value: Number(position.id),
    label: position.name,
  }))
  return props.allowAll ? [{ value: 0, label: props.allLabel }, ...list] : list
})

const selectedIndex = computed(() =>
  options.value.findIndex((option) => option.value === Number(props.modelValue)),
)
const selectedOption = computed(() => options.value[selectedIndex.value] || null)
const displayLabel = computed(() => selectedOption.value?.label || props.placeholder)
const activeDescendant = computed(() =>
  open.value ? optionId(activeIndex.value) : undefined,
)

function scrollActiveIntoView() {
  nextTick(() => {
    const el = menuRef.value?.querySelector(`[data-index="${activeIndex.value}"]`)
    el?.scrollIntoView?.({ block: 'nearest' })
  })
}

function openMenu(startOffset = 0) {
  if (props.disabled) return
  open.value = true
  const base = selectedIndex.value >= 0 ? selectedIndex.value : 0
  activeIndex.value = Math.min(Math.max(base + startOffset, 0), options.value.length - 1)
  // 窄屏 / 矮窗口下方放不下时往上翻，避免菜单被底栏或视口裁掉。
  const rect = triggerRef.value?.getBoundingClientRect()
  if (rect && !props.inline) {
    const cap = window.innerWidth <= 560
      ? Math.min(300, window.innerHeight * 0.48)
      : Math.min(360, window.innerHeight * 0.52)
    const wanted = Math.min(cap, options.value.length * 44 + 12)
    const below = window.innerHeight - rect.bottom - 10
    const above = rect.top - 10
    dropUp.value = below < wanted && above > below
  } else {
    dropUp.value = false
  }
  scrollActiveIntoView()
}

function closeMenu(restoreFocus = false) {
  if (!open.value) return
  open.value = false
  if (restoreFocus) nextTick(() => triggerRef.value?.focus())
}

function toggleMenu() {
  if (open.value) closeMenu()
  else openMenu()
}

function pick(option) {
  emit('update:modelValue', option.value)
  closeMenu(true)
}

function moveActive(step) {
  if (!options.value.length) return
  const next = activeIndex.value + step
  activeIndex.value = Math.min(Math.max(next, 0), options.value.length - 1)
  scrollActiveIntoView()
}

function onTriggerKeydown(event) {
  if (props.disabled) return
  if (event.key === 'Escape') {
    if (open.value) {
      event.preventDefault()
      closeMenu(true)
    }
    return
  }
  if (!open.value) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      openMenu(1)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      openMenu(-1)
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openMenu()
    }
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveActive(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(-1)
  } else if (event.key === 'Home') {
    event.preventDefault()
    activeIndex.value = 0
    scrollActiveIntoView()
  } else if (event.key === 'End') {
    event.preventDefault()
    activeIndex.value = options.value.length - 1
    scrollActiveIntoView()
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    const option = options.value[activeIndex.value]
    if (option) pick(option)
  } else if (event.key === 'Tab') {
    closeMenu()
  }
}

function onDocumentPointerDown(event) {
  if (!open.value) return
  if (rootRef.value?.contains(event.target)) return
  closeMenu()
}

function onDocumentKeydown(event) {
  if (event.key === 'Escape') closeMenu(true)
}

watch(open, (isOpen) => {
  if (isOpen) {
    document.addEventListener('pointerdown', onDocumentPointerDown, true)
    document.addEventListener('keydown', onDocumentKeydown)
  } else {
    document.removeEventListener('pointerdown', onDocumentPointerDown, true)
    document.removeEventListener('keydown', onDocumentKeydown)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown, true)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <div ref="rootRef" class="pos-select" :class="{ 'is-open': open, 'is-inline': inline, 'is-up': dropUp }">
    <button
      :id="triggerId"
      ref="triggerRef"
      type="button"
      class="pos-select-trigger"
      :disabled="disabled"
      aria-haspopup="listbox"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="menuId"
      :aria-label="ariaLabel"
      :aria-activedescendant="activeDescendant"
      @click="toggleMenu"
      @keydown="onTriggerKeydown"
    >
      <span class="pos-select-value" :class="{ 'is-placeholder': !selectedOption }">
        {{ displayLabel }}
      </span>
      <svg
        class="pos-select-chevron"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>

    <div
      v-if="open"
      :id="menuId"
      ref="menuRef"
      class="pos-select-menu"
      role="listbox"
      :aria-label="ariaLabel"
    >
      <div
        v-for="(option, index) in options"
        :id="optionId(index)"
        :key="option.value"
        class="pos-select-option"
        :class="{ 'is-active': index === activeIndex, 'is-selected': index === selectedIndex }"
        role="option"
        :aria-selected="index === selectedIndex ? 'true' : 'false'"
        :data-index="index"
        @click="pick(option)"
        @mouseenter="activeIndex = index"
      >
        <span class="pos-select-option-label">{{ option.label }}</span>
        <svg
          v-if="index === selectedIndex"
          class="pos-select-check"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.4"
          aria-hidden="true"
        >
          <polyline points="5 12 10 17 19 7" />
        </svg>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pos-select {
  position: relative;
  width: 100%;
}
.pos-select-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  min-height: 44px;
  padding: 0 13px 0 14px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: var(--surface-2);
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-14);
  font-weight: 600;
  line-height: 1.2;
  text-align: left;
  cursor: pointer;
}
.pos-select-trigger:hover {
  border-color: var(--ink);
}
.pos-select.is-open .pos-select-trigger {
  border-color: var(--action);
  box-shadow: 0 0 0 3px var(--wb-tint-14);
}
.pos-select-trigger:focus-visible {
  outline: 2px solid var(--action);
  outline-offset: 2px;
}
.pos-select-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.pos-select-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pos-select-value.is-placeholder {
  opacity: 0.55;
  font-weight: 500;
}
.pos-select-chevron {
  flex: none;
  width: 14px;
  height: 14px;
  opacity: 0.55;
  transition: transform 0.15s ease;
}
.pos-select.is-open .pos-select-chevron {
  transform: rotate(180deg);
}
.pos-select-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  left: 0;
  z-index: var(--z-shell);
  max-height: min(360px, 52vh);
  overflow-y: auto;
  padding: 5px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-1);
  box-shadow: var(--paper-shadow);
}
.pos-select-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 44px;
  padding: 0 10px 0 12px;
  border-radius: 8px;
  color: var(--ink);
  font-size: var(--fs-13);
  cursor: pointer;
}
.pos-select-option.is-active {
  background: var(--wb-tint-08);
}
.pos-select-option.is-selected {
  font-weight: 700;
}
.pos-select-option.is-selected .pos-select-check {
  color: var(--action);
}
.pos-select-option-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pos-select-check {
  flex: none;
  width: 16px;
  height: 16px;
}
.pos-select.is-up .pos-select-menu {
  top: auto;
  bottom: calc(100% + 6px);
}
.pos-select.is-inline .pos-select-menu {
  position: static;
  max-height: 240px;
  margin-top: 6px;
  box-shadow: none;
}
@media (max-width: 560px) {
  .pos-select-menu {
    max-height: min(300px, 48vh);
  }
}
</style>
