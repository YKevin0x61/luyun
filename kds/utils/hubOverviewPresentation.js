/**
 * KDS 首页总览区呈现模式：连接健康时数字为主；断连时告警为主、数字降权。
 *
 * @param {string|undefined|null} connectionStatus realtime store status
 * @returns {{ alertPrimary: boolean, numbersTrusted: boolean }}
 */
export function hubOverviewPresentation(connectionStatus) {
  const alertPrimary = connectionStatus !== 'connected'
  return {
    alertPrimary,
    numbersTrusted: !alertPrimary
  }
}
