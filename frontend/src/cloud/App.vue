<template>
  <main v-if="!webSession.user" class="login-screen">
    <section class="login-intro"><div class="wordmark">TEAM<span>HUB</span></div><p class="eyebrow">每一份资料，都有来处。</p><h1>让团队的成果<br>在需要时找到。</h1><p>组织共享与资料投递，随时从浏览器访问。</p><div class="intro-line"></div><span class="small">独立云端 · 明确权限 · 保留版本</span></section>
    <section class="login-panel"><h2>登录云服务</h2><p class="muted">使用组织分配的账号继续。</p>
      <el-form label-position="top" @submit.prevent="signIn">
        <el-form-item label="邮箱"><el-input v-model="email" type="email" autocomplete="username" aria-label="邮箱" size="large" required /></el-form-item>
        <el-form-item label="密码"><el-input v-model="password" type="password" autocomplete="current-password" aria-label="密码" size="large" show-password required /></el-form-item>
        <p v-if="error" class="error-text" role="alert">{{ error }}</p>
        <el-button type="primary" size="large" native-type="submit" :loading="busy" class="full-width">登录</el-button>
      </el-form>
      <p class="small muted">文件在云端管理，下载后由你选择保存位置。</p>
    </section>
  </main>
  <div v-else class="cloud-shell">
    <aside class="cloud-sidebar">
      <a class="wordmark" href="/cloud.html" aria-label="TeamHub 首页">TEAM<span>HUB</span></a>
      <nav aria-label="云端导航"><button :class="{ active: tab === 'shared' }" @click="tab = 'shared'"><span>▤</span>云仓库</button><button :class="{ active: tab === 'delivery' }" @click="tab = 'delivery'"><span>↗</span>资料投递</button></nav>
      <div class="sidebar-bottom"><p>{{ webSession.user.display_name }}</p><span class="small muted">{{ webSession.user.email }}</span><a v-if="webSession.user.is_superuser" href="/admin.html">管理组织</a><button class="logout-button" @click="signOut">退出登录</button></div>
    </aside>
    <div class="cloud-content"><header class="cloud-topbar"><CloudLibraryPicker :libraries="spaces" :model-value="workspaceId" :loading="librariesLoading || selecting" @change="selectLibrary" /><span class="small muted">云端工作台</span></header>
      <div class="cloud-workspace" v-loading="librariesLoading || selecting">
        <el-alert v-if="error && workspaceId" :title="error" type="error" :closable="false" show-icon />
        <SharedLibraryEmpty v-if="!workspaceId && !librariesLoading && !selecting" :libraries="spaces" :error="error" @select="selectLibrary" @retry="loadSpaces" />
        <ShareBrowser v-if="workspaceId && !librariesLoading && !selecting && tab === 'shared'" :key="`${webSession.user.id}:${workspaceId}`" :workspace-id="workspaceId" />
        <DeliveryPanel v-if="workspaceId && !librariesLoading && !selecting && tab === 'delivery'" :key="`${webSession.user.id}:delivery:${workspaceId}`" :workspace-id="workspaceId" :transport="transport" :desktop="false" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ensureSession, login, logout, transport, webSession } from './api'
import ShareBrowser from './ShareBrowser.vue'
import DeliveryPanel from '../components/delivery/DeliveryPanel.vue'
import CloudLibraryPicker from '../components/CloudLibraryPicker.vue'
import SharedLibraryEmpty from '../components/SharedLibraryEmpty.vue'
import { chooseInitialLibrary, readLibraryPreference, rememberLibraryPreference } from '../utils/sharedLibraries'

const email = ref(''), password = ref(''), busy = ref(false), error = ref('')
const spaces = ref([]), workspaceId = ref(''), tab = ref('shared')
const librariesLoading = ref(false), selecting = ref(false)
let generation = 0, selectionGeneration = 0, disposed = false
const captureScope = () => ({ generation, actor: webSession.user?.id, server: webSession.serverId })
const isCurrent = scope => !disposed && scope.generation === generation && scope.actor === webSession.user?.id && scope.server === webSession.serverId
function preferenceStorage() { try { return window.localStorage } catch { return null } }

async function loadSpaces() {
  const previousId = workspaceId.value
  generation++; selectionGeneration++
  const scope = captureScope()
  spaces.value = []; workspaceId.value = ''; selecting.value = false; error.value = ''
  if (!scope.actor) { librariesLoading.value = false; return }
  librariesLoading.value = true
  try {
    const teams = await transport('GET', '/api/teams')
    if (!isCurrent(scope)) return
    const groups = await Promise.all(teams.map(async team => {
      const items = await transport('GET', `/api/teams/${encodeURIComponent(team.id)}/workspaces`)
      return items.map(space => ({ ...space, team_id: team.id, team_name: team.name }))
    }))
    if (!isCurrent(scope)) return
    spaces.value = groups.flat()
    const preferred = previousId || readLibraryPreference(preferenceStorage(), scope.server, scope.actor)
    const initial = chooseInitialLibrary(spaces.value, preferred)
    if (initial) await selectLibrary(initial.id)
  } catch (err) { if (isCurrent(scope)) error.value = err.message || '资料库暂时无法加载，请重试' }
  finally { if (isCurrent(scope)) librariesLoading.value = false }
}

async function selectLibrary(id) {
  const selected = spaces.value.find(space => space.id === id)
  if (!selected || selecting.value) return false
  const scope = captureScope(), sequence = ++selectionGeneration
  selecting.value = true; workspaceId.value = ''; error.value = ''
  try {
    const context = await transport('GET', `/api/workspaces/${encodeURIComponent(id)}/collaboration-context`)
    if (!isCurrent(scope) || sequence !== selectionGeneration) return false
    if (context.workspace_id !== id) throw new Error('资料库信息不一致，请重新加载')
    workspaceId.value = id
    rememberLibraryPreference(preferenceStorage(), scope.server, scope.actor, id)
    return true
  } catch (err) {
    if (isCurrent(scope) && sequence === selectionGeneration) {
      error.value = err.message || '无法进入资料库，请重试'
      if ([401, 403, 404, 410].includes(err.status)) spaces.value = spaces.value.filter(space => space.id !== id)
    }
    return false
  } finally { if (isCurrent(scope) && sequence === selectionGeneration) selecting.value = false }
}

async function signIn() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await login(email.value, password.value); password.value = '' }
  catch (err) { error.value = err.message }
  finally { busy.value = false }
}

async function signOut() {
  try { await logout(); spaces.value = []; workspaceId.value = ''; error.value = '' }
  catch (err) { error.value = `退出未完成：${err.message}` }
}

onMounted(async () => {
  busy.value = true
  try { await ensureSession() }
  catch (err) { error.value = err.message }
  finally { busy.value = false }
})

watch(() => [webSession.user?.id, webSession.serverId], async ([id]) => {
  generation++; selectionGeneration++
  spaces.value = []; workspaceId.value = ''; tab.value = 'shared'; error.value = ''; selecting.value = false; librariesLoading.value = false
  if (!id) return
  await loadSpaces()
})
onBeforeUnmount(() => { disposed = true; generation++; selectionGeneration++ })
</script>

