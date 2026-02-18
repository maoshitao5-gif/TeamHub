/**
 * 标签 API
 */
import request from './request'

export function getTags() {
  return request.get('/api/tags')
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
