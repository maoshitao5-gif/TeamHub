/**
 * 管理后台 API
 */
import request from './request'

// ==================== 文件管理 ====================

/**
 * 获取所有文件列表（分页）
 * @param {number} page - 页码
 * @param {number} pageSize - 每页数量
 * @returns {Promise}
 */
export function getAdminFiles(page = 1, pageSize = 50) {
  return request.get('/admin/files', {
    params: { page, page_size: pageSize }
  })
}

/**
 * 批量删除文件
 * @param {number[]} fileIds - 文件ID列表
 * @returns {Promise}
 */
export function batchDeleteFiles(fileIds) {
  return request.post('/admin/files/batch-delete', {
    ids: fileIds
  })
}

// ==================== 标签管理 ====================

/**
 * 获取所有标签
 * @returns {Promise}
 */
export function getAdminTags() {
  return request.get('/admin/tags')
}

/**
 * 创建标签
 * @param {string} name - 标签名称
 * @returns {Promise}
 */
export function createTag(name) {
  return request.post('/admin/tags', {
    name
  })
}

/**
 * 更新标签
 * @param {number} tagId - 标签ID
 * @param {string} name - 新标签名称
 * @returns {Promise}
 */
export function updateTag(tagId, name) {
  return request.put(`/admin/tags/${tagId}`, {
    name
  })
}

/**
 * 删除标签
 * @param {number} tagId - 标签ID
 * @returns {Promise}
 */
export function deleteTag(tagId) {
  return request.delete(`/admin/tags/${tagId}`)
}

/**
 * 批量删除标签
 * @param {number[]} tagIds - 标签ID列表
 * @returns {Promise}
 */
export function batchDeleteTags(tagIds) {
  return request.post('/admin/tags/batch-delete', {
    ids: tagIds
  })
}

// ==================== 用户管理 ====================

/**
 * 获取所有用户
 * @returns {Promise}
 */
export function getAdminUsers() {
  return request.get('/admin/users')
}

/**
 * 创建用户
 * @param {string} username - 用户名
 * @param {string} password - 密码
 * @param {boolean} isAdmin - 是否为管理员
 * @returns {Promise}
 */
export function createUser(username, password, isAdmin = false) {
  return request.post('/admin/users', {
    username,
    password,
    is_admin: isAdmin
  })
}

/**
 * 更新用户
 * @param {number} userId - 用户ID
 * @param {object} data - 更新数据 {password?, is_admin?}
 * @returns {Promise}
 */
export function updateUser(userId, data) {
  return request.put(`/admin/users/${userId}`, data)
}

/**
 * 删除用户
 * @param {number} userId - 用户ID
 * @returns {Promise}
 */
export function deleteUser(userId) {
  return request.delete(`/admin/users/${userId}`)
}
