// Publishing has a local destination; changing it never changes the browsing library.
export function choosePublicationLibrary(libraries, preferredId) {
  if (libraries.some(library => library.id === preferredId)) return preferredId
  return libraries.length === 1 ? libraries[0].id : ''
}

export function restorePublicationIntent(encoded, libraries, original) {
  try {
    const saved = JSON.parse(encoded || 'null')
    if (!saved || typeof saved.id !== 'string' || !saved.id ||
        !['new', 'update'].includes(saved.action) || typeof saved.workspaceId !== 'string' ||
        !['workspace', 'restricted'].includes(saved.visibility) ||
        !Array.isArray(saved.recipients) || saved.recipients.some(id => typeof id !== 'string')) return null
    if (saved.action === 'update') {
      if (!original?.can_contribute || original.workspace_id !== saved.workspaceId) return null
      if (saved.originalDocumentId && saved.originalDocumentId !== original.shared_document_id) return null
    } else if (!libraries.some(library => library.id === saved.workspaceId)) return null
    return { ...saved, recipients: [...saved.recipients] }
  } catch { return null }
}
