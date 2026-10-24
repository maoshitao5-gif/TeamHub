/** Cloud credentials remain in the personal backend; navigation is scoped to server + account. */
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useAppStore } from './app'
import { cloudLogin, cloudLogout, getSyncStatus, listTeams, listCloudWorkspaces, selectSharedLibrary } from '@/api/sync'
import { chooseInitialLibrary, readLibraryPreference, rememberLibraryPreference } from '@/utils/sharedLibraries'
import { advanceCloudSessionScope, isCurrentCloudSessionScope } from '@/utils/cloudSession'

export const useAuthStore = defineStore('auth', () => {
  const appStore = useAppStore()
  const user = ref(null), loggedIn = ref(false), serverId = ref(null)
  const teams = ref([]), allWorkspaces = ref([]), currentWorkspace = ref(null)
  const loading = ref(false), workspaceLoading = ref(false), workspaceError = ref(''), onboardingChecked = ref(false)
  const sessionError = ref('')
  const isLoggedIn = computed(() => loggedIn.value)
  const needsOnboarding = computed(() => isLoggedIn.value && onboardingChecked.value && teams.value.length === 0)
  const currentTeam = computed(() => teams.value.find(team => team.id === currentWorkspace.value?.team_id) || null)
  const workspaces = computed(() => allWorkspaces.value)
  let sessionEpoch = 0, selectionEpoch = 0, restoring = null, selectionTail = Promise.resolve(), persistedWorkspace = null, logoutEntry = null
  const currentSession = epoch => epoch === sessionEpoch
  const currentSelection = (epoch, selection) => currentSession(epoch) && selection === selectionEpoch
  const message = error => error.response?.data?.error?.message || error.response?.data?.detail?.error?.message || error.response?.data?.detail || error.message || '无法切换资料库，请重试'
  const storage = () => { try { return typeof localStorage === 'undefined' ? null : localStorage } catch { return null } }

  function invalidateSession(reason = '') {
    advanceCloudSessionScope()
    sessionEpoch++; selectionEpoch++; restoring = null; persistedWorkspace = null; logoutEntry = null
    user.value = null; loggedIn.value = false; serverId.value = null
    teams.value = []; allWorkspaces.value = []; currentWorkspace.value = null
    loading.value = false; workspaceLoading.value = false; workspaceError.value = ''; onboardingChecked.value = false
    sessionError.value = reason
    appStore.syncStatus = null
    return sessionEpoch
  }

  function updateStatus(workspace) {
    appStore.syncStatus = { ...(appStore.syncStatus || {}), logged_in: loggedIn.value, user: user.value,
      server_id: serverId.value, workspace_id: workspace?.id || null, workspace_name: workspace?.name || null }
  }

  function applyWorkspace(workspace) {
    currentWorkspace.value = workspace
    updateStatus(workspace)
    if (workspace) rememberLibraryPreference(storage(), serverId.value, user.value?.id, workspace.id)
  }

  // Keep acknowledged backend state even when a newer selection supersedes the visible update.
  function persistWorkspace(workspace, epoch, selection) {
    const result = selectionTail.then(async () => {
      if (!currentSelection(epoch, selection)) return false
      try {
        await selectSharedLibrary(workspace?.id || '', workspace?.name || '')
        if (currentSession(epoch)) persistedWorkspace = { epoch, workspace }
        else if (logoutEntry?.previousEpoch === epoch && currentSession(logoutEntry.epoch)) {
          // Logout waits for this request; retain its acknowledgement without reopening the old page.
          persistedWorkspace = { epoch: logoutEntry.epoch, workspace }
        }
        if (!currentSelection(epoch, selection)) return false
        applyWorkspace(workspace)
        return true
      } catch (error) {
        // B may have succeeded while C was queued. If C fails, A would misrepresent the actual saved B.
        if (currentSelection(epoch, selection) && persistedWorkspace?.epoch === epoch) applyWorkspace(persistedWorkspace.workspace)
        throw error
      }
    })
    selectionTail = result.catch(() => {})
    return result
  }

  async function chooseFromAvailable(epoch, selection, preferredId, autoSelect = true) {
    if (!currentSelection(epoch, selection)) return false
    const selected = autoSelect ? chooseInitialLibrary(allWorkspaces.value, preferredId)
      : allWorkspaces.value.find(space => space.id === currentWorkspace.value?.id) || null
    if (currentWorkspace.value && !selected) { currentWorkspace.value = null; updateStatus(null) }
    return persistWorkspace(selected, epoch, selection)
  }

  async function login(email, password) {
    const epoch = invalidateSession(sessionError.value)
    loading.value = true; workspaceLoading.value = true
    try {
      const result = await cloudLogin(email, password)
      if (!currentSession(epoch)) return false
      advanceCloudSessionScope()
      sessionError.value = ''
      user.value = result.user || null; serverId.value = result.server_id || null; loggedIn.value = !!user.value?.id
      teams.value = result.teams || []; allWorkspaces.value = result.workspaces || []; onboardingChecked.value = true
      updateStatus(null)
      persistedWorkspace = { epoch, workspace: null }
      const selection = ++selectionEpoch
      try {
        await chooseFromAvailable(epoch, selection, readLibraryPreference(storage(), serverId.value, user.value?.id))
      } catch (error) { if (currentSelection(epoch, selection)) workspaceError.value = message(error) }
      return currentSession(epoch) && loggedIn.value
    } finally { if (currentSession(epoch)) { loading.value = false; workspaceLoading.value = false } }
  }

  function restoreSession() {
    if (sessionError.value) return Promise.resolve(false)
    if (logoutEntry?.epoch === sessionEpoch) return Promise.resolve(false)
    const epoch = sessionEpoch
    if (restoring?.epoch === epoch) return restoring.promise
    const entry = { epoch, promise: null }
    entry.promise = restoreSessionOnce(epoch).finally(() => { if (restoring === entry) restoring = null })
    restoring = entry
    return entry.promise
  }

  async function restoreSessionOnce(epoch) {
    const selection = ++selectionEpoch
    workspaceLoading.value = true; workspaceError.value = ''
    try {
      const status = await getSyncStatus()
      if (!currentSelection(epoch, selection)) return false
      if (!status.logged_in || !status.user?.id) { invalidateSession(); return false }
      if (!loggedIn.value || user.value?.id !== status.user.id || serverId.value !== status.server_id) advanceCloudSessionScope()
      user.value = status.user; serverId.value = status.server_id || null; loggedIn.value = true
      appStore.syncStatus = { ...status, workspace_id: null, workspace_name: null }
      const response = await listCloudWorkspaces()
      if (!currentSelection(epoch, selection)) return false
      const availableTeams = response.teams || await listTeams()
      if (!currentSelection(epoch, selection)) return false
      teams.value = availableTeams; allWorkspaces.value = response.workspaces || []; onboardingChecked.value = true
      persistedWorkspace = { epoch, workspace: allWorkspaces.value.find(space => space.id === status.workspace_id) || null }
      const preferred = readLibraryPreference(storage(), serverId.value, user.value.id) || (serverId.value ? status.workspace_id : null)
      await chooseFromAvailable(epoch, selection, preferred)
      return true
    } catch (error) {
      if (currentSelection(epoch, selection)) workspaceError.value = message(error)
      return false
    } finally { if (currentSelection(epoch, selection)) workspaceLoading.value = false }
  }

  async function refreshWorkspaces({ autoSelect = true } = {}) {
    if (logoutEntry?.epoch === sessionEpoch) return false
    if (!isLoggedIn.value) return restoreSession()
    const epoch = sessionEpoch, selection = ++selectionEpoch
    workspaceLoading.value = true; workspaceError.value = ''
    try {
      const response = await listCloudWorkspaces()
      if (!currentSelection(epoch, selection)) return false
      const availableTeams = response.teams || await listTeams()
      if (!currentSelection(epoch, selection)) return false
      teams.value = availableTeams; allWorkspaces.value = response.workspaces || []; onboardingChecked.value = true
      const preferred = currentWorkspace.value?.id || readLibraryPreference(storage(), serverId.value, user.value?.id)
      return await chooseFromAvailable(epoch, selection, preferred, autoSelect)
    } catch (error) {
      if (currentSelection(epoch, selection)) workspaceError.value = message(error)
      return false
    } finally { if (currentSelection(epoch, selection)) workspaceLoading.value = false }
  }

  async function switchWorkspace(workspace) {
    if (logoutEntry?.epoch === sessionEpoch) return false
    const target = allWorkspaces.value.find(space => space.id === workspace?.id)
    if (!isLoggedIn.value || !target) { workspaceError.value = '资料库不存在或当前账号无权访问，请刷新后重新选择'; return false }
    const epoch = sessionEpoch, selection = ++selectionEpoch
    workspaceLoading.value = true; workspaceError.value = ''
    try { return await persistWorkspace(target, epoch, selection) }
    catch (error) { if (currentSelection(epoch, selection)) workspaceError.value = message(error); return false }
    finally { if (currentSelection(epoch, selection)) workspaceLoading.value = false }
  }

  function logout() {
    if (logoutEntry?.epoch === sessionEpoch) return logoutEntry.promise
    // Cancel pending login/selection callbacks, but show the account until credential removal is confirmed.
    const previousEpoch = sessionEpoch, inFlightTail = selectionTail
    advanceCloudSessionScope()
    const epoch = ++sessionEpoch
    selectionEpoch++; restoring = null; loading.value = false
    workspaceLoading.value = true; workspaceError.value = ''
    if (persistedWorkspace) persistedWorkspace = { epoch, workspace: persistedWorkspace.workspace }
    const entry = { epoch, previousEpoch, promise: null }
    entry.promise = Promise.resolve().then(async () => {
      try {
        await inFlightTail
        if (!currentSession(epoch)) return false
        await cloudLogout()
        if (!currentSession(epoch)) return false
        invalidateSession()
        return true
      } catch (error) {
        if (currentSession(epoch)) {
          if (persistedWorkspace?.epoch === epoch) applyWorkspace(persistedWorkspace.workspace)
          workspaceError.value = `未退出云服务：${message(error)}`
        }
        return false
      } finally {
        if (currentSession(epoch)) workspaceLoading.value = false
        if (logoutEntry === entry) logoutEntry = null
      }
    })
    logoutEntry = entry
    return entry.promise
  }

  // Compatibility for management views: changing an organization never selects its first library.
  const switchTeam = () => refreshWorkspaces({ autoSelect: false })
  watch(() => appStore.settings.cloud_api_url, (_url, previous) => { if (previous !== undefined) invalidateSession() }, { flush: 'sync' })
  watch(() => appStore.libraryPath, (_path, previous) => { if (previous) invalidateSession() }, { flush: 'sync' })
  if (typeof window !== 'undefined') window.addEventListener('cloud:session-expired', event => {
    if (isCurrentCloudSessionScope(event.detail?.scope)) invalidateSession('云服务登录已失效，请重新登录')
  })
  return { user, serverId, teams, currentTeam, allWorkspaces, workspaces, currentWorkspace,
    loading, workspaceLoading, workspaceError, sessionError, isLoggedIn, needsOnboarding, onboardingChecked,
    login, logout, restoreSession, refreshWorkspaces, switchWorkspace, switchTeam }
})
