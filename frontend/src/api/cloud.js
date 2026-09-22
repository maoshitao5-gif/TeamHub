/**
 * 云服务 API 封装
 * 自动附加 JWT Bearer Token，401 时尝试静默续期
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'

const CLOUD_API_URL = import.meta.env.VITE_CLOUD_API_URL || 'http://localhost:9000'

const cloudRequest = axios.create({
  baseURL: CLOUD_API_URL,
  timeout: 30000,
  proxy: false,
  headers: { 'Content-Type': 'application/json; charset=utf-8' },
})

/**
 * 运行时动态设置云后端地址（从 config.json 读取后调用）
 * 用户在设置页保存云服务地址后，所有 cloud.js 请求切换到该地址
 */
export function setCloudApiUrl(url) {
  if (url) {
    cloudRequest.defaults.baseURL = url
  }
}

// ===== Token 存储（Electron: safeStorage，Web: localStorage）=====

export function saveAccessToken(token) {
  localStorage.setItem('cloud_access_token', token)
}

export function loadAccessToken() {
  return localStorage.getItem('cloud_access_token')
}

export function clearAccessToken() {
  localStorage.removeItem('cloud_access_token')
}

export function saveRefreshToken(token) {
  if (window.electron?.storeToken) {
    window.electron.storeToken('cloud_refresh_token', token)
  } else {
    localStorage.setItem('cloud_refresh_token', token)
  }
}

export async function loadRefreshToken() {
  if (window.electron?.loadToken) {
    return await window.electron.loadToken('cloud_refresh_token')
  }
  return localStorage.getItem('cloud_refresh_token')
}

export async function clearRefreshToken() {
  if (window.electron?.clearToken) {
    await window.electron.clearToken('cloud_refresh_token')
  } else {
    localStorage.removeItem('cloud_refresh_token')
  }
}

// ===== 静默续期 =====

let _refreshing = false
let _refreshSubscribers = []

function _onRefreshed(token) {
  _refreshSubscribers.forEach(cb => cb(token))
  _refreshSubscribers = []
}

async function _silentRefresh() {
  const refreshToken = await loadRefreshToken()
  if (!refreshToken) throw new Error('no_refresh_token')

  const res = await axios.post(`${cloudRequest.defaults.baseURL}/api/auth/refresh`, { refresh_token: refreshToken })
  const newToken = res.data.access_token
  saveAccessToken(newToken)
  return newToken
}

// ===== 请求拦截器：附加 Bearer Token =====

cloudRequest.interceptors.request.use((config) => {
  const token = loadAccessToken()
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// ===== 响应拦截器：401 自动续期 =====

cloudRequest.interceptors.response.use(
  (res) => res.data,
  async (error) => {
    const originalRequest = error.config

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (_refreshing) {
        // 等待已有的 refresh 完成
        return new Promise((resolve) => {
          _refreshSubscribers.push((token) => {
            originalRequest.headers['Authorization'] = `Bearer ${token}`
            resolve(cloudRequest(originalRequest))
          })
        })
      }

      originalRequest._retry = true
      _refreshing = true

      try {
        const newToken = await _silentRefresh()
        _onRefreshed(newToken)
        originalRequest.headers['Authorization'] = `Bearer ${newToken}`
        return cloudRequest(originalRequest)
      } catch (e) {
        _refreshSubscribers = []
        clearAccessToken()
        // 通知 auth store 已登出
        window.dispatchEvent(new CustomEvent('cloud:session-expired'))
        return Promise.reject(error)
      } finally {
        _refreshing = false
      }
    }

    const message = error.response?.data?.detail || error.message || '请求失败'
    if (!originalRequest._silent) {
      ElMessage.error(typeof message === 'string' ? message : JSON.stringify(message))
    }
    return Promise.reject(error)
  }
)

// ===== Auth API =====

export const authApi = {
  register: (data) => cloudRequest.post('/api/auth/register', data),
  login: (data) => cloudRequest.post('/api/auth/login', data),
  refresh: (refreshToken) => cloudRequest.post('/api/auth/refresh', { refresh_token: refreshToken }),
  logout: (refreshToken) => cloudRequest.post('/api/auth/logout', { refresh_token: refreshToken }),
}

// ===== User API =====

export const userApi = {
  getMe: () => cloudRequest.get('/api/users/me'),
  updateMe: (data) => cloudRequest.put('/api/users/me', data),
}

// ===== Team API =====

export const teamApi = {
  create: (data) => cloudRequest.post('/api/teams', data),
  list: () => cloudRequest.get('/api/teams'),
  get: (id) => cloudRequest.get(`/api/teams/${id}`),
  update: (id, data) => cloudRequest.put(`/api/teams/${id}`, data),
  delete: (id) => cloudRequest.delete(`/api/teams/${id}`),
  listMembers: (id) => cloudRequest.get(`/api/teams/${id}/members`),
  inviteMember: (id, data) => cloudRequest.post(`/api/teams/${id}/members`, data),
  updateMember: (teamId, userId, data) => cloudRequest.put(`/api/teams/${teamId}/members/${userId}`, data),
  removeMember: (teamId, userId) => cloudRequest.delete(`/api/teams/${teamId}/members/${userId}`),
}

// ===== Workspace API =====

export const workspaceApi = {
  create: (teamId, data) => cloudRequest.post(`/api/teams/${teamId}/workspaces`, data),
  list: (teamId) => cloudRequest.get(`/api/teams/${teamId}/workspaces`),
  get: (wsId) => cloudRequest.get(`/api/workspaces/${wsId}`),
  update: (wsId, data) => cloudRequest.put(`/api/workspaces/${wsId}`, data),
  delete: (wsId) => cloudRequest.delete(`/api/workspaces/${wsId}`),
}

// ===== Document API =====

export const cloudDocApi = {
  create: (wsId, data) => cloudRequest.post(`/api/workspaces/${wsId}/documents`, data),
  list: (wsId, params) => cloudRequest.get(`/api/workspaces/${wsId}/documents`, { params }),
  get: (wsId, docId) => cloudRequest.get(`/api/workspaces/${wsId}/documents/${docId}`),
  update: (wsId, docId, data) => cloudRequest.put(`/api/workspaces/${wsId}/documents/${docId}`, data),
  delete: (wsId, docId) => cloudRequest.delete(`/api/workspaces/${wsId}/documents/${docId}`),
  listTags: (wsId) => cloudRequest.get(`/api/workspaces/${wsId}/tags`),
}

// ===== Storage API =====

export const storageApi = {
  presignUpload: (data) => cloudRequest.post('/api/storage/presign-upload', data),
  presignDownload: (data) => cloudRequest.post('/api/storage/presign-download', data),
  confirmUpload: (data) => cloudRequest.post('/api/storage/confirm-upload', data),
}

// ===== Sync API =====

export const syncApi = {
  push: (wsId, data) => cloudRequest.post(`/api/workspaces/${wsId}/sync/push`, data),
  pull: (wsId, sinceSequence = 0) => cloudRequest.get(`/api/workspaces/${wsId}/sync/pull`, {
    params: { since_sequence: sinceSequence }
  }),
}

// ===== Device API =====

export const deviceApi = {
  register: (data) => cloudRequest.post('/api/devices/register', data),
  list: () => cloudRequest.get('/api/devices'),
  updateCursor: (deviceId, workspaceId, sequence) =>
    cloudRequest.put(`/api/devices/${deviceId}/cursor`, null, {
      params: { workspace_id: workspaceId, sequence }
    }),
}

export default cloudRequest
