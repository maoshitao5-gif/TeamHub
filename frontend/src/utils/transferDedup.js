/** Normalize content-identity results without interpreting names or metadata as equality. */
export function contentDuplicate(value, target) {
  const payload = value?.response?.data || value
  const error = payload?.error || payload?.detail?.error || payload?.detail
  if (payload?.code !== 'DUPLICATE_CONTENT' && error?.code !== 'DUPLICATE_CONTENT') return null
  const details = error?.details || {}
  const doc = payload?.duplicate_document || details.duplicate_document || {}
  return {
    target,
    documentId: doc.document_id || doc.id || payload?.document_id || details.document_id,
    name: doc.name || payload?.name || '已有资料',
    status: doc.status || payload?.document_status,
    workspaceId: payload?.workspace_id || details.workspace_id,
  }
}

export function existingLocalLocation(duplicate) {
  if (!duplicate?.documentId || !['pending', 'organized'].includes(duplicate.status)) return null
  return { path: duplicate.status === 'pending' ? '/pending' : '/library', query: { highlight: duplicate.documentId } }
}
