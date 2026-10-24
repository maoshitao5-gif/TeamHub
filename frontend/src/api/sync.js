/**
 * 本地后端 — 同步与共享 API 封装(M4 收敛版)
 * 层：前端（调用本地后端 :8001 的 /api/sync 与 /api/sharing 接口）
 *
 * M4 起前端不直连共享后端(契约 §7/V-13):
 * 登录态、令牌、续期全部由本地服务统一管理;共享写入走 /api/sharing 适配层。
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

/** 登录云端账号,返回 { user, teams, workspaces }(令牌由本地服务保管) */
export function cloudLogin(email, password) {
  return request.post('/api/sync/login', { email, password })
}

/** 退出云端登录 */
export function cloudLogout() {
  return request.post('/api/sync/logout')
}

/** 获取已登录账号下的所有可用工作空间列表 */
export function listCloudWorkspaces() {
  return request.get('/api/sync/workspaces', { _silent: true })
}

/** 进入共享资料库；选择成功后由认证状态统一更新当前入口。 */
export function selectSharedLibrary(id, name) {
  return request.post('/api/sync/workspace', { workspace_id: id, workspace_name: name })
}

/** 旧调用名仅保留兼容，界面无需绑定步骤。 */
export const bindWorkspace = selectSharedLibrary

/** 获取同步状态概览(含 logged_in / user,静默请求) */
export function getSyncStatus() {
  return request.get('/api/sync/status', { _silent: true })
}


// ========== 团队与工作空间(经本地代理) ==========

/** 我的团队列表 */
export function listTeams() {
  return request.get('/api/sync/teams', { _silent: true })
}

/** 创建团队 */
export function createTeam(data) {
  return request.post('/api/sync/teams', data)
}

/** 团队下工作空间列表 */
export function listTeamWorkspaces(teamId) {
  return request.get(`/api/sync/teams/${teamId}/workspaces`)
}

/** 创建工作空间 */
export function createWorkspace(teamId, data) {
  return request.post(`/api/sync/teams/${teamId}/workspaces`, data)
}

/** 团队成员列表 */
export function listTeamMembers(teamId) {
  return request.get(`/api/sync/teams/${teamId}/members`)
}

/** 邀请成员 */
export function inviteMember(teamId, data) {
  return request.post(`/api/sync/teams/${teamId}/members`, data)
}

/** 移除成员 */
export function removeMember(teamId, userId) {
  return request.delete(`/api/sync/teams/${teamId}/members/${userId}`)
}


// ========== 共享发布 / 取回(M4 唯一通路) ==========

/** 发布本地资料到共享空间(成功后才记录共享关联) */
export function publishDoc(docId, { workspaceId, note, forceNew, requestId, visibility = 'workspace', access = [], confirmed = false } = {}) {
  return request.post('/api/sharing/publish', {
    doc_id: docId,
    workspace_id: workspaceId || null,
    note: note || null,
    force_new: !!forceNew,
    visibility, access,
    request_id: requestId || crypto.randomUUID(),
    confirmed,
  }, { _silent: true })
}

/** 向已关联的共享资料提交新版本(更新原资料) */
export function submitDocVersion(docId, note, { requestId, confirmed = false, confirmOldContent = false, expectedWorkspaceId, expectedDocumentId } = {}) {
  return request.post('/api/sharing/submit-version', {
    confirm_old_content: confirmOldContent,
    expected_workspace_id: expectedWorkspaceId || null,
    expected_document_id: expectedDocumentId || null,
    doc_id: docId,
    request_id: requestId || crypto.randomUUID(),
    confirmed,
    note: note || null,
  }, { _silent: true })
}

/** 取回共享资料指定版本到本地库 */
export function pullSharedDoc(cloudDocId, { workspaceId, versionId, targetDir, name, tags } = {}) {
  return request.post('/api/sharing/pull', {
    cloud_doc_id: cloudDocId,
    workspace_id: workspaceId || null,
    version_id: versionId || null,
    target_dir: targetDir || null,
    name: name || null,
    tags: tags && tags.length ? tags : ['共享'],
  }, { _silent: true })
}

/** 查询本地资料的共享关联 */
export function getShareLink(docId) {
  return request.get(`/api/sharing/links/${docId}`, { _silent: true })
}

/** 已有关联的原资料位置，经当前账号和原服务验证。 */
export function getShareTarget(docId) {
  return request.get(`/api/sharing/links/${docId}/target`, { _silent: true })
}

/** 共享版本下载地址(经本地服务流式转发,可直接 window.open / <a href>) */
export function cloudVersionDownloadUrl(docId, versionId, workspaceId) {
  const base = request.defaults.baseURL
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''
  return `${base}/api/sharing/cloud-docs/${docId}/versions/${versionId}/download${qs}`
}


// ========== 云端资料只读(经本地代理) ==========

/** 列出云端工作空间的所有文档(供云仓库页展示) */
export function listCloudDocs(workspaceId) {
  const params = workspaceId ? { workspace_id: workspaceId } : {}
  return request.get('/api/sync/cloud-docs', { params, _silent: true })
}

/** 列出云端文档的版本历史 */
export function listCloudDocVersions(docId, workspaceId) {
  const params = workspaceId ? { workspace_id: workspaceId } : {}
  return request.get(`/api/sync/cloud-docs/${docId}/versions`, { params, _silent: true })
}

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

/** 预览只读请求，经本机服务带入云端登录态；统一网页与桌面的 Blob 返回值。 */
export async function cloudPreviewTransport(method, path, data, options = {}) {
  if (method !== 'GET' || !path.startsWith('/api/sharing/cloud-docs/')) throw new Error('预览仅允许读取云端资料')
  const response = await request.get(path, { ...options, _silent: true, timeout: 60000 })
  return options.responseType === 'blob' ? response.data : response
}

/** 获取当前版本时同样允许关闭预览或切换账号中止请求。 */
export function getCloudPreviewVersions(docId, workspaceId, options = {}) {
  return request.get(`/api/sync/cloud-docs/${encodeURIComponent(docId)}/versions`, {
    ...options, params: { workspace_id: workspaceId }, _silent: true,
  })
}
