/**
 * 文件管理 API（原管理后台功能，现已合并到普通 API）
 */
import request from './request'

/**
 * 批量删除文件
 * @param {number[]} fileIds - 文件ID列表
 * @returns {Promise}
 */
export function batchDeleteFiles(fileIds) {
  return request.post('/files/batch-delete', {
    ids: fileIds
  })
}

/**
 * 核对存储：扫描 storage 目录，同步数据库记录
 * @returns {Promise}
 */
export function syncStorage() {
  return request.post('/files/sync-storage')
}