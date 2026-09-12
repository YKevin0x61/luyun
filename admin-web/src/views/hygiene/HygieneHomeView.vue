<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { hygienePermissionLabel } from '../../utils/hygieneCopy'
import { staffRequest } from '../../utils/hygieneStaff'

const router = useRouter()
const employee = ref(null)
const errorText = ref('')
const loggingOut = ref(false)

onMounted(async () => {
  try {
    const data = await staffRequest('/api/hygiene/staff/me')
    employee.value = data.employee
  } catch (err) {
    errorText.value = err.message || '无法读取登录状态'
    router.replace('/hygiene/login')
  }
})

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
      <h1 class="staff-title">卫生入口</h1>
      <p v-if="errorText" class="staff-alert">{{ errorText }}</p>
      <template v-else-if="employee">
        <p class="staff-hello">{{ employee.phone }}</p>
        <dl class="staff-meta">
          <div>
            <dt>职位</dt>
            <dd>{{ employee.job_title || '未设置' }}</dd>
          </div>
          <div>
            <dt>卫生权限</dt>
            <dd>{{ hygienePermissionLabel(employee.permission) }}</dd>
          </div>
        </dl>
        <p class="staff-lead">日常检查、专项卫生和整改单会在这里出现。现在先确认账号能登录。</p>
        <button type="button" class="btn btn-block staff-submit" :disabled="loggingOut" @click="logout">
          退出登录
        </button>
      </template>
      <p v-else class="staff-lead">正在确认登录…</p>
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
</style>
