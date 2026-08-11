import { computed, reactive, ref } from 'vue'
import { api } from '../api/client'
import {
  SAVED_VALUE_SLOT,
  buildLoginSignature,
  parseTargetUrl,
  sigField,
} from '../utils/posCredentials'

/** Setup page — POS credentials panel. */
export function usePosCredentials({ showAlert, clearAlert }) {
  const configured = ref(false)
  const meta = ref(null)
  const credForm = reactive({
    phone: '',
    password: '',
    targetUrl: '',
    shopId: '',
    companyId: '',
    shopName: '',
    deliveryShopId: '',
  })
  const phonePlaceholder = ref('11 位手机号')
  const showPassword = ref(false)
  const verifying = ref(false)
  const discoveringShops = ref(false)
  const discoveredShops = ref([])
  const saving = ref(false)
  const loginVerifiedSignature = ref('')

  const togglePwdLabel = computed(() => (showPassword.value ? '隐藏' : '显示'))
  const verifyBtnLabel = computed(() => (verifying.value ? '验证中…' : '验证登录'))
  const discoverBtnLabel = computed(() => (discoveringShops.value ? '拉取中…' : '从账号拉取门店'))
  const saveBtnLabel = computed(() => (saving.value ? '保存中…' : '保存并启用'))

  const metaItems = computed(() => {
    const data = meta.value
    if (!data || !data.configured) return null
    return [
      ['账号', data.phone || '—'],
      ['shop_id', data.shop_id || '—'],
      ['company_id', data.company_id || '—'],
      ['delivery_shop_id', data.delivery_shop_id || '—'],
      ['门店名称', data.shop_name || '—'],
      ['更新时间', data.updated_at ? String(data.updated_at).replace('T', ' ').slice(0, 19) : '—'],
    ]
  })

  function resetVerifiedSignature() {
    loginVerifiedSignature.value = ''
  }

  function fillForm(data) {
    credForm.shopId = data.shop_id || ''
    credForm.companyId = data.company_id || ''
    credForm.deliveryShopId = data.delivery_shop_id || ''
    credForm.shopName = data.shop_name || ''
  }

  function resetCredForm() {
    credForm.phone = ''
    credForm.password = ''
    credForm.targetUrl = ''
    credForm.shopId = ''
    credForm.companyId = ''
    credForm.shopName = ''
    credForm.deliveryShopId = ''
  }

  async function fetchCredentialsMeta() {
    try {
      return await api.get('/api/credentials', null, null, 'no-store')
    } catch (_) {
      return {}
    }
  }

  async function fetchCurrent() {
    clearAlert()
    try {
      const data = await api.get('/api/credentials', null, null, 'no-store')
      configured.value = !!data.configured
      meta.value = data.configured ? data : null
      if (data.configured) {
        fillForm(data)
        credForm.phone = ''
        phonePlaceholder.value = `当前账号 ${data.phone || ''}（留空则保持不变）`
      } else {
        phonePlaceholder.value = '11 位手机号'
      }
    } catch (err) {
      configured.value = false
      meta.value = null
      showAlert('error', '加载当前凭据失败：' + err.message)
    }
  }

  function applyDiscoveredShop(shop) {
    if (!shop) return
    credForm.shopId = shop.shop_id || ''
    credForm.companyId = shop.company_id || ''
    credForm.shopName = shop.shop_name || ''
    credForm.deliveryShopId = shop.delivery_shop_id || shop.company_id || ''
    if (shop.table_list_url) credForm.targetUrl = shop.table_list_url
    resetVerifiedSignature()
  }

  function onParseUrl() {
    const raw = credForm.targetUrl.trim()
    const parsed = parseTargetUrl(raw)
    if (!parsed) {
      showAlert('error', '无法解析：请粘贴 App 内真实的 tableList / tableStateInfo URL，或改用「从账号拉取门店」')
      return
    }
    applyDiscoveredShop(parsed)
    showAlert('info', '已解析并填充门店信息，请核对后保存')
  }

  async function onDiscoverShops() {
    clearAlert()
    const credMeta = await fetchCredentialsMeta()
    const metaConfigured = credMeta.configured === true
    const phoneInput = credForm.phone.trim()
    const password = credForm.password

    if (!metaConfigured) {
      if (!phoneInput) {
        showAlert('error', '请先填写龙管家 2.0 手机号')
        return
      }
      if (!password) {
        showAlert('error', '请先填写密码')
        return
      }
    }

    discoveringShops.value = true
    discoveredShops.value = []
    try {
      const payload = {}
      if (phoneInput) payload.phone = phoneInput
      if (password) payload.password = password
      const data = await api.post('/api/credentials/discover-shops', payload)
      if (!data.success || !data.ok) {
        throw new Error(data.message || '拉取门店失败')
      }
      const shops = Array.isArray(data.shops) ? data.shops : []
      if (!shops.length) {
        throw new Error('未返回可管理门店')
      }
      discoveredShops.value = shops
      applyDiscoveredShop(shops[0])
      const suffix = shops.length > 1 ? `（共 ${shops.length} 个，已选第一个，可在下方切换）` : ''
      showAlert('success', `已拉取门店：${shops[0].shop_name || shops[0].shop_id}${suffix}`)
    } catch (err) {
      discoveredShops.value = []
      showAlert('error', '拉取门店失败：' + err.message)
    } finally {
      discoveringShops.value = false
    }
  }

  function onPickDiscoveredShop(event) {
    const idx = Number(event.target.value)
    const shop = discoveredShops.value[idx]
    applyDiscoveredShop(shop)
    if (shop?.shop_name) {
      showAlert('info', `已切换门店：${shop.shop_name}`)
    }
  }

  function togglePasswordVisibility() {
    showPassword.value = !showPassword.value
  }

  async function verifyLoginBeforeSave() {
    const credMeta = await fetchCredentialsMeta()
    const metaConfigured = credMeta.configured === true

    const phoneInput = credForm.phone.trim()
    const password = credForm.password
    const shopId = credForm.shopId.trim()
    const companyId = credForm.companyId.trim()
    const shopName = credForm.shopName.trim()
    const deliveryShopId = credForm.deliveryShopId.trim()

    if (!metaConfigured) {
      if (!phoneInput) {
        showAlert('error', '请先填写手机号再验证登录')
        return false
      }
      if (!password) {
        showAlert('error', '请先填写密码再验证登录')
        return false
      }
      if (!shopId || !companyId || !shopName) {
        showAlert('error', '请先填写 shop_id、company_id 和门店名称（与餐桌列表一致），再验证登录')
        return false
      }
    }

    verifying.value = true
    try {
      const verifyPayload = {}
      if (phoneInput) verifyPayload.phone = phoneInput
      if (password) verifyPayload.password = password
      if (shopId) verifyPayload.shop_id = shopId
      if (companyId) verifyPayload.company_id = companyId
      if (shopName) verifyPayload.shop_name = shopName
      if (deliveryShopId) verifyPayload.delivery_shop_id = deliveryShopId

      const data = await api.post('/api/credentials/verify-login', verifyPayload)
      if (!data.login_ok) {
        loginVerifiedSignature.value = ''
        throw new Error(data.message || '登录验证未通过')
      }
      const deliveryForSig = deliveryShopId || companyId || (metaConfigured ? SAVED_VALUE_SLOT : '')
      loginVerifiedSignature.value = buildLoginSignature(
        sigField(phoneInput, metaConfigured),
        sigField(password, metaConfigured),
        sigField(shopId, metaConfigured),
        sigField(companyId, metaConfigured),
        sigField(shopName, metaConfigured),
        sigField(deliveryForSig, metaConfigured),
      )
      const settledHint = data.settled_bill_count != null
        ? `，已结账单 ${data.settled_bill_count} 条`
        : ''
      showAlert('success', (data.message || '登录与 API 校验通过') + settledHint + '，可安全保存')
      return true
    } catch (err) {
      loginVerifiedSignature.value = ''
      showAlert('error', '登录验证失败：' + err.message)
      return false
    } finally {
      verifying.value = false
    }
  }

  async function onVerify() {
    await verifyLoginBeforeSave()
  }

  async function onSubmitCred() {
    clearAlert()
    const credMeta = await fetchCredentialsMeta()
    const phoneInput = credForm.phone.trim()
    const passwordInput = credForm.password

    if (!credMeta.configured && !phoneInput) {
      showAlert('error', '首次配置必须填写手机号')
      return
    }
    if (!credMeta.configured && !passwordInput) {
      showAlert('error', '首次配置必须填写密码')
      return
    }

    const body = {
      phone: phoneInput || (credMeta.phone || '').replace(/\*/g, '') || null,
      password: passwordInput || null,
      shop_id: credForm.shopId.trim(),
      company_id: credForm.companyId.trim(),
      shop_name: credForm.shopName.trim(),
      delivery_shop_id: credForm.deliveryShopId.trim() || null,
    }

    if (!body.phone || /\*/.test(body.phone)) {
      showAlert('error', '请输入完整手机号（无法基于脱敏值更新）')
      return
    }
    if (!body.password && !credMeta.configured) {
      showAlert('error', '首次配置必须填写密码')
      return
    }

    const metaConfiguredSave = credMeta.configured === true
    const deliveryRaw = credForm.deliveryShopId.trim()
    const deliveryForSig = deliveryRaw || body.company_id || (metaConfiguredSave ? SAVED_VALUE_SLOT : '')
    const loginSignature = buildLoginSignature(
      sigField(phoneInput, metaConfiguredSave),
      sigField(passwordInput, metaConfiguredSave),
      sigField(body.shop_id, metaConfiguredSave),
      sigField(body.company_id, metaConfiguredSave),
      sigField(body.shop_name, metaConfiguredSave),
      sigField(deliveryForSig, metaConfiguredSave),
    )
    if (loginVerifiedSignature.value !== loginSignature) {
      const verified = await verifyLoginBeforeSave()
      if (!verified) return
    }

    saving.value = true
    try {
      await api.post('/api/credentials', body)
      showAlert('success', '保存成功！爬虫将自动重新登录采集数据。')
      credForm.password = ''
      await fetchCurrent()
    } catch (err) {
      showAlert('error', '保存失败：' + err.message)
    } finally {
      saving.value = false
    }
  }

  async function onClearCredentials() {
    if (!window.confirm('确认清空当前登录凭据？爬虫将进入待机状态，直到下次保存新凭据。')) return
    try {
      await api.delete('/api/credentials')
      showAlert('success', '已清空凭据，爬虫进入待机')
      resetCredForm()
      await fetchCurrent()
    } catch (err) {
      showAlert('error', '清空失败：' + err.message)
    }
  }

  return {
    configured,
    meta,
    credForm,
    phonePlaceholder,
    showPassword,
    verifying,
    discoveringShops,
    discoveredShops,
    saving,
    loginVerifiedSignature,
    togglePwdLabel,
    verifyBtnLabel,
    discoverBtnLabel,
    saveBtnLabel,
    metaItems,
    resetVerifiedSignature,
    fetchCurrent,
    onParseUrl,
    onDiscoverShops,
    onPickDiscoveredShop,
    togglePasswordVisibility,
    onVerify,
    onSubmitCred,
    onClearCredentials,
  }
}
