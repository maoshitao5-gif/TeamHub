/**
 * 认证 Pinia Store
 * 管理用户登录态、access token、团队和工作空间列表
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import cloudRequest, {
  authApi, userApi, teamApi, workspaceApi,
  saveAccessToken, loadAccessToken, clearAccessToken,
  saveRefreshToken, loadRefreshToken, clearRefreshToken,
} from '@/api/cloud'
import { syncTokenToBackend, cloudLogout as backendLogout } from '@/api/sync'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)
  const accessToken = ref(loadAccessToken())
  const teams = ref([])
  const currentTeam = ref(null)
  const workspaces = ref([])
  const currentWorkspace = ref(null)
  const loading = ref(false)
  const onboardingChecked = ref(false)

  // 有 accessToken 即视为已登录（避免 restoreSession 异步完成前路由守卫误判）
  // user.value 为 null 仅代表用户信息尚未加载，不代表未登录
  const isLoggedIn = computed(() => !!accessToken.value)

  // 已登录 + 未加入任何团队 + 已完成检查 → 需要入职引导
  const needsOnboarding = computed(() =>
    isLoggedIn.value && onboardingChecked.value && teams.value.length === 0
  )

  // ===== 登录 =====

  async function login(email, password) {
    loading.value = true
    try {
      const res = await authApi.login({ email, password })
      _applyTokens(res.access_token, res.refresh_token)
      await _loadUserAndTeams()
      syncTokenToBackend(res.access_token, res.refresh_token).catch(() => {})
      return true
    } finally {
      loading.value = false
    }
  }

  async function register(email, password, displayName) {
    loading.value = true
    try {
      const res = await authApi.register({ email, password, display_name: displayName })
      _applyTokens(res.access_token, res.refresh_token)
      await _loadUserAndTeams()
      syncTokenToBackend(res.access_token, res.refresh_token).catch(() => {})
      return true
    } finally {
      loading.value = false
    }
  }

  // ===== 静默恢复登录态（应用启动时调用）=====
  // 所有请求均使用 _silent: true，避免 cloud_backend 不可达时出现错误提示

  async function restoreSession() {
    const token = loadAccessToken()
    if (!token) return false

    accessToken.value = token
    try {
      const me = await cloudRequest.get('/api/users/me', { _silent: true })
      user.value = me
      await _loadTeams()
      syncTokenToBackend(accessToken.value, await loadRefreshToken()).catch(() => {})
      return true
    } catch {
      // access token 可能过期，尝试用 refresh token 续期
      const refreshToken = await loadRefreshToken()
      if (!refreshToken) {
        _clearState()
        return false
      }
      try {
        const res = await cloudRequest.post(
          '/api/auth/refresh',
          { refresh_token: refreshToken },
          { _silent: true }
        )
        accessToken.value = res.access_token
        saveAccessToken(res.access_token)
        const me = await cloudRequest.get('/api/users/me', { _silent: true })
        user.value = me
        await _loadTeams()
        syncTokenToBackend(res.access_token, res.refresh_token || refreshToken).catch(() => {})
        return true
      } catch {
        _clearState()
        return false
      }
    }
  }

  // ===== 登出 =====

  async function logout() {
    try {
      const refreshToken = await loadRefreshToken()
      if (refreshToken) {
        await authApi.logout(refreshToken)
      }
    } catch { /* 忽略登出请求失败 */ }
    backendLogout().catch(() => {})
    _clearState()
  }

  // ===== 工作空间切换 =====

  async function switchWorkspace(workspace) {
    currentWorkspace.value = workspace
    // 通知需要重新加载数据的组件
    window.dispatchEvent(new CustomEvent('cloud:workspace-changed', { detail: workspace }))
  }

  async function switchTeam(team) {
    currentTeam.value = team
    const wsList = await workspaceApi.list(team.id)
    workspaces.value = wsList
    if (wsList.length > 0) {
      await switchWorkspace(wsList[0])
    } else {
      currentWorkspace.value = null
    }
  }

  // ===== 内部工具函数 =====

  function _applyTokens(access, refresh) {
    accessToken.value = access
    saveAccessToken(access)
    saveRefreshToken(refresh)
  }

  async function _loadUserAndTeams() {
    user.value = await userApi.getMe()
    await _loadTeams()
  }

  async function _loadTeams() {
    teams.value = await teamApi.list()
    onboardingChecked.value = true
    if (teams.value.length > 0) {
      await switchTeam(teams.value[0])
    }
  }

  function _clearState() {
    user.value = null
    accessToken.value = null
    teams.value = []
    workspaces.value = []
    currentTeam.value = null
    currentWorkspace.value = null
    clearAccessToken()
    clearRefreshToken()
  }

  // 监听 session 过期事件（由 cloud.js 拦截器触发）
  if (typeof window !== 'undefined') {
    window.addEventListener('cloud:session-expired', () => {
      _clearState()
    })
  }

  return {
    user, accessToken, teams, currentTeam, workspaces, currentWorkspace,
    loading, isLoggedIn, needsOnboarding, onboardingChecked,
    login, register, logout, restoreSession, switchWorkspace, switchTeam,
  }
})
