<template>
  <div class="team-page">
    <div class="page-header">
      <h2>团队管理</h2>
      <el-button type="primary" :icon="Plus" @click="showCreateTeam = true">创建团队</el-button>
    </div>

    <!-- 未登录提示 -->
    <el-card v-if="!authStore.isLoggedIn" class="not-login-card">
      <el-empty description="请先登录云服务账号以使用团队功能">
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
      <div v-if="authStore.teams.length === 0" class="empty-teams">
        <el-empty description="您还没有加入任何团队">
          <el-button type="primary" @click="showCreateTeam = true">创建第一个团队</el-button>
        </el-empty>
      </div>

      <div v-else class="teams-grid">
        <el-card
          v-for="team in authStore.teams"
          :key="team.id"
          class="team-card"
          :class="{ 'is-current': authStore.currentTeam?.id === team.id }"
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
      <template v-if="authStore.currentTeam">
        <div class="section-title">
          <h3>{{ authStore.currentTeam.name }} — 成员</h3>
          <el-button size="small" :icon="Plus" @click="showInvite = true">邀请成员</el-button>
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
                v-if="row.user_id !== authStore.user?.id && row.role !== 'owner'"
                size="small" text type="danger"
                @click="removeMember(row)"
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <!-- 工作空间列表 -->
        <div class="section-title" style="margin-top: 24px">
          <h3>工作空间</h3>
          <el-button size="small" :icon="Plus" @click="showCreateWs = true">新建工作空间</el-button>
        </div>

        <div class="workspaces-list">
          <div
            v-for="ws in authStore.workspaces"
            :key="ws.id"
            class="ws-item"
            :class="{ 'is-current': authStore.currentWorkspace?.id === ws.id }"
            @click="authStore.switchWorkspace(ws)"
          >
            <el-icon><FolderOpened /></el-icon>
            <span>{{ ws.name }}</span>
            <el-tag v-if="authStore.currentWorkspace?.id === ws.id" size="small" type="success">当前</el-tag>
          </div>
        </div>
      </template>
    </template>

    <!-- 创建团队弹窗 -->
    <el-dialog v-model="showCreateTeam" title="创建团队" width="400px">
      <el-form :model="createTeamForm" label-width="80px">
        <el-form-item label="团队名称">
          <el-input v-model="createTeamForm.name" placeholder="我的团队" />
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
    <el-dialog v-model="showCreateWs" title="新建工作空间" width="400px">
      <el-form :model="createWsForm" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="createWsForm.name" placeholder="工作空间名称" />
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
import { ref, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, FolderOpened } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { teamApi, workspaceApi } from '@/api/cloud'

const authStore = useAuthStore()

const members = ref([])
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

async function selectTeam(team) {
  await authStore.switchTeam(team)
  await loadMembers()
}

async function loadMembers() {
  if (!authStore.currentTeam) return
  loadingMembers.value = true
  try {
    members.value = await teamApi.listMembers(authStore.currentTeam.id)
  } finally {
    loadingMembers.value = false
  }
}

async function doCreateTeam() {
  if (!createTeamForm.value.name || !createTeamForm.value.slug) {
    ElMessage.warning('请填写团队名称和标识符')
    return
  }
  creating.value = true
  try {
    await teamApi.create(createTeamForm.value)
    ElMessage.success('团队创建成功')
    showCreateTeam.value = false
    createTeamForm.value = { name: '', slug: '' }
    await authStore.restoreSession()  // 重新加载团队列表
  } finally {
    creating.value = false
  }
}

async function doInvite() {
  if (!inviteForm.value.email) {
    ElMessage.warning('请输入成员邮箱')
    return
  }
  inviting.value = true
  try {
    await teamApi.inviteMember(authStore.currentTeam.id, inviteForm.value)
    ElMessage.success('邀请成功')
    showInvite.value = false
    inviteForm.value = { email: '', role: 'member' }
    await loadMembers()
  } finally {
    inviting.value = false
  }
}

async function doCreateWs() {
  if (!createWsForm.value.name) {
    ElMessage.warning('请输入工作空间名称')
    return
  }
  creatingWs.value = true
  try {
    await workspaceApi.create(authStore.currentTeam.id, createWsForm.value)
    ElMessage.success('工作空间创建成功')
    showCreateWs.value = false
    createWsForm.value = { name: '', description: '' }
    await authStore.switchTeam(authStore.currentTeam)  // 刷新工作空间列表
  } finally {
    creatingWs.value = false
  }
}

async function removeMember(member) {
  await ElMessageBox.confirm(`确认移除成员 ${member.display_name}？`, '移除成员', { type: 'warning' })
  await teamApi.removeMember(authStore.currentTeam.id, member.user_id)
  ElMessage.success('已移除成员')
  await loadMembers()
}

async function handleLogout() {
  await authStore.logout()
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

onMounted(async () => {
  if (authStore.isLoggedIn && authStore.currentTeam) {
    await loadMembers()
  }
})

watch(() => authStore.currentTeam, async (team) => {
  if (team) await loadMembers()
})
</script>

<style scoped>
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
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid #e8eaed;
  background: #fff;
}

.ws-item:hover { background: #f0f7ff; border-color: #4096ff; }
.ws-item.is-current { background: #e8f4ff; border-color: #4096ff; color: #4096ff; font-weight: 600; }

.empty-teams { margin: 40px 0; }
</style>
