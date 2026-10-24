import request from './request'

export const getLocalFavorites = () => request.get('/api/favorites', { params: { target_type: 'local' } })
export const addLocalFavorite = documentId => request.post('/api/favorites', {
  target_type: 'local', local_document_id: documentId,
})
export const removeFavorite = favoriteId => request.delete(`/api/favorites/${favoriteId}`)
export const getSharedFavorites = () => request.get('/api/favorites', { params: { target_type: 'shared' } })
export const addSharedFavorite = (server, workspace, document) => request.post('/api/favorites', {
  target_type: 'shared', server_url: server, workspace_id: workspace, shared_document_id: document,
})
export const resolveSharedFavorite = id => request.get(`/api/favorites/${id}/resolve`, { _silent: true })
