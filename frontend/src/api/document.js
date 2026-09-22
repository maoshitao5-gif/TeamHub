/**
 * 文档 API（对应后端 /api/documents 路由）
 *
 * 覆盖功能：
 *   收纳 / 快速放入待整理 / 搜索 / 自动补全 / 详情
 *   更新信息 / 更新标签 / 整理 / 移回待整理 / 删除 / 恢复
 *   批量整理 / 批量更新标签 / 批量删除 / 批量恢复
 *   版本合并 / 分析待整理 / 重定位 / 撤销放入 / 重复检测
 *   文档目录列表（用于左侧目录树）/ 获取文档下的目录
 */
import request from './request'

// 收纳文档
export function createDocument(data) {
  return request.post('/api/documents', data)
}

// 快速放入待整理
export function quickAddToPending(data) {
  return request.post('/api/documents/quick-add', data)
}

// 搜索文档
export function searchDocuments(params) {
  return request.post('/api/documents/search', params)
}

// 文档名称自动补全（搜索框实时联想）
export function autocompleteDocuments(q, limit = 10) {
  return request.get('/api/documents/autocomplete', { params: { q, limit } })
}

// 获取文档详情
export function getDocument(id) {
  return request.get(`/api/documents/${id}`)
}

// 更新文档
export function updateDocument(id, data) {
  return request.put(`/api/documents/${id}`, data)
}

// 设置文档标签
export function updateDocumentTags(id, tagNames) {
  return request.put(`/api/documents/${id}/tags`, tagNames)
}

// 整理文档（从待整理移入文件库）
export function organizeDocument(id, data) {
  return request.post(`/api/documents/${id}/organize`, data)
}

// 移回待整理（从文档库移回待整理区）
export function moveToPending(id) {
  return request.post(`/api/documents/${id}/move-to-pending`)
}

// 删除文档
export function deleteDocument(id, permanent = false) {
  return request.delete(`/api/documents/${id}`, { params: { permanent } })
}

// 恢复文档
export function restoreDocument(id) {
  return request.post(`/api/documents/${id}/restore`)
}

// 批量更新标签
export function batchUpdateTags(data) {
  return request.post('/api/documents/batch/tags', data)
}

// 批量永久删除
export function batchDeleteDocuments(documentIds) {
  return request.post('/api/documents/batch/delete', { document_ids: documentIds })
}

// 批量恢复
export function batchRestoreDocuments(documentIds) {
  return request.post('/api/documents/batch/restore', { document_ids: documentIds })
}

// 批量整理
export function batchOrganizeDocuments(data) {
  return request.post('/api/documents/batch/organize', data)
}

// 获取待整理数量
export function getPendingCount() {
  return request.get('/api/documents/pending/count')
}

// 获取版本列表
export function getVersions(documentId) {
  return request.get(`/api/documents/${documentId}/versions`)
}

// 添加新版本
export function addVersion(documentId, data) {
  return request.post(`/api/documents/${documentId}/versions`, data)
}

// 恢复版本
export function restoreVersion(documentId, versionId) {
  return request.post(`/api/documents/${documentId}/versions/${versionId}/restore`)
}

// 删除版本
export function deleteVersion(documentId, versionId) {
  return request.delete(`/api/documents/${documentId}/versions/${versionId}`)
}

// 合并多个文档为版本
export function mergeDocumentsAsVersions(data) {
  return request.post('/api/documents/merge-as-versions', data)
}

// 智能分析待整理文档
export function analyzePending() {
  return request.post('/api/documents/pending/analyze')
}

// 获取文件夹内容树
export function getDocumentFiles(documentId) {
  return request.get(`/api/documents/${documentId}/files`)
}

// 重新定位缺失文档
export function relocateDocument(id, newPath) {
  return request.post(`/api/documents/${id}/relocate`, { new_path: newPath })
}

// 撤销放入待整理（移回原始路径）
export function undoPending(id) {
  return request.post(`/api/documents/${id}/undo-pending`)
}

// 单文件查重（file_path 或 sha256_hash 二选一，可附带 exclude_doc_id）
export function checkFileDuplicate(filePath) {
  return request.post('/api/documents/check-duplicate', { file_path: filePath })
}

// 按 sha256 直接查重（云端导入时用，无需本地文件路径）
export function checkHashDuplicate(sha256Hash) {
  return request.post('/api/documents/check-duplicate', { sha256_hash: sha256Hash })
}

// 按文档 ID 查重：取该文档当前版本的 sha256，检查是否有其他文档内容相同
export function checkDocLibraryDuplicate(docId) {
  return request.get(`/api/documents/${docId}/check-library-duplicate`)
}

// 文件夹（不打散模式）查重：后端批量扫描文件夹内所有文件，返回重复数量
export function checkFolderDuplicates(folderPath) {
  return request.post('/api/documents/check-folder-duplicates', { folder_path: folderPath })
}

// 获取数据库中有文档的目录列表（用于 LibraryTreePanel 导航）
export const getDocumentDirs = (parent = '') =>
  request.get('/api/documents/dirs', { params: { parent } })
