// 只记录前端会话代次；云端凭据始终由个人后端保管。
let scope = 0

export const getCloudSessionScope = () => scope
export const advanceCloudSessionScope = () => ++scope
export const isCurrentCloudSessionScope = value => Number.isInteger(value) && value === scope

export function notifyCloudSessionExpiry(response, requestScope, target = globalThis.window) {
  const code = response?.data?.detail?.error?.code || response?.data?.error?.code
  if (response?.status !== 401 || code !== 'CLOUD_SESSION_EXPIRED' || !isCurrentCloudSessionScope(requestScope)) return false
  target?.dispatchEvent(new CustomEvent('cloud:session-expired', { detail: { scope: requestScope } }))
  return true
}
