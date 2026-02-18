/**
 * 文档 API
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
