/** Pure helpers for POS credential form (URL parse + login signature). */

export const SAVED_VALUE_SLOT = '__saved__'

/**
 * Parse a Longgj App WebView URL into shop_id / company_id / shop_name.
 * @param {string} raw
 * @returns {{ shop_id: string, company_id: string, shop_name: string } | null}
 */
export function parseTargetUrl(raw) {
  const text = String(raw || '').trim()
  if (!text || text.includes('{shopId}') || text.includes('{companyId}')) return null
  try {
    const url = text.startsWith('http') ? new URL(text) : new URL(text, 'https://cy7mm.wuuxiang.com')
    const parts = url.pathname.split('/').filter(Boolean)
    const homeIdx = parts.indexOf('home')
    if (homeIdx < 0) return null

    const route = parts[homeIdx + 1]
    let shop_id = ''
    let company_id = ''

    if (route === 'tableList' && parts.length >= homeIdx + 5) {
      shop_id = parts[homeIdx + 3] || ''
      company_id = parts[homeIdx + 4] || ''
    } else if (route === 'tableStateInfo' && parts.length >= homeIdx + 4) {
      shop_id = parts[homeIdx + 2] || ''
      company_id = parts[homeIdx + 3] || ''
    } else if (route === 'occupyTable' && parts.length >= homeIdx + 5) {
      shop_id = parts[homeIdx + 3] || ''
      company_id = parts[homeIdx + 4] || ''
    } else {
      return null
    }

    if (!/^\d+$/.test(shop_id) || !/^\d+$/.test(company_id)) return null

    return {
      shop_id,
      company_id,
      shop_name: decodeURIComponent(url.searchParams.get('shopName') || ''),
    }
  } catch (_) {
    return null
  }
}

/** Field value for signature: trim, or saved-slot when configured and empty. */
export function sigField(rawValue, metaConfigured) {
  const t = String(rawValue ?? '').trim()
  if (t) return t
  return metaConfigured ? SAVED_VALUE_SLOT : ''
}

export function buildLoginSignature(phone, password, shopId, companyId, shopName, deliveryShopId) {
  return [
    String(phone ?? '').trim(),
    String(password ?? ''),
    (shopId || '').trim(),
    (companyId || '').trim(),
    (shopName || '').trim(),
    (deliveryShopId || '').trim(),
  ].join('||')
}
