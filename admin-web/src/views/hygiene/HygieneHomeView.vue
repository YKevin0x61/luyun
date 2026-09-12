<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import HygieneStandardOverlay from '../../components/hygiene/HygieneStandardOverlay.vue'
import {
  HYGIENE_SHIFTS,
  hygienePermissionLabel,
  hygieneShiftLabel,
} from '../../utils/hygieneCopy'
import { standardImageUrl } from '../../utils/hygieneMarkup'
import { staffRequest } from '../../utils/hygieneStaff'

const router = useRouter()
const employee = ref(null)
const errorText = ref('')
const loggingOut = ref(false)
const picking = ref('')
const catalog = ref([])
const previewItem = ref(null)

const needsShiftPick = computed(() => {
  return Boolean(employee.value) && !employee.value.shift
})

onMounted(loadMe)

async function loadMe() {
  try {
    const data = await staffRequest('/api/hygiene/staff/me')
    employee.value = data.employee
    if (data.employee && data.employee.shift) {
      try {
        await loadCatalog()
      } catch (err) {
        errorText.value = err.message || '无法加载日常清单'
      }
    } else {
      catalog.value = []
    }
  } catch (err) {
    errorText.value = err.message || '无法读取登录状态'
    router.replace('/hygiene/login')
  }
}

async function loadCatalog() {
  const data = await staffRequest('/api/hygiene/staff/daily-catalog')
  catalog.value = data.zones || []
}

async function pickShift(shift) {
  if (picking.value) return
  errorText.value = ''
  picking.value = shift
  try {
    await staffRequest('/api/hygiene/staff/shift', {
      method: 'POST',
      body: { shift },
    })
    await loadMe()
  } catch (err) {
    errorText.value = err.message || '选班失败'
  } finally {
    picking.value = ''
  }
}

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    await staffRequest('/api/hygiene/staff/logout', { method: 'POST' })
  } catch {
    // Session may already be gone; still leave the phone entry.
  }
  router.replace('/hygiene/login')
}
</script>

<template>
  <div class="staff-phone">
    <div class="staff-card">
      <p class="staff-brand">LuckIn<span>卫生</span></p>
      <template v-if="needsShiftPick">
        <h1 class="staff-title">今天上哪一班？</h1>
        <p class="staff-lead">选一次就锁在这个营业日。白班和夜班的日常检查分开交，选错了要找超级管理员改。</p>
        <p v-if="errorText" class="staff-alert">{{ errorText }}</p>
        <div class="staff-shift-choices">
          <button
            v-for="shift in HYGIENE_SHIFTS"
            :key="shift"
            type="button"
            class="btn staff-shift-btn"
            :class="{ 'btn-primary': shift === '白班', 'staff-shift-night': shift === '夜班' }"
            :disabled="Boolean(picking)"
            @click="pickShift(shift)"
          >
            {{ picking === shift ? '正在锁定…' : shift }}
          </button>
        </div>
        <button type="button" class="btn btn-block staff-submit staff-chooser-logout" :disabled="loggingOut" @click="logout">
          退出登录
        </button>
      </template>
      <template v-else>
        <h1 class="staff-title">卫生入口</h1>
        <p v-if="errorText" class="staff-alert">{{ errorText }}</p>
        <template v-else-if="employee">
          <p class="staff-hello">{{ employee.phone }}</p>
          <dl class="staff-meta">
            <div>
              <dt>当天班次</dt>
              <dd>{{ hygieneShiftLabel(employee.shift) }}</dd>
            </div>
            <div>
              <dt>职位</dt>
              <dd>{{ employee.job_title || '未设置' }}</dd>
            </div>
            <div>
              <dt>卫生权限</dt>
              <dd>{{ hygienePermissionLabel(employee.permission) }}</dd>
            </div>
          </dl>
          <p class="staff-lead">今天班次已锁定。白班夜班看同一套日常清单，没有当前标准图的检查项不会出现。点一项先看标准图，拍照交单还没开。</p>
          <div v-if="catalog.length" class="staff-catalog">
            <section v-for="zone in catalog" :key="zone.id" class="staff-zone">
              <h2>{{ zone.name }}</h2>
              <p v-if="!(zone.items || []).length" class="staff-zone-empty">这个区还没有带标准图的检查项</p>
              <button
                v-for="item in zone.items"
                :key="item.id"
                type="button"
                class="staff-item"
                @click="previewItem = item"
              >
                {{ item.name }}
              </button>
            </section>
          </div>
          <button type="button" class="btn btn-block staff-submit" :disabled="loggingOut" @click="logout">
            退出登录
          </button>
        </template>
        <p v-else class="staff-lead">正在确认登录…</p>
      </template>
    </div>
    <div
      v-if="previewItem"
      class="staff-preview"
      role="dialog"
      aria-modal="true"
      :aria-label="previewItem.name"
      @click.self="previewItem = null"
    >
      <div class="staff-preview-card">
        <h2>{{ previewItem.name }}</h2>
        <p class="staff-lead">先看标准图。现场拍照交单下一张票再做。</p>
        <HygieneStandardOverlay
          :src="standardImageUrl('staff', previewItem)"
          :markup="previewItem.markup || []"
          :alt="previewItem.name"
        />
        <button type="button" class="btn btn-block staff-submit" @click="previewItem = null">关掉</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.staff-phone {
  min-height: 100%;
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: max(20px, env(safe-area-inset-top)) 16px max(24px, env(safe-area-inset-bottom));
}
.staff-card {
  width: 100%;
  max-width: 420px;
  margin: auto 0;
  background: rgba(17, 24, 39, 0.92);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 28px 22px;
}
.staff-brand {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  margin: 0 0 10px;
}
.staff-brand span { color: var(--accent); margin-left: 6px; }
.staff-title { font-size: 22px; margin: 0 0 8px; }
.staff-hello {
  font-size: 20px;
  font-variant-numeric: tabular-nums;
  margin: 0 0 16px;
}
.staff-meta {
  display: grid;
  gap: 10px;
  margin: 0 0 16px;
  padding: 12px 14px;
  background: var(--card2);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.staff-meta div { display: flex; justify-content: space-between; gap: 12px; }
.staff-meta dt { color: var(--text-dim); font-size: 13px; }
.staff-meta dd { margin: 0; font-size: 14px; }
.staff-lead {
  color: var(--text-dim);
  font-size: 14px;
  line-height: 1.55;
  margin: 0 0 20px;
}
.staff-alert {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  margin: 0 0 16px;
}
.staff-submit { min-height: 48px; font-size: 16px; }
.staff-shift-choices {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.staff-shift-btn {
  min-height: 56px;
  font-size: 18px;
  font-weight: 600;
}
.staff-shift-night {
  background: #1e293b;
  border-color: #334155;
  color: #e2e8f0;
}
.staff-shift-night:hover:not(:disabled) {
  border-color: var(--cyan);
  color: #fff;
}
.staff-chooser-logout { margin-top: 16px; }
.staff-catalog {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0 0 20px;
}
.staff-zone h2 {
  margin: 0 0 8px;
  font-size: 15px;
}
.staff-zone-empty {
  margin: 0;
  color: var(--text-dim);
  font-size: 13px;
}
.staff-item {
  display: block;
  width: 100%;
  text-align: left;
  margin: 0 0 6px;
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--card2);
  color: var(--text);
  font-size: 15px;
  font-family: inherit;
  cursor: pointer;
}
.staff-item:hover { border-color: var(--accent); }
.staff-preview {
  position: fixed;
  inset: 0;
  background: rgba(2, 6, 23, 0.72);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 16px;
  z-index: 20;
}
.staff-preview-card {
  width: 100%;
  max-width: 420px;
  background: rgba(17, 24, 39, 0.96);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 16px 20px;
}
.staff-preview-card h2 {
  margin: 0 0 6px;
  font-size: 18px;
}
.staff-preview-card .staff-submit { margin-top: 14px; }
</style>
