/**
 * 标签 API
 */
import request from './request'

export function getTags(includeStats = false) {
  return request.get('/api/tags', {
    params: includeStats ? { include_stats: true } : {}
  })
}

export function createTag(data) {
  return request.post('/api/tags', data)
}

export function updateTag(id, data) {
  return request.put(`/api/tags/${id}`, data)
}

export function deleteTag(id) {
  return request.delete(`/api/tags/${id}`)
}

export function mergeTags(data) {
  return request.post('/api/tags/merge', data)
}

// 标签联想搜索（搜索框实时联想）
export function suggestTags(q = '', limit = 10) {
  return request.get('/api/tags/suggest', { params: { q, limit } })
}

// 获取与已选标签共同出现的标签（用于渐进式筛选）
export function getRelatedTags(selectedIds, limit = 8) {
  return request.get('/api/tags/related', {
    params: { selected: selectedIds.join(','), limit }
  })
}
