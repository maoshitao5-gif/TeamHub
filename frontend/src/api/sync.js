/**
 * 本地后端 — 同步 API 封装
 * 层：前端（调用本地后端 :8001 的 /api/sync 接口）
 */
import request from './request'

/** 获取当前设备 ID 和设备名称 */
export function getDeviceInfo() {
  return request.get('/api/sync/device', { _silent: true })
}

/** 修改设备显示名称 */
export function updateDeviceName(name) {
  return request.put('/api/sync/device/name', { name })
}

/** 将前端 JWT 同步写入本地后端 config.json（登录/注册成功后调用，静默） */
export function syncTokenToBackend(accessToken, refreshToken) {
  return request.post('/api/sync/sync-token', {
    access_token: accessToken,
    refresh_token: typeof refreshToken === 'string' ? refreshToken : null,
  }, { _silent: true })
}

/** 登录云端账号，返回 { user, teams, workspaces } */
export function cloudLogin(email, password) {
  return request.post('/api/sync/login', { email, password })
}

/** 获取已登录账号下的所有可用工作空间列表 */
export function listCloudWorkspaces() {
  return request.get('/api/sync/workspaces', { _silent: true })
}

/** 退出云端登录 */
export function cloudLogout() {
  return request.post('/api/sync/logout')
}

/** 绑定工作空间 */
export function bindWorkspace(id, name) {
  return request.post('/api/sync/workspace', { workspace_id: id, workspace_name: name })
}

/** 获取同步状态概览（不调用云端，静默请求） */
export function getSyncStatus() {
  return request.get('/api/sync/status', { _silent: true })
}

/**
 * 推送单个文档到云端
 * @param {string} docId 本地文档 ID
 * @param {string} [versionNote] 本次推送的版本备注（可选）
 */
export function pushDoc(docId, versionNote = '') {
  return request.post('/api/sync/push-doc', { doc_id: docId, version_note: versionNote }, { _silent: true })
}

/**
 * 从云端拉取单个文档到本地
 * @param {string} workspaceId 工作空间 ID
 * @param {string} cloudDocId 云端文档 ID
 * @param {string} [targetDir] 拉取目标子目录（相对文件库根，空=根目录）
 */
export function pullDoc(workspaceId, cloudDocId, targetDir = '') {
  return request.post('/api/sync/pull-doc', {
    workspace_id: workspaceId,
    cloud_doc_id: cloudDocId,
    target_dir: targetDir,
  })
}

/**
 * 检查单个文档本地与云端哈希差异
 * @param {string} docId 本地文档 ID
 * @returns {{ status: 'not_pushed'|'synced'|'modified'|'unknown', local_hash, cloud_hash, ... }}
 */
export function checkDoc(docId) {
  return request.post('/api/sync/check-doc', { doc_id: docId })
}

/**
 * 列出云端工作空间的所有文档（供云仓库页展示）
 * @param {string} [workspaceId] 缺省时使用 config.json 中绑定的工作空间
 */
export function listCloudDocs(workspaceId) {
  const params = workspaceId ? { workspace_id: workspaceId } : {}
  return request.get('/api/sync/cloud-docs', { params })
}

/**
 * 列出云端文档的版本历史
 * @param {string} docId 云端文档 ID
 * @param {string} [workspaceId]
 */
export function listCloudDocVersions(docId, workspaceId) {
  const params = workspaceId ? { workspace_id: workspaceId } : {}
  return request.get(`/api/sync/cloud-docs/${docId}/versions`, { params, _silent: true })
}

/**
 * 获取云端某版本的预签名下载 URL
 * @param {string} docId 云端文档 ID
 * @param {string} versionId 版本 ID
 * @param {string} [workspaceId]
 * @returns {{ download_url, original_filename, version_number, file_size }}
 */
export function getCloudVersionDownloadUrl(docId, versionId, workspaceId) {
  const params = workspaceId ? { workspace_id: workspaceId } : {}
  return request.get(`/api/sync/cloud-docs/${docId}/versions/${versionId}/download-url`, { params, _silent: true })
}


// ========== 团队发现与加入申请 ==========

/** 搜索可加入的团队 */
export function discoverTeams(q = '') {
  return request.get('/api/sync/teams/discover', { params: { q } })
}

/** 提交加入团队申请 */
export function createJoinRequest(teamId, message = '') {
  return request.post('/api/sync/join-requests', { team_id: teamId, message })
}

/** 查看自己的所有申请 */
export function getMyJoinRequests() {
  return request.get('/api/sync/join-requests/my')
}

/** 管理员待审申请总数 */
export function getPendingJoinRequestCount() {
  return request.get('/api/sync/join-requests/pending-count', { _silent: true })
}

/** 管理员查看某团队的申请列表 */
export function listTeamJoinRequests(teamId, status = 'pending') {
  return request.get(`/api/sync/teams/${teamId}/join-requests`, { params: { status } })
}

/** 审批加入申请 */
export function reviewJoinRequest(teamId, requestId, action) {
  return request.post(`/api/sync/teams/${teamId}/join-requests/${requestId}/review`, { action })
}


// ========== 服务端搜索 + 活动流 ==========

/** 服务端文档搜索 */
export function searchCloudDocs(params = {}) {
  return request.get('/api/sync/docs/search', { params })
}

/** 工作空间活动流 */
export function getWorkspaceActivity(workspaceId, limit = 30) {
  const params = { limit }
  if (workspaceId) params.workspace_id = workspaceId
  return request.get('/api/sync/workspace/activity', { params })
}
