/** Only accessible library IDs are restored; an organization is not a navigation step. */
export function chooseInitialLibrary(libraries, preferredId) {
  return libraries.find(library => library.id === preferredId) || (libraries.length === 1 ? libraries[0] : null)
}

export function libraryLabel(library) {
  return library?.team_name ? `${library.team_name} / ${library.name}` : (library?.name || '共享资料库')
}

export function libraryPreferenceKey(serverId, userId) {
  return serverId && userId ? `teamhub:shared-library:${encodeURIComponent(serverId)}:${encodeURIComponent(userId)}` : null
}

export function readLibraryPreference(storage, serverId, userId) {
  const key = libraryPreferenceKey(serverId, userId)
  if (!key) return null
  try { return storage?.getItem(key) || null } catch { return null }
}

export function rememberLibraryPreference(storage, serverId, userId, workspaceId) {
  const key = libraryPreferenceKey(serverId, userId)
  if (!key || !workspaceId) return
  try { storage?.setItem(key, workspaceId) } catch { /* Navigation still works if local storage is unavailable. */ }
}
