<template>
  <div class="team-page">
    <div class="page-header">
      <div><h2>组织与资料库管理</h2><p class="management-help">管理组织成员与资料库。日常查看资料，请前往云仓库。</p></div>
      <el-button v-if="authStore.isLoggedIn" type="primary" :icon="Plus" @click="showCreateTeam = true">创建组织</el-button>
    </div>

    <!-- 未登录提示 -->
    <el-card v-if="!authStore.isLoggedIn" class="not-login-card">
      <el-empty description="请先登录云服务账号以使用组织管理">
        <el-button type="primary" @click="$router.push('/login')">前往登录</el-button>
      </el-empty>
    </el-card>

    <template v-else>
      <!-- 当前用户信息 -->
      <el-card class="user-info-card">
        <div class="user-row">
          <el-avatar :size="48" :style="{ background: '#4096ff' }">
            {{ authStore.user?.display_name?.[0]?.toUpperCase() }}
          </el-avatar>
          <div class="user-detail">
            <div class="user-name">{{ authStore.user?.display_name }}</div>
            <div class="user-email">{{ authStore.user?.email }}</div>
          </div>
          <el-button size="small" text type="danger" @click="handleLogout">退出登录</el-button>
        </div>
      </el-card>

      <!-- 团队列表 -->
      <div v-if="managedTeams.length === 0" class="empty-teams">
        <el-empty description="暂无可管理的组织，请联系管理员加入组织">
          <el-button type="primary" @click="showCreateTeam = true">创建组织</el-button>
        </el-empty>
      </div>

      <div v-else class="teams-grid">
        <el-card
          v-for="team in managedTeams"
          :key="team.id"
          class="team-card"
          :class="{ 'is-current': managedTeam?.id === team.id }"
          @click="selectTeam(team)"
        >
          <div class="team-card-header">
            <div class="team-name">{{ team.name }}</div>
            <el-tag size="small" type="info">#{{ team.slug }}</el-tag>
          </div>
          <div class="team-storage">
            <span>存储：{{ formatSize(team.storage_used) }} / {{ formatSize(team.storage_quota) }}</span>
            <el-progress
              :percentage="Math.min(100, Math.round(team.storage_used / team.storage_quota * 100))"
              :show-text="false"
              style="margin-top: 6px"
            />
          </div>
        </el-card>
      </div>

      <!-- 当前团队详情 -->
      <template v-if="managedTeam">
        <div class="section-title">
          <h3>{{ managedTeam.name }} — 成员</h3>
          <el-button v-if="canManageOrganization" size="small" :icon="Plus" @click="showInvite = true">邀请成员</el-button>
        </div>

        <el-table :data="members" v-loading="loadingMembers">
          <el-table-column label="昵称" prop="display_name" />
          <el-table-column label="邮箱" prop="email" />
          <el-table-column label="角色" width="120">
            <template #default="{ row }">
              <el-tag :type="roleTagType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="加入时间" width="160">
            <template #default="{ row }">{{ formatDate(row.joined_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80" align="center">
            <template #default="{ row }">
              <el-button
                v-if="canManageOrganization && row.user_id !== authStore.user?.id && row.role !== 'owner'"
                size="small" text type="danger"
                @click="removeMember(row)"
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 工作空间列表 -->
        <div class="section-title" style="margin-top: 24px">
          <h3>共享资料库</h3>
          <el-button v-if="canManageOrganization" size="small" :icon="Plus" @click="showCreateWs = true">新建资料库</el-button>
        </div>

        <div class="workspaces-list">
          <div
            v-for="ws in managedWorkspaces"
            :key="ws.id"
            class="ws-item"
          >
            <el-icon><FolderOpened /></el-icon>
            <span>{{ ws.name }}</span>
          </div>
        </div>
      </template>
    </template>

    <!-- 创建组织弹窗 -->
    <el-dialog v-model="showCreateTeam" title="创建组织" width="400px">
      <el-form :model="createTeamForm" label-width="80px">
        <el-form-item label="组织名称">
          <el-input v-model="createTeamForm.name" placeholder="我的组织" />
        </el-form-item>
        <el-form-item label="标识符">
          <el-input v-model="createTeamForm.slug" placeholder="my-team（小写字母和连字符）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateTeam = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="doCreateTeam">创建</el-button>
      </template>
    </el-dialog>

    <!-- 邀请成员弹窗 -->
    <el-dialog v-model="showInvite" title="邀请成员" width="400px">
      <el-form :model="inviteForm" label-width="80px">
        <el-form-item label="邮箱">
          <el-input v-model="inviteForm.email" placeholder="成员邮箱地址" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="inviteForm.role">
            <el-option label="普通成员" value="member" />
            <el-option label="管理员" value="admin" />
            <el-option label="只读" value="viewer" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showInvite = false">取消</el-button>
        <el-button type="primary" :loading="inviting" @click="doInvite">邀请</el-button>
      </template>
    </el-dialog>

    <!-- 创建工作空间弹窗 -->
    <el-dialog v-model="showCreateWs" title="新建资料库" width="400px">
      <el-form :model="createWsForm" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="createWsForm.name" placeholder="资料库名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="createWsForm.description" type="textarea" rows="2" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateWs = false">取消</el-button>
        <el-button type="primary" :loading="creatingWs" @click="doCreateWs">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, FolderOpened } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { createTeam, createWorkspace, listTeamMembers, listTeams, listTeamWorkspaces, inviteMember, removeMember as removeTeamMember } from '@/api/sync'

const authStore = useAuthStore()
const appStore = useAppStore()

const managedTeams = ref([])
const managedTeamId = ref('')
const managedTeam = computed(() => managedTeams.value.find(team => team.id === managedTeamId.value) || null)
const managedWorkspaces = ref([])
const members = ref([])
const canManageOrganization = computed(() => ['owner', 'admin'].includes(members.value.find(member => member.user_id === authStore.user?.id)?.role))
const loadingMembers = ref(false)
const showCreateTeam = ref(false)
const showInvite = ref(false)
const showCreateWs = ref(false)
const creating = ref(false)
const inviting = ref(false)
const creatingWs = ref(false)

const createTeamForm = ref({ name: '', slug: '' })
const inviteForm = ref({ email: '', role: 'member' })
const createWsForm = ref({ name: '', description: '' })

let active = true
let scopeGeneration = 0
const cloudUrl = () => JSON.stringify([appStore.settings.cloud_api_url || '', authStore.serverId, appStore.libraryPath])
const captureScope = () => ({ generation: scopeGeneration, actorId: authStore.user?.id, cloudUrl: cloudUrl() })
const isCurrent = scope => active && authStore.isLoggedIn && scope.actorId &&
  scope.generation === scopeGeneration && scope.actorId === authStore.user?.id && scope.cloudUrl === cloudUrl()

watch([() => authStore.user?.id, () => authStore.isLoggedIn, cloudUrl], () => {
  scopeGeneration++
  showCreateTeam.value = false; showInvite.value = false; showCreateWs.value = false
  creating.value = false; inviting.value = false; creatingWs.value = false
  createTeamForm.value = { name: '', slug: '' }
  inviteForm.value = { email: '', role: 'member' }
  createWsForm.value = { name: '', description: '' }
  managedTeams.value = []; managedTeamId.value = ''; managedWorkspaces.value = []; members.value = []
}, { flush: 'sync' })

function selectTeam(team) {
  managedTeamId.value = team.id
}
watch(managedTeamId, () => {
  showInvite.value = false; showCreateWs.value = false
  inviting.value = false; creatingWs.value = false
  inviteForm.value = { email: '', role: 'member' }
  createWsForm.value = { name: '', description: '' }
}, { flush: 'sync' })

let teamsRequest = 0
async function loadManagedTeams() {
  const requestId = ++teamsRequest, scope = captureScope()
  if (!isCurrent(scope)) return
  try {
    const response = await listTeams()
    if (!isCurrent(scope) || requestId !== teamsRequest) return
    managedTeams.value = response
    if (!response.some(team => team.id === managedTeamId.value)) managedTeamId.value = response[0]?.id || ''
  } catch { /* 拦截器显示错误；此页不会改变云仓库的选择。 */ }
}

let membersRequest = 0
async function loadMembers() {
  const requestId = ++membersRequest
  const teamId = managedTeam.value?.id
  const scope = captureScope()
  members.value = []; managedWorkspaces.value = []
  if (!teamId || !isCurrent(scope)) {
    loadingMembers.value = false
    return
  }
  loadingMembers.value = true
  try {
    const [response, workspaces] = await Promise.all([listTeamMembers(teamId), listTeamWorkspaces(teamId)])
    if (requestId === membersRequest && isCurrent(scope) && managedTeam.value?.id === teamId) {
      members.value = response
      managedWorkspaces.value = workspaces
    }
  } catch {
    // 请求拦截器已显示错误，保留空列表以免误操作旧团队成员。
  } finally {
    if (requestId === membersRequest) loadingMembers.value = false
  }
}

async function doCreateTeam() {
  const scope = captureScope()
  if (creating.value || !isCurrent(scope)) return
  if (!createTeamForm.value.name || !createTeamForm.value.slug) {
    ElMessage.warning('请填写组织名称和标识符')
    return
  }
  creating.value = true
  try {
    const team = await createTeam(createTeamForm.value)
    if (!isCurrent(scope)) return
    ElMessage.success('组织创建成功')
    showCreateTeam.value = false
    createTeamForm.value = { name: '', slug: '' }
    await Promise.all([loadManagedTeams(), authStore.refreshWorkspaces({ autoSelect: false })])
    if (!isCurrent(scope)) return
    managedTeamId.value = team.id
  } catch { /* 请求错误已由拦截器显示。 */
  } finally {
    if (isCurrent(scope)) creating.value = false
  }
}

async function doInvite() {
  const scope = captureScope()
  const teamId = managedTeam.value?.id
  if (inviting.value || !teamId || !isCurrent(scope)) return
  if (!inviteForm.value.email) {
    ElMessage.warning('请输入成员邮箱')
    return
  }
  inviting.value = true
  try {
    await inviteMember(teamId, inviteForm.value)
    if (!isCurrent(scope) || managedTeam.value?.id !== teamId) return
    ElMessage.success('邀请成功')
    showInvite.value = false
    inviteForm.value = { email: '', role: 'member' }
    await Promise.all([loadMembers(), authStore.refreshWorkspaces({ autoSelect: false })])
  } catch { /* 请求错误已由拦截器显示。 */
  } finally {
    if (isCurrent(scope) && managedTeam.value?.id === teamId) inviting.value = false
  }
}

async function doCreateWs() {
  const scope = captureScope()
  const team = managedTeam.value
  if (creatingWs.value || !team || !isCurrent(scope)) return
  if (!createWsForm.value.name) {
    ElMessage.warning('请输入资料库名称')
    return
  }
  creatingWs.value = true
  try {
    await createWorkspace(team.id, createWsForm.value)
    if (!isCurrent(scope) || managedTeam.value?.id !== team.id) return
    ElMessage.success('资料库创建成功')
    showCreateWs.value = false
    createWsForm.value = { name: '', description: '' }
    await Promise.all([loadMembers(), authStore.refreshWorkspaces({ autoSelect: false })])
  } catch { /* 请求错误已由拦截器显示。 */
  } finally {
    if (isCurrent(scope) && managedTeam.value?.id === team.id) creatingWs.value = false
  }
}

async function removeMember(member) {
  const scope = captureScope()
  const teamId = managedTeam.value?.id
  if (!teamId || !isCurrent(scope)) return
  try {
    await ElMessageBox.confirm(`确认移除成员 ${member.display_name}？`, '移除成员', { type: 'warning' })
    if (!isCurrent(scope) || managedTeam.value?.id !== teamId) return
    await removeTeamMember(teamId, member.user_id)
    if (!isCurrent(scope) || managedTeam.value?.id !== teamId) return
    ElMessage.success('已移除成员')
    await loadMembers()
  } catch { /* 取消确认时无需提示，请求错误已由拦截器显示。 */ }
}

async function handleLogout() {
  if (!await authStore.logout()) {
    ElMessage.error(authStore.workspaceError || '退出失败，请重试')
    return
  }
  ElMessage.success('已退出登录')
}

function roleLabel(role) {
  return { owner: '拥有者', admin: '管理员', member: '成员', viewer: '只读' }[role] || role
}

function roleTagType(role) {
  return { owner: 'danger', admin: 'warning', member: '', viewer: 'info' }[role] || ''
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++ }
  return `${bytes.toFixed(1)} ${units[i]}`
}

function formatDate(str) {
  return str ? new Date(str).toLocaleDateString('zh-CN') : ''
}

watch([() => managedTeam.value?.id, () => authStore.user?.id, () => authStore.isLoggedIn, cloudUrl], loadMembers, { immediate: true })
watch([() => authStore.user?.id, () => authStore.isLoggedIn, cloudUrl], loadManagedTeams, { immediate: true })
onBeforeUnmount(() => { active = false; membersRequest++; teamsRequest++; scopeGeneration++ })
</script>

<style scoped>
.management-help { color: #68788a; font-size: 13px; margin: 8px 0 0; }
.team-page { max-width: 900px; }

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}

.page-header h2 { margin: 0; font-size: 22px; }

.not-login-card,
.user-info-card { margin-bottom: 24px; }

.user-row {
  display: flex;
  align-items: center;
  gap: 16px;
}

.user-detail { flex: 1; }
.user-name { font-size: 16px; font-weight: 600; }
.user-email { font-size: 13px; color: #909399; margin-top: 2px; }

.teams-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}

.team-card {
  cursor: pointer;
  transition: all 0.2s;
  border: 2px solid transparent;
}

.team-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.1); }
.team-card.is-current { border-color: #4096ff; }

.team-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.team-name { font-size: 16px; font-weight: 600; }
.team-storage { font-size: 12px; color: #909399; }

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.section-title h3 { margin: 0; font-size: 16px; }

.workspaces-list { display: flex; flex-direction: column; gap: 8px; }

.ws-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-radius: 8px;
  transition: all 0.2s;
  border: 1px solid #e8eaed;
  background: #fff;
}

.ws-item:hover { background: #f0f7ff; border-color: #4096ff; }
.ws-item.is-current { background: #e8f4ff; border-color: #4096ff; color: #4096ff; font-weight: 600; }

.empty-teams { margin: 40px 0; }
</style>
