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

// ===== 数据库云端备份 =====

export function createBackup() {
  return request.post('/api/settings/backup/create')
}

export function listBackups() {
  return request.get('/api/settings/backup/list')
}

export function restoreBackup(backupId, confirmed = false) {
  return request.post('/api/settings/backup/restore', { backup_id: backupId, confirmed })
}

export function deleteBackup(backupId) {
  return request.delete(`/api/settings/backup/${backupId}`)
}
