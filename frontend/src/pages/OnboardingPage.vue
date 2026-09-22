<template>
  <div class="onboarding-page">
    <div class="onboarding-card">
      <div class="onboarding-logo">
        <el-icon :size="40"><FolderOpened /></el-icon>
        <h1>欢迎来到 TeamHub 云仓库</h1>
        <p class="subtitle">选择一种方式开始协作</p>
      </div>

      <!-- 选择模式 -->
      <div v-if="step === 'choose'" class="choose-panel">
        <div class="option-cards">
          <div class="option-card" @click="step = 'join'">
            <el-icon :size="32" color="#4096ff"><Search /></el-icon>
            <h3>加入现有仓库</h3>
            <p>搜索已有团队并申请加入</p>
          </div>
          <div class="option-card" @click="step = 'create'">
            <el-icon :size="32" color="#67c23a"><Plus /></el-icon>
            <h3>新建仓库</h3>
            <p>创建属于自己的云仓库</p>
          </div>
        </div>
      </div>

      <!-- 加入团队 -->
      <div v-if="step === 'join'" class="join-panel">
        <el-button text :icon="ArrowLeft" @click="step = 'choose'" class="back-btn">返回</el-button>

        <div class="search-box">
          <el-input
            v-model="searchQuery"
            placeholder="输入团队名称搜索..."
            size="large"
            clearable
            :prefix-icon="Search"
            @input="debouncedSearch"
          />
        </div>

        <div v-if="searchLoading" class="center-hint">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>搜索中...</span>
        </div>

        <div v-else-if="searchResults.length === 0 && searchQuery" class="center-hint">
          没有找到匹配的团队
        </div>

        <div v-else class="team-list">
          <div
            v-for="team in searchResults"
            :key="team.id"
            class="team-item"
            :class="{ selected: selectedTeam?.id === team.id }"
            @click="selectedTeam = team"
          >
            <div class="team-info">
              <span class="team-name">{{ team.name }}</span>
              <span class="team-slug">{{ team.slug }}</span>
            </div>
            <div class="team-meta">
              <span>{{ team.member_count }} 名成员</span>
              <span>{{ team.workspace_count }} 个工作空间</span>
            </div>
          </div>
        </div>

        <div v-if="selectedTeam" class="join-form">
          <el-input
            v-model="joinMessage"
            type="textarea"
            :rows="2"
            placeholder="留言（可选）：简单介绍自己"
            maxlength="500"
            show-word-limit
          />
          <el-button
            type="primary"
            size="large"
            class="action-btn"
            :loading="submitting"
            @click="submitJoinRequest"
          >
            申请加入「{{ selectedTeam.name }}」
          </el-button>
        </div>

        <!-- 已提交的申请状态 -->
        <div v-if="myRequests.length > 0" class="my-requests">
          <h4>我的申请</h4>
          <div v-for="req in myRequests" :key="req.id" class="request-item">
            <span class="request-team">{{ req.team_name }}</span>
            <el-tag
              :type="req.status === 'pending' ? 'warning' : req.status === 'approved' ? 'success' : 'danger'"
              size="small"
            >
              {{ req.status === 'pending' ? '待审批' : req.status === 'approved' ? '已通过' : '已拒绝' }}
            </el-tag>
          </div>
          <el-button text type="primary" @click="refreshMyRequests" :loading="refreshing">
            刷新状态
          </el-button>
        </div>
      </div>

      <!-- 创建团队 + 工作空间 -->
      <div v-if="step === 'create'" class="create-panel">
        <el-button text :icon="ArrowLeft" @click="step = 'choose'" class="back-btn">返回</el-button>

        <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-position="top">
          <el-form-item label="团队名称" prop="name">
            <el-input
              v-model="createForm.name"
              placeholder="如：产品研发部"
              size="large"
              @input="autoGenerateSlug"
            />
          </el-form-item>

          <el-form-item label="唯一标识 (slug)" prop="slug">
            <el-input v-model="createForm.slug" placeholder="如：product-dev" size="large">
              <template #prepend>teamhub/</template>
            </el-input>
          </el-form-item>

          <el-form-item label="工作空间名称" prop="workspaceName">
            <el-input v-model="createForm.workspaceName" placeholder="如：默认工作空间" size="large" />
          </el-form-item>

          <el-button
            type="primary"
            size="large"
            class="action-btn"
            :loading="submitting"
            @click="handleCreate"
          >
            一键创建
          </el-button>
        </el-form>
      </div>

      <!-- 底部跳过 -->
      <div class="onboarding-footer">
        <el-link type="info" @click="skipOnboarding">暂时跳过，稍后设置</el-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { FolderOpened, Search, Plus, ArrowLeft, Loading } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import {
  discoverTeams,
  createJoinRequest,
  getMyJoinRequests,
} from '@/api/sync'
import { teamApi, workspaceApi } from '@/api/cloud'
import { bindWorkspace } from '@/api/sync'

const router = useRouter()
const authStore = useAuthStore()

const step = ref('choose')  // choose | join | create
const submitting = ref(false)

// ===== 加入团队 =====
const searchQuery = ref('')
const searchLoading = ref(false)
const searchResults = ref([])
const selectedTeam = ref(null)
const joinMessage = ref('')
const myRequests = ref([])
const refreshing = ref(false)

let searchTimer = null
function debouncedSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    if (!searchQuery.value.trim()) {
      searchResults.value = []
      return
    }
    searchLoading.value = true
    try {
      searchResults.value = await discoverTeams(searchQuery.value)
    } catch { /* ignore */ }
    searchLoading.value = false
  }, 400)
}

async function submitJoinRequest() {
  if (!selectedTeam.value) return
  submitting.value = true
  try {
    await createJoinRequest(selectedTeam.value.id, joinMessage.value)
    ElMessage.success('申请已提交，等待管理员审批')
    selectedTeam.value = null
    joinMessage.value = ''
    await refreshMyRequests()
  } catch { /* interceptor shows error */ }
  submitting.value = false
}

async function refreshMyRequests() {
  refreshing.value = true
  try {
    myRequests.value = await getMyJoinRequests()
    // 检查是否有 approved 的申请 → 直接进入
    const approved = myRequests.value.find(r => r.status === 'approved')
    if (approved) {
      ElMessage.success('申请已通过！正在进入云仓库...')
      // 重新加载团队列表（会触发 onboardingChecked 更新）
      await authStore.restoreSession()
      router.push('/shared')
    }
  } catch { /* ignore */ }
  refreshing.value = false
}

// ===== 创建团队 =====
const createFormRef = ref(null)
const createForm = reactive({
  name: '',
  slug: '',
  workspaceName: '默认工作空间',
})
const createRules = {
  name: [{ required: true, message: '请输入团队名称', trigger: 'blur' }],
  slug: [
    { required: true, message: '请输入唯一标识', trigger: 'blur' },
    { pattern: /^[a-z0-9-]+$/, message: '仅支持小写字母、数字和短横线', trigger: 'blur' },
  ],
  workspaceName: [{ required: true, message: '请输入工作空间名称', trigger: 'blur' }],
}

function autoGenerateSlug() {
  if (!createForm.name) return
  createForm.slug = createForm.name
    .toLowerCase()
    .replace(/[\s_]+/g, '-')
    .replace(/[^a-z0-9\u4e00-\u9fa5-]/g, '')
    .replace(/[\u4e00-\u9fa5]/g, '')  // 移除中文字符
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 50)
  // 如果 slug 为空（纯中文名），生成随机后缀
  if (!createForm.slug) {
    createForm.slug = 'team-' + Date.now().toString(36)
  }
}

async function handleCreate() {
  const valid = await createFormRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    // 1. 创建团队
    const team = await teamApi.create({ name: createForm.name, slug: createForm.slug })
    // 2. 创建工作空间
    const ws = await workspaceApi.create(team.id, {
      name: createForm.workspaceName,
      description: '',
    })
    // 3. 绑定到本地后端
    await bindWorkspace(ws.id, ws.name)
    // 4. 刷新 store
    await authStore.restoreSession()
    ElMessage.success('云仓库创建成功！')
    router.push('/shared')
  } catch { /* interceptor shows error */ }
  submitting.value = false
}

function skipOnboarding() {
  // 标记已完成引导检查，不再自动跳转
  authStore.onboardingChecked = false
  router.push('/library')
}

onMounted(async () => {
  // 加载已有申请
  try {
    myRequests.value = await getMyJoinRequests()
  } catch { /* ignore */ }
  // 同时加载全部团队供浏览
  try {
    searchResults.value = await discoverTeams('')
  } catch { /* ignore */ }
})
</script>

<style scoped>
.onboarding-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e2d3d 0%, #2c3e50 100%);
  padding: 20px;
}

.onboarding-card {
  background: #fff;
  border-radius: 16px;
  padding: 48px 40px 36px;
  width: 520px;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
}

.onboarding-logo {
  text-align: center;
  margin-bottom: 32px;
}

.onboarding-logo .el-icon {
  color: #4096ff;
  margin-bottom: 12px;
}

.onboarding-logo h1 {
  font-size: 24px;
  font-weight: 700;
  color: #1e2d3d;
  margin: 0 0 8px;
}

.subtitle {
  color: #909399;
  font-size: 14px;
  margin: 0;
}

/* ===== 选择面板 ===== */
.option-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.option-card {
  border: 2px solid #e4e7ed;
  border-radius: 12px;
  padding: 28px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}

.option-card:hover {
  border-color: #4096ff;
  box-shadow: 0 4px 16px rgba(64, 150, 255, 0.15);
  transform: translateY(-2px);
}

.option-card h3 {
  margin: 14px 0 6px;
  font-size: 16px;
  color: #303133;
}

.option-card p {
  margin: 0;
  font-size: 13px;
  color: #909399;
}

/* ===== 通用 ===== */
.back-btn {
  margin-bottom: 16px;
}

.action-btn {
  width: 100%;
  margin-top: 16px;
  height: 44px;
  font-size: 16px;
}

.center-hint {
  text-align: center;
  padding: 24px 0;
  color: #909399;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

/* ===== 搜索面板 ===== */
.search-box {
  margin-bottom: 16px;
}

.team-list {
  max-height: 240px;
  overflow-y: auto;
  margin-bottom: 16px;
}

.team-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.team-item:hover {
  border-color: #4096ff;
  background: #f0f7ff;
}

.team-item.selected {
  border-color: #4096ff;
  background: #e6f4ff;
}

.team-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.team-name {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}

.team-slug {
  font-size: 12px;
  color: #909399;
}

.team-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #909399;
}

.join-form {
  margin-top: 16px;
}

/* ===== 我的申请 ===== */
.my-requests {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #e4e7ed;
}

.my-requests h4 {
  margin: 0 0 12px;
  font-size: 14px;
  color: #606266;
}

.request-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
}

.request-team {
  font-size: 14px;
  color: #303133;
}

/* ===== 底部 ===== */
.onboarding-footer {
  text-align: center;
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #e4e7ed;
}
</style>
