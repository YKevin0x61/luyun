import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'
import { formatTs } from '../utils/backupProgress'

/** Setup page — session / password / API tokens. */
export function useAccountSettings({ showAlert, clearAlert }) {
  const sessionUserHint = ref('加载中…')

  async function loadSessionInfo() {
    try {
      const data = await api.get('/api/auth/status')
      if (!data.logged_in) {
        sessionUserHint.value = '当前未登录，请刷新页面或重新登录。'
        return
      }
      sessionUserHint.value = data.username
        ? `已登录为 ${data.username}。退出后需重新输入密码才能访问管理页面。`
        : '已登录。退出后需重新输入密码才能访问管理页面。'
    } catch (err) {
      // Previous raw fetch treated any non-OK as "未登录"; keep that for HTTP errors.
      if (err && err.status) {
        sessionUserHint.value = '当前未登录，请刷新页面或重新登录。'
        return
      }
      sessionUserHint.value = '无法获取登录状态'
    }
  }

  async function handleLogout() {
    if (!window.confirm('确定要退出登录吗？')) return
    try {
      await api.post('/api/auth/logout')
    } catch (err) {
      showAlert('error', '退出失败：' + (err.message || '未知错误'))
      return
    }
    window.location.href = '/login'
  }

  const changePwdForm = reactive({ oldPassword: '', newPassword: '' })
  const changingPwd = ref(false)
  const changePwdBtnLabel = computed(() => (changingPwd.value ? '更新中…' : '更新密码'))

  async function onChangePassword() {
    clearAlert()
    changingPwd.value = true
    try {
      await api.post('/api/auth/change-password', {
        old_password: changePwdForm.oldPassword,
        new_password: changePwdForm.newPassword,
      })
      showAlert('success', '密码已更新')
      changePwdForm.oldPassword = ''
      changePwdForm.newPassword = ''
    } catch (err) {
      showAlert('error', '修改密码失败：' + err.message)
    } finally {
      changingPwd.value = false
    }
  }

  const tokenLabel = ref('')
  const tokens = ref([])
  const tokensLoading = ref(false)
  const tokensError = ref('')
  const generatingToken = ref(false)
  const genTokenBtnLabel = computed(() => (generatingToken.value ? '生成中…' : '生成新 Token'))

  async function loadTokenList() {
    tokensLoading.value = true
    tokensError.value = ''
    try {
      const data = await api.get('/api/auth/tokens')
      tokens.value = data.tokens || []
    } catch (err) {
      tokensError.value = err.message || '加载失败'
    } finally {
      tokensLoading.value = false
    }
  }

  const tokenModal = reactive({ show: false, plaintext: '' })
  const copyLabel = ref('复制')

  async function genToken() {
    clearAlert()
    generatingToken.value = true
    try {
      const label = tokenLabel.value.trim()
      const data = await api.post('/api/auth/token', { label })
      tokenModal.plaintext = data.api_token
      tokenModal.show = true
      tokenLabel.value = ''
      await loadTokenList()
    } catch (err) {
      showAlert('error', '生成 Token 失败：' + err.message)
    } finally {
      generatingToken.value = false
    }
  }

  async function revokeToken(prefix) {
    if (!window.confirm(`确认撤销 Token ${prefix}… ？`)) return
    try {
      await api.delete('/api/auth/token/' + encodeURIComponent(prefix))
      showAlert('success', 'Token 已撤销')
      await loadTokenList()
    } catch (err) {
      showAlert('error', '撤销失败：' + err.message)
    }
  }

  function closeTokenModal() {
    tokenModal.show = false
    tokenModal.plaintext = ''
    copyLabel.value = '复制'
  }

  async function copyToken() {
    try {
      await navigator.clipboard.writeText(tokenModal.plaintext)
      copyLabel.value = '已复制'
      setTimeout(() => {
        copyLabel.value = '复制'
      }, 2000)
    } catch (_) {
      showAlert('error', '复制失败，请手动选择复制')
    }
  }

  return {
    sessionUserHint,
    loadSessionInfo,
    handleLogout,
    changePwdForm,
    changingPwd,
    changePwdBtnLabel,
    onChangePassword,
    tokenLabel,
    tokens,
    tokensLoading,
    tokensError,
    generatingToken,
    genTokenBtnLabel,
    loadTokenList,
    formatTs,
    tokenModal,
    copyLabel,
    genToken,
    revokeToken,
    closeTokenModal,
    copyToken,
  }
}
