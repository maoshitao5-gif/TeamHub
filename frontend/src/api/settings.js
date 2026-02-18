/**
 * 设置 API
 */
import request from './request'

export function getLibraryInfo() {
  return request.get('/api/settings/library')
}

export function setupLibrary(path) {
  return request.post('/api/settings/library', { path })
}

export function getSettings() {
  return request.get('/api/settings/config')
}

export function updateSettings(data) {
  return request.put('/api/settings/config', data)
}

export function scanLibrary() {
  return request.post('/api/settings/scan')
}

export function getLibrarySubdirs(parent = '') {
  return request.get('/api/settings/library/subdirs', { params: { parent } })
}
