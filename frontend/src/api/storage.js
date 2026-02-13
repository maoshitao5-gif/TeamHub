/**
 * 存储位置管理 API
 */
import request from './request'

/**
 * 获取所有存储位置
 * @returns {Promise}
 */
export function getStorageLocations() {
  return request.get('/storage-locations')
}

/**
 * 创建存储位置
 * @param {Object} data - 存储位置数据
 * @param {string} data.name - 存储位置名称
 * @param {string} data.path - 存储路径（绝对路径）
 * @param {boolean} data.enabled - 是否启用
 * @returns {Promise}
 */
export function createStorageLocation(data) {
  return request.post('/storage-locations', data)
}

/**
 * 更新存储位置
 * @param {number} locationId - 存储位置ID
 * @param {Object} data - 更新数据
 * @returns {Promise}
 */
export function updateStorageLocation(locationId, data) {
  return request.put(`/storage-locations/${locationId}`, data)
}

/**
 * 删除存储位置
 * @param {number} locationId - 存储位置ID
 * @returns {Promise}
 */
export function deleteStorageLocation(locationId) {
  return request.delete(`/storage-locations/${locationId}`)
}

/**
 * 设置默认存储位置
 * @param {number} locationId - 存储位置ID
 * @returns {Promise}
 */
export function setDefaultStorageLocation(locationId) {
  return request.post(`/storage-locations/${locationId}/set-default`)
}
