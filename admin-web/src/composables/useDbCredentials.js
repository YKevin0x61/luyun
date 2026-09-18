import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'

// 短于该阈值就提示重置：现场出现过 3 位密码，这种强度需要醒目警示。
export const MIN_RECOMMENDED_PASSWORD_LENGTH = 16

/**
 * Setup page —「数据库凭据」面板：查看 PostgreSQL 连接信息、重置业务角色密码。
 *
 * 安全约定（与后端一致）：响应永不含密码明文，页面也不提供显示 / 索取新密码的入口；
 * 重置必须二次确认当前后台管理员密码，新密码只写入后端的 env 文件。
 */
export function useDbCredentials({ showAlert, clearAlert } = {}) {
  const dbCred = ref(null)
  const dbCredLoading = ref(false)
  const dbCredError = ref('')

  const dbIsPostgres = computed(() => !!dbCred.value?.is_postgres)
  const dbEnvOverride = computed(() => !!dbCred.value?.env_override)
  const dbPasswordLength = computed(() =>
    typeof dbCred.value?.password_length === 'number' ? dbCred.value.password_length : null,
  )
  // 0 位 = 连接串里根本没有密码（本机 trust 认证等），后端会直接拒绝重置。
  const dbNoPassword = computed(() => dbPasswordLength.value === 0)
  // 过短警示只对「有密码但太短」出现；0 位走「无需也无法重置」的说明文案。
  const dbShortPassword = computed(
    () =>
      dbPasswordLength.value !== null &&
      dbPasswordLength.value > 0 &&
      dbPasswordLength.value < MIN_RECOMMENDED_PASSWORD_LENGTH,
  )
  const dbEnvFileWritable = computed(() => dbCred.value?.env_file_writable !== false)
  const dbPasswordLengthLabel = computed(() =>
    dbPasswordLength.value === null ? '—' : `${dbPasswordLength.value} 位`,
  )

  const dbPasswordWarning = computed(() => {
    if (!dbCred.value) return ''
    if (dbNoPassword.value) {
      return '当前连接串未包含密码（本机 trust 认证等），无需也无法在此重置。'
    }
    if (dbShortPassword.value) {
      return `当前数据库密码仅 ${dbPasswordLength.value} 位，建议重置为 32 位随机密码。`
    }
    return ''
  })
  const dbPasswordWarningType = computed(() => (dbShortPassword.value ? 'error' : 'info'))

  // 会必然失败的重置按钮直接禁用，避免点了才报错。
  const dbResetDisabledReason = computed(() => {
    if (!dbCred.value) return '数据库连接信息尚未加载完成'
    if (dbEnvOverride.value) {
      return '检测到环境变量被显式设置，其优先级高于 env 文件，写文件不生效；请先移除该环境变量'
    }
    if (!dbIsPostgres.value) return '当前为 SQLite 后端，无需数据库密码'
    if (dbNoPassword.value) return '当前连接串未包含密码（本机 trust 认证等），无法在此重置'
    return ''
  })
  const dbResetDisabled = computed(() => !!dbResetDisabledReason.value)

  async function loadDbCred() {
    clearAlert?.()
    dbCredLoading.value = true
    dbCredError.value = ''
    try {
      dbCred.value = await api.get('/api/admin/db-credentials')
    } catch (err) {
      dbCredError.value = err?.message || '加载失败'
    } finally {
      dbCredLoading.value = false
    }
  }

  // ==================== 二次确认 + 管理员密码 ====================
  const dbResetConfirm = reactive({ open: false, password: '', error: '' })
  const dbResetShowPassword = ref(false)
  const dbResetting = ref(false)
  const dbResetBtnLabel = computed(() => (dbResetting.value ? '重置中…' : '确认重置'))
  const dbResetToggleLabel = computed(() => (dbResetShowPassword.value ? '隐藏' : '显示'))

  const dbResetResult = reactive({
    show: false,
    user: '',
    envFile: '',
    passwordLength: null,
    restartTriggered: false,
    restartError: '',
  })

  function openDbReset() {
    if (dbResetDisabled.value || dbResetting.value) return
    dbResetConfirm.open = true
    dbResetConfirm.password = ''
    dbResetConfirm.error = ''
    dbResetShowPassword.value = false
  }

  function closeDbReset() {
    dbResetConfirm.open = false
    dbResetConfirm.password = ''
    dbResetConfirm.error = ''
  }

  async function dbResetSubmit() {
    if (dbResetting.value) return
    if (!dbResetConfirm.password) {
      dbResetConfirm.error = '请输入当前后台管理员密码'
      return
    }
    clearAlert?.()
    dbResetting.value = true
    dbResetConfirm.error = ''
    try {
      const data = await api.post('/api/admin/db-credentials/reset', {
        confirm_password: dbResetConfirm.password,
      })
      // 响应里的 password_length 是重置后的权威值；重启期间不再回查，避免把面板打成加载失败。
      if (dbCred.value && typeof data.password_length === 'number') {
        dbCred.value.password_length = data.password_length
      }
      dbResetResult.show = true
      dbResetResult.user = data.user || ''
      dbResetResult.envFile = data.env_file || ''
      dbResetResult.passwordLength = typeof data.password_length === 'number' ? data.password_length : null
      // restart_scheduled 是新字段（重启安排在响应之后）；restart_triggered 兼容旧后端
      dbResetResult.restartTriggered = !!(data.restart_scheduled || data.restart_triggered)
      dbResetResult.restartError = data.restart_error || ''
      closeDbReset()
      showAlert?.('success', '数据库密码已重置，新密码已写入 env 文件')
    } catch (err) {
      // 后端 detail 已是中文（403 密码不对 / 400 环境变量优先等），原文展示，保持弹窗以便重试。
      const status = err?.status
      if (status === undefined || status === null || status >= 502) {
        // 没有状态码（网络错误）或 502/503/504：很可能是重启把响应切断了 —— 反代返回的
        // 是 HTML 错误页，直接贴出来只会让人更迷惑，这里给可执行的判断依据。
        dbResetConfirm.error =
          '没有收到服务器响应（应用可能正在重启）。密码可能已经重置成功：请稍后刷新页面，' +
          '并在服务器日志里确认是否出现「数据库密码已重置」。'
      } else {
        dbResetConfirm.error = err?.message || '重置失败'
      }
      showAlert?.('error', '重置未确认：' + dbResetConfirm.error)
    } finally {
      dbResetting.value = false
    }
  }

  return {
    dbCred,
    dbCredLoading,
    dbCredError,
    loadDbCred,
    dbIsPostgres,
    dbEnvOverride,
    dbPasswordLength,
    dbPasswordLengthLabel,
    dbNoPassword,
    dbShortPassword,
    dbEnvFileWritable,
    dbPasswordWarning,
    dbPasswordWarningType,
    dbResetDisabled,
    dbResetDisabledReason,
    dbResetConfirm,
    dbResetShowPassword,
    dbResetToggleLabel,
    dbResetting,
    dbResetBtnLabel,
    openDbReset,
    closeDbReset,
    dbResetSubmit,
    dbResetResult,
  }
}
