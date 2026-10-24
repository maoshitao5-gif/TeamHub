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

export function getLibraryDirTree(parent = '', excludePath = '') {
  const params = {}
  if (parent) params.parent = parent
  if (excludePath) params.exclude = excludePath
  return request.get('/api/settings/library/dirtree', { params })
}

export function createLibraryDir(path) {
  return request.post('/api/settings/library/mkdir', { path })
}

export function cleanTrash() {
  return request.post('/api/settings/trash/clean')
}

export function cleanVersions() {
  return request.post('/api/settings/versions/clean')
}

export function updatePendingPath(data) {
  return request.put('/api/settings/pending-path', data)
}

export const previewStorageMigration = data => request.post('/api/settings/storage/preview', data)
export const migrateStorage = data => request.post('/api/settings/storage/migrate', data, { timeout: 0 })
export const getStorageMigration = () => request.get('/api/settings/storage/migration', { _silent: true })
export const recoverStorageMigration = () => request.post('/api/settings/storage/recover', {}, { timeout: 0 })
