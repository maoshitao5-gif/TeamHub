/**
 * 认证相关 API
 */
import request from './request'

/**
 * 用户登录
 * @param {string} username - 用户名
 * @param {string} password - 密码
 * @returns {Promise}
 */
export function login(username, password) {
  return request.post('/login', {
    username,
    password
  })
}

/**
 * 获取当前用户信息
 * @returns {Promise}
 */
export function getCurrentUser() {
  return request.get('/auth/me')
}

/**
 * 退出登录（清除本地 token）
 */
export function logout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('username')
  localStorage.removeItem('is_admin')
}

/**
 * 设置用户信息
 */
export function setUserInfo(userInfo) {
  if (userInfo.username) {
    localStorage.setItem('username', userInfo.username)
  }
  if (userInfo.is_admin !== undefined) {
    localStorage.setItem('is_admin', userInfo.is_admin ? 'true' : 'false')
  }
}

/**
 * 检查是否为管理员
 * @returns {boolean}
 */
export function isAdmin() {
  return localStorage.getItem('is_admin') === 'true'
}

/**
 * 检查是否已登录
 * @returns {boolean}
 */
export function isLoggedIn() {
  return !!localStorage.getItem('access_token')
}

/**
 * 获取 token
 * @returns {string|null}
 */
export function getToken() {
  return localStorage.getItem('access_token')
}

/**
 * 获取用户名
 * @returns {string|null}
 */
export function getUsername() {
  return localStorage.getItem('username')
}
