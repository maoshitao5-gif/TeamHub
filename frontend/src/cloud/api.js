import { reactive } from 'vue'

// 两个网页入口共用同源会话，访问令牌仅存在当前页面内存中。
export const webSession = reactive({ user: null, initialized: false, serverId: '' })
let accessToken = ''
let refreshing = null
let serverInfoRequest = null
for (const key of ['admin_token', 'admin_user', 'cloud_access_token', 'cloud_refresh_token']) {
  localStorage.removeItem(key)
}

async function parseResponse(response, blob = false) {
  if (response.ok) return response.status === 204 ? null : blob ? response.blob() : response.json()
  const payload = await response.json().catch(() => ({}))
  const detail = payload.error || payload.detail?.error || payload.detail
  const message = typeof detail === 'string' ? detail : detail?.message || `请求失败（${response.status}）`
  const error = new Error(message)
  error.status = response.status
  error.code = detail?.code
  error.response = { status: response.status, data: payload }
  throw error
}

async function sessionRequest(action, body) {
  return parseResponse(await fetch(`/api/web-auth/${action}`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-TeamHub-Web': '1' },
    body: body ? JSON.stringify(body) : undefined,
  }))
}

async function ensureServerInfo() {
  if (!serverInfoRequest) {
    serverInfoRequest = fetch('/api/server-info', { credentials: 'omit' }).then(parseResponse).then(info => {
      if (info.protocol_major !== 1 || !info.server_id) throw new Error('云端协议不兼容，请联系管理员更新服务')
      webSession.serverId = info.server_id
      return info
    }).catch(error => { serverInfoRequest = null; throw error })
  }
  return serverInfoRequest
}

function acceptSession(result) {
  accessToken = result.access_token
  webSession.user = result.user
  webSession.initialized = true
  return result
}

export async function login(email, password) {
  await ensureServerInfo()
  if (refreshing) await refreshing.catch(() => {})
  return acceptSession(await sessionRequest('login', { email, password }))
}

export function refreshSession() {
  if (!refreshing) {
    const rotate = () => sessionRequest('refresh').then(acceptSession)
    // 同一浏览器的成员页与管理页顺序轮换共享 Cookie，避免互相消费旧凭据。
    refreshing = (navigator.locks ? navigator.locks.request('teamhub-web-session', rotate) : rotate())
      .catch(error => {
        if ([401, 403].includes(error.status)) {
          accessToken = ''
          webSession.user = null
        }
        throw error
      }).finally(() => { refreshing = null; webSession.initialized = true })
  }
  return refreshing
}

export async function ensureSession() {
  await ensureServerInfo()
  if (!webSession.initialized) {
    try { await refreshSession() } catch (error) {
      if (![401, 403].includes(error.status)) throw error
    }
  }
  return webSession.user
}

export async function logout() {
  if (refreshing) await refreshing.catch(() => {})
  await sessionRequest('logout')
  accessToken = ''
  webSession.user = null
}

export async function transport(method, path, data, options = {}) {
  if (!path.startsWith('/api/') || path.startsWith('//')) throw new Error('只能访问当前云端服务')
  const url = new URL(path, window.location.origin)
  if (options.params) for (const [key, value] of Object.entries(options.params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value))
  }
  const raw = data instanceof Blob || data instanceof ArrayBuffer || data instanceof FormData
  const run = () => fetch(url, {
    method, credentials: 'same-origin', signal: options.signal,
    headers: {
      ...(data !== undefined && !raw ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers, ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: data === undefined || data === null ? undefined : raw ? data : JSON.stringify(data),
  })
  const requestUser = webSession.user?.id
  let response = await run()
  if (response.status === 401) {
    await refreshSession()
    if (requestUser && webSession.user?.id !== requestUser) throw new Error('登录账号已变化，请在当前账号下重新操作')
    response = await run()
  }
  return parseResponse(response, options.responseType === 'blob')
}

export async function saveDownload(path, filename) {
  const blob = await transport('GET', path, undefined, { responseType: 'blob' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename || 'TeamHub文件'
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
