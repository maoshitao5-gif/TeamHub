import request from './request'
export const getCollaborationContext = workspace => request.get('/api/sharing/context', { params: { workspace_id: workspace } })
export const getSharedTagLinks = (id, workspace) => request.get(`/api/sharing/tags/${id}`, { params: { workspace_id: workspace } })
export const changeSharedTags = (id, workspace, changes) => request.post(`/api/sharing/tags/${id}`, { workspace_id: workspace, ...changes })
export const getSharedSuggestions = (workspace, type, q = '', selected = []) => request.get('/api/sharing/suggestions', { params: { workspace_id: workspace, type, q, selected_tag_ids: selected.join(',') } })
export const getSharedTrash = (workspace, page) => request.get('/api/sharing/trash', { params: { workspace_id: workspace, page } })
export const manageSharedDocument = (id, workspace, action) => request.post(`/api/sharing/documents/${id}/${action}`, null, { params: { workspace_id: workspace } })
export const getSharedMembers = workspace => request.get('/api/sharing/members', { params: { workspace_id: workspace } })
export const deactivateSharedMember = (id, workspace) => request.post(`/api/sharing/members/${id}/deactivate`, null, { params: { workspace_id: workspace } })
