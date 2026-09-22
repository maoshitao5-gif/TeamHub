import axios from 'axios'

// 读取云后端地址（优先 env，其次 localStorage，最后默认值）
const BASE_URL =
  import.meta.env.VITE_CLOUD_API_URL ||
  localStorage.getItem('cloud_api_url') ||
  'http://localhost:9000'

const adminRequest = axios.create({
  baseURL: BASE_URL,
  timeout: 15000,
})

// 请求拦截器：自动附加 JWT
adminRequest.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：401 清除 token 并跳登录
adminRequest.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('admin_token')
      localStorage.removeItem('admin_user')
      window.location.hash = '#/login'
    }
    return Promise.reject(err)
  }
)

// ========== Auth ==========
export const login = (email, password) =>
  adminRequest.post('/api/auth/login', { email, password })

// ========== 统计 ==========
export const getStats = () => adminRequest.get('/api/admin/stats')

// ========== 用户管理 ==========
export const getUsers = (params) => adminRequest.get('/api/admin/users', { params })
export const updateUser = (id, data) => adminRequest.put(`/api/admin/users/${id}`, data)
export const deleteUser = (id) => adminRequest.delete(`/api/admin/users/${id}`)

// ========== 团队管理 ==========
export const getTeams = (params) => adminRequest.get('/api/admin/teams', { params })
export const updateTeam = (id, data) => adminRequest.put(`/api/admin/teams/${id}`, data)
export const deleteTeam = (id) => adminRequest.delete(`/api/admin/teams/${id}`)
