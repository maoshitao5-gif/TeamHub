<template>
  <div class="cloud-repo-page">

    <!-- 页头 -->
    <div class="page-header">
      <div class="header-left">
        <div class="page-title">云仓库</div>
        <div v-if="workspaceBound && stats.total > 0" class="header-stats">
          <span>{{ stats.total }} 个文档</span>
          <span class="stats-sep">·</span>
          <span>{{ formatSize(stats.totalSize) }}</span>
        </div>
      </div>
      <div class="header-right">
        <el-button size="small" :loading="loading" @click="refreshCurrentTab">
          <el-icon><RefreshRight /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <!-- 未绑定工作空间：引导 -->
    <template v-if="!workspaceBound">
      <div class="empty-state-wrap" v-loading="loading">
        <el-empty v-if="!loading" description="请先在设置页面登录云服务并绑定工作空间" :image-size="120">
          <el-button type="primary" @click="$router.push('/settings')">去设置</el-button>
        </el-empty>
      </div>
    </template>

    <!-- 主内容（已绑定工作空间） -->
    <template v-else>
      <el-tabs v-model="activeTab" class="main-tabs" @tab-change="onTabChange">

        <!-- ===== 全部文档标签页 ===== -->
        <el-tab-pane label="全部文档" name="docs">
          <template #label>
            <span>全部文档</span>
          </template>

          <!-- 搜索面板 -->
          <div class="search-panel">
            <div class="search-row">
              <el-input
                v-model="searchKeyword"
                placeholder="搜索文档名称..."
                clearable
                :prefix-icon="SearchIcon"
                @input="debouncedSearch"
                @clear="debouncedSearch"
              />
              <el-select
                v-model="searchTags"
                multiple
                filterable
                placeholder="按标签筛选"
                style="min-width:200px;"
                @change="debouncedSearch"
                clearable
              >
                <el-option
                  v-for="tag in cloudTags"
                  :key="tag.id"
                  :label="tag.name"
                  :value="tag.name"
                />
              </el-select>
            </div>
            <div class="filter-bar">
              <span class="result-count">共 {{ searchTotal }} 个文档</span>
              <el-pagination
                v-if="searchTotal > pageSize"
                layout="prev, pager, next"
                :total="searchTotal"
                :page-size="pageSize"
                v-model:current-page="currentPage"
                @current-change="doSearch"
                small
              />
            </div>
          </div>

          <!-- 文档列表 -->
          <div class="document-list" v-loading="docsLoading">
            <div v-if="!docsLoading && searchDocs.length === 0" class="empty-state">
              <el-icon :size="64" color="#bdc3c7"><FolderOpened /></el-icon>
              <p v-if="searchKeyword || searchTags.length > 0">未找到匹配的云端文档</p>
              <p v-else>云仓库暂无文档</p>
              <p class="empty-hint">在文档库中选择文档「推送到云端」即可同步</p>
            </div>

            <div
              v-for="doc in searchDocs"
              :key="doc.id"
              class="doc-card-wrap"
            >
              <div class="doc-card" @click="openDetail(doc)">
                <div class="doc-icon">
                  <el-icon :size="22" :color="doc.is_folder ? '#e67e22' : '#4096ff'">
                    <FolderOpened v-if="doc.is_folder" />
                    <Document v-else />
                  </el-icon>
                </div>
                <div class="doc-info">
                  <div class="doc-name">{{ doc.name }}</div>
                  <div class="doc-tags" v-if="doc.tags?.length > 0">
                    <el-tag
                      v-for="tag in doc.tags"
                      :key="tag.id || tag.name"
                      size="small"
                      effect="light"
                    >{{ tag.name || tag }}</el-tag>
                  </div>
                </div>
                <div class="doc-meta">
                  <span v-if="doc.uploader_display_name" class="uploader">{{ doc.uploader_display_name }}</span>
                  <span v-if="doc.version_count > 1" class="version-badge">v{{ doc.version_count }}</span>
                  <span>{{ formatSize(doc.total_size) }}</span>
                  <span class="meta-sep">·</span>
                  <span>{{ formatTime(doc.updated_at) }}</span>
                </div>
                <div class="doc-actions" @click.stop>
                  <el-tooltip v-if="doc.is_folder" content="文件夹暂不支持拉取" placement="top">
                    <el-button text size="small" type="primary" disabled>拉取到本地</el-button>
                  </el-tooltip>
                  <el-button
                    v-if="!doc.is_folder"
                    text size="small"
                    type="primary"
                    @click="openPullDialog(doc)"
                  >拉取到本地</el-button>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- ===== 最近动态标签页 ===== -->
        <el-tab-pane label="最近动态" name="activity">
          <div class="activity-panel" v-loading="activityLoading">
            <div v-if="!activityLoading && activityList.length === 0" class="empty-state">
              <p>暂无动态</p>
            </div>
            <el-timeline v-else>
              <el-timeline-item
                v-for="item in activityList"
                :key="item.id"
                :timestamp="formatTime(item.created_at)"
                placement="top"
              >
                <div class="activity-content">
                  <span class="activity-user">{{ item.user_display_name || '未知用户' }}</span>
                  <span class="activity-action">{{ formatOperation(item.operation, item.entity_type) }}</span>
                  <span class="activity-target">「{{ item.payload?.name || item.entity_id }}」</span>
                </div>
              </el-timeline-item>
            </el-timeline>
          </div>
        </el-tab-pane>

        <!-- ===== 待审申请标签页（仅管理员） ===== -->
        <el-tab-pane name="requests" v-if="isTeamAdmin">
          <template #label>
            <span>
              待审申请
              <el-badge v-if="pendingRequestCount > 0" :value="pendingRequestCount" :max="99" class="tab-badge" />
            </span>
          </template>

          <div class="requests-panel" v-loading="requestsLoading">
            <div v-if="!requestsLoading && joinRequests.length === 0" class="empty-state">
              <p>暂无待审批的加入申请</p>
            </div>

            <div
              v-for="req in joinRequests"
              :key="req.id"
              class="request-card"
            >
              <div class="request-info">
                <div class="request-user">
                  <span class="request-name">{{ req.user_display_name }}</span>
                  <span class="request-email">{{ req.user_email }}</span>
                </div>
                <div v-if="req.message" class="request-message">
                  "{{ req.message }}"
                </div>
                <div class="request-time">{{ formatTime(req.created_at) }}</div>
              </div>
              <div class="request-actions">
                <el-button
                  type="primary"
                  size="small"
                  :loading="reviewingId === req.id"
                  @click="handleReview(req, 'approve')"
                >通过</el-button>
                <el-button
                  size="small"
                  :loading="reviewingId === req.id"
                  @click="handleReview(req, 'reject')"
                >拒绝</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>

    <!-- 文档详情抽屉 -->
    <el-drawer
      v-model="showDetail"
      :title="detailDoc?.name || '文档详情'"
      size="420px"
    >
      <template v-if="detailDoc">
        <div class="detail-section">
          <h4>基本信息</h4>
          <div class="detail-item"><span class="label">名称</span><span>{{ detailDoc.name }}</span></div>
          <div class="detail-item" v-if="detailDoc.description"><span class="label">描述</span><span>{{ detailDoc.description }}</span></div>
          <div class="detail-item"><span class="label">大小</span><span>{{ formatSize(detailDoc.total_size) }}</span></div>
          <div class="detail-item" v-if="detailDoc.uploader_display_name"><span class="label">推送者</span><span>{{ detailDoc.uploader_display_name }}</span></div>
          <div class="detail-item"><span class="label">更新时间</span><span>{{ formatTime(detailDoc.updated_at) }}</span></div>
          <div class="detail-tags" v-if="detailDoc.tags?.length">
            <span class="label">标签</span>
            <div class="tags-list">
              <el-tag v-for="t in detailDoc.tags" :key="t.id" size="small" effect="light">{{ t.name }}</el-tag>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <h4>版本历史</h4>
          <div v-if="detailVersionsLoading" class="versions-loading">
            <el-icon class="is-loading"><Loading /></el-icon> 加载中…
          </div>
          <div v-else-if="detailVersions.length === 0" class="versions-empty">暂无版本</div>
          <div v-else class="versions-list">
            <div v-for="ver in detailVersions" :key="ver.id" class="version-item">
              <div class="version-left">
                <span class="version-num">v{{ ver.version_number }}</span>
                <el-tag v-if="ver.is_current" size="small" type="success" effect="plain">当前</el-tag>
              </div>
              <div class="version-meta">
                <span>{{ formatSize(ver.file_size) }}</span>
                <span class="meta-sep">·</span>
                <span>{{ formatTime(ver.created_at) }}</span>
                <span v-if="ver.note" class="version-note">· {{ ver.note }}</span>
              </div>
              <el-button
                text size="small"
                :loading="downloadingVersionId === ver.id"
                @click="downloadVersion(detailDoc, ver)"
              >
                <el-icon v-if="downloadingVersionId !== ver.id"><Download /></el-icon>
                下载
              </el-button>
            </div>
          </div>
        </div>

        <div class="detail-actions" v-if="!detailDoc.is_folder">
          <el-button type="primary" @click="openPullDialog(detailDoc)">
            <el-icon><Download /></el-icon>
            拉取到本地
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!-- 拉取对话框 -->
    <el-dialog
      v-model="showPullDialog"
      title="拉取到本地文档库"
      width="480px"
      :close-on-click-modal="false"
    >
      <div v-if="pullingDoc" class="pull-source-info">
        <el-icon style="font-size:13px; color:#909399; vertical-align:middle;"><Document /></el-icon>
        <span>{{ pullingDoc.name }}</span>
      </div>
      <el-form label-width="80px" label-position="left" style="margin-top:16px;">
        <el-form-item label="存放到">
          <DirectoryTreeSelector v-model="pullTargetDir" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPullDialog = false" size="default">取消</el-button>
        <el-button type="primary" @click="submitPull" :loading="pullSubmitLoading" size="default">
          <el-icon v-if="!pullSubmitLoading"><Download /></el-icon>
          确认拉取
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  RefreshRight, FolderOpened, Document, Download, Loading,
  Search as SearchIcon,
} from '@element-plus/icons-vue'
import {
  listCloudDocVersions, getCloudVersionDownloadUrl, pullDoc, getSyncStatus,
  searchCloudDocs, getWorkspaceActivity,
  listTeamJoinRequests, reviewJoinRequest, getPendingJoinRequestCount,
} from '@/api/sync'
import { listCloudDocs } from '@/api/sync'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import DirectoryTreeSelector from '@/components/DirectoryTreeSelector.vue'

const router = useRouter()
const appStore = useAppStore()
const authStore = useAuthStore()

// ===== 状态 =====
const loading = ref(false)
const workspaceBound = ref(false)
const activeTab = ref('docs')
let _currentWorkspaceId = ''
let _currentTeamId = ''

// ===== 统计 =====
const stats = ref({ total: 0, totalSize: 0 })

// ===== 管理员判断 =====
const isTeamAdmin = computed(() => {
  if (!authStore.currentTeam) return false
  // 通过 currentTeam 里的成员列表或 role 判断
  // 简化：如果用户是团队创建者/admin，显示待审标签页
  return true  // 暂时所有成员都可以看到（后端会做权限校验）
})

// ===== 文档搜索（服务端） =====
const searchKeyword = ref('')
const searchTags = ref([])
const searchDocs = ref([])
const searchTotal = ref(0)
const currentPage = ref(1)
const pageSize = 20
const docsLoading = ref(false)
const cloudTags = ref([])

let searchTimer = null
function debouncedSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    currentPage.value = 1
    doSearch()
  }, 400)
}

async function doSearch() {
  if (!_currentWorkspaceId) return
  docsLoading.value = true
  try {
    const res = await searchCloudDocs({
      q: searchKeyword.value,
      tags: searchTags.value.join(','),
      page: currentPage.value,
      page_size: pageSize,
    })
    searchDocs.value = res.items || []
    searchTotal.value = res.total || 0
  } catch {
    searchDocs.value = []
    searchTotal.value = 0
  }
  docsLoading.value = false
}

// ===== 活动流 =====
const activityList = ref([])
const activityLoading = ref(false)

async function fetchActivity() {
  if (!_currentWorkspaceId) return
  activityLoading.value = true
  try {
    activityList.value = await getWorkspaceActivity(_currentWorkspaceId, 50)
  } catch {
    activityList.value = []
  }
  activityLoading.value = false
}

function formatOperation(operation, entityType) {
  const opMap = {
    create: '推送了',
    update: '更新了',
    delete: '删除了',
    trash: '移入回收站',
    restore: '恢复了',
  }
  const typeMap = {
    document: '文档',
    version: '版本',
    tag: '标签',
  }
  return `${opMap[operation] || operation}${typeMap[entityType] || entityType}`
}

// ===== 加入申请管理 =====
const joinRequests = ref([])
const requestsLoading = ref(false)
const pendingRequestCount = ref(0)
const reviewingId = ref(null)

async function fetchJoinRequests() {
  if (!_currentTeamId) return
  requestsLoading.value = true
  try {
    joinRequests.value = await listTeamJoinRequests(_currentTeamId, 'pending')
    pendingRequestCount.value = joinRequests.value.length
  } catch {
    joinRequests.value = []
  }
  requestsLoading.value = false
}

async function fetchPendingCount() {
  try {
    const res = await getPendingJoinRequestCount()
    pendingRequestCount.value = res.count || 0
  } catch {
    pendingRequestCount.value = 0
  }
}

async function handleReview(req, action) {
  reviewingId.value = req.id
  try {
    await reviewJoinRequest(_currentTeamId, req.id, action)
    ElMessage.success(action === 'approve' ? '已通过' : '已拒绝')
    await fetchJoinRequests()
  } catch { /* interceptor */ }
  reviewingId.value = null
}

// ===== Tab 切换 =====
function onTabChange(tab) {
  if (tab === 'activity') fetchActivity()
  if (tab === 'requests') fetchJoinRequests()
}

function refreshCurrentTab() {
  if (activeTab.value === 'docs') {
    doSearch()
    fetchCloudTags()
  }
  if (activeTab.value === 'activity') fetchActivity()
  if (activeTab.value === 'requests') fetchJoinRequests()
}

// ===== 文档详情抽屉 =====
const showDetail = ref(false)
const detailDoc = ref(null)
const detailVersions = ref([])
const detailVersionsLoading = ref(false)

async function openDetail(doc) {
  detailDoc.value = doc
  showDetail.value = true
  detailVersionsLoading.value = true
  detailVersions.value = []
  try {
    const list = await listCloudDocVersions(doc.id, _currentWorkspaceId)
    detailVersions.value = Array.isArray(list)
      ? list.sort((a, b) => b.version_number - a.version_number)
      : []
  } catch {
    detailVersions.value = []
  }
  detailVersionsLoading.value = false
}

// ===== 版本下载 =====
const downloadingVersionId = ref(null)

async function downloadVersion(doc, ver) {
  downloadingVersionId.value = ver.id
  try {
    const res = await getCloudVersionDownloadUrl(doc.id, ver.id, _currentWorkspaceId)
    const url = res?.download_url
    if (!url) throw new Error('未获取到下载链接')

    const filename = res.original_filename || `${doc.name}_v${ver.version_number}`

    if (window.electron?.downloadUrl) {
      const saveDir = await window.electron.selectDirectory()
      if (!saveDir) return
      const localPath = `${saveDir}/${filename}`
      await window.electron.downloadUrl(url, localPath)
      ElMessage.success(`已下载到 ${localPath}`)
    } else {
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || '下载失败')
  } finally {
    downloadingVersionId.value = null
  }
}

// ===== 拉取对话框 =====
const showPullDialog = ref(false)
const pullingDoc = ref(null)
const pullTargetDir = ref('')
const pullSubmitLoading = ref(false)

function openPullDialog(doc) {
  pullingDoc.value = doc
  pullTargetDir.value = ''
  showPullDialog.value = true
}

async function submitPull() {
  if (!pullingDoc.value) return
  pullSubmitLoading.value = true
  try {
    await pullDoc(_currentWorkspaceId, pullingDoc.value.id, pullTargetDir.value || '')
    ElMessage.success(`「${pullingDoc.value.name}」已拉取到本地文档库`)
    showPullDialog.value = false
    showDetail.value = false
    router.push('/library')
  } catch (e) {
    if (e.response?.status === 409) {
      ElMessage.info(e.response.data?.detail || '本地文件与云端一致，无需重复拉取')
      showPullDialog.value = false
    } else {
      ElMessage.error('拉取失败：' + (e.response?.data?.detail || e.message || '未知错误'))
    }
  } finally {
    pullSubmitLoading.value = false
  }
}

// ===== 云端标签 =====
async function fetchCloudTags() {
  if (!_currentWorkspaceId) return
  try {
    // 从全量文档列表提取标签（复用 listCloudDocs）
    const res = await listCloudDocs(_currentWorkspaceId)
    const docs = Array.isArray(res) ? res : (res?.documents || [])
    const map = new Map()
    for (const doc of docs) {
      for (const t of (doc.tags || [])) {
        const id = t.id || t.name
        if (!map.has(id)) map.set(id, { id, name: t.name || t, color: t.color || null })
      }
    }
    cloudTags.value = [...map.values()]
    // 更新统计
    stats.value = {
      total: docs.length,
      totalSize: docs.reduce((s, d) => s + (d.total_size || 0), 0),
    }
  } catch { /* ignore */ }
}

// ===== 初始化 =====
async function initPage() {
  loading.value = true
  try {
    const status = await getSyncStatus()
    if (!status?.workspace_id) {
      workspaceBound.value = false
      return
    }
    workspaceBound.value = true
    _currentWorkspaceId = status.workspace_id

    // 获取 team_id（通过 authStore）
    if (authStore.currentTeam) {
      _currentTeamId = authStore.currentTeam.id
    }

    // 并行加载：文档搜索 + 标签 + 待审数
    await Promise.all([
      doSearch(),
      fetchCloudTags(),
      fetchPendingCount(),
    ])
  } catch (e) {
    ElMessage.error('加载云仓库失败：' + (e.response?.data?.detail || e.message || '未知错误'))
  } finally {
    loading.value = false
  }
}

const formatSize = (bytes) => bytes ? formatFileSize(bytes) : '-'
const formatTime = (iso) => iso ? formatDateTime(iso) : '-'

onMounted(() => {
  initPage()
})

// 监听工作空间切换
if (typeof window !== 'undefined') {
  window.addEventListener('cloud:workspace-changed', () => {
    initPage()
  })
}
</script>

<style scoped>
.cloud-repo-page {
  padding: 0;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 16px;
}

.page-title {
  font-size: 22px;
  font-weight: 600;
  color: #1a2332;
}

.header-stats {
  font-size: 13px;
  color: #909399;
}

.stats-sep {
  margin: 0 6px;
}

.empty-state-wrap {
  min-height: 300px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ===== Tabs ===== */
.main-tabs :deep(.el-tabs__header) {
  margin-bottom: 16px;
}

.tab-badge {
  margin-left: 4px;
}

.tab-badge :deep(.el-badge__content) {
  font-size: 10px;
}

/* ===== 搜索面板 ===== */
.search-panel {
  background: #ffffff;
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 16px;
  box-shadow: var(--shadow-sm);
  border: 1px solid #ebeef5;
}

.search-row {
  display: flex;
  gap: 12px;
}

.filter-bar {
  display: flex;
  gap: 12px;
  margin-top: 12px;
  align-items: center;
  justify-content: space-between;
}

.result-count {
  font-size: 13px;
  color: #909399;
}

/* ===== 文档列表 ===== */
.document-list {
  min-height: 300px;
}

.doc-card-wrap {
  margin-bottom: 6px;
}

.doc-card {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  transition: var(--transition-base, all 0.22s ease);
  cursor: pointer;
  box-shadow: var(--shadow-sm);
}

.doc-card:hover {
  box-shadow: inset 3px 0 0 var(--color-accent), var(--shadow-hover);
  transform: translateY(-2px);
  border-color: #dde8f8;
}

.doc-icon {
  margin-right: 12px;
  flex-shrink: 0;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.doc-tags .el-tag {
  border-radius: 20px;
}

.doc-meta {
  font-size: 12px;
  color: #aab2bd;
  white-space: nowrap;
  margin-right: 12px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.uploader {
  color: #606266;
  font-weight: 500;
}

.version-badge {
  background: #e6f4ff;
  color: #4096ff;
  padding: 1px 6px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

.meta-sep {
  margin: 0 2px;
}

.doc-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.empty-state {
  text-align: center;
  padding: 60px 0;
  color: #909399;
}

.empty-state p {
  margin: 12px 0 0;
  font-size: 14px;
}

.empty-hint {
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 6px !important;
}

/* ===== 活动流 ===== */
.activity-panel {
  min-height: 200px;
  padding: 8px 0;
}

.activity-content {
  font-size: 14px;
}

.activity-user {
  font-weight: 600;
  color: #303133;
}

.activity-action {
  color: #606266;
  margin: 0 4px;
}

.activity-target {
  color: #4096ff;
  font-weight: 500;
}

/* ===== 待审申请 ===== */
.requests-panel {
  min-height: 200px;
}

.request-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  margin-bottom: 8px;
  box-shadow: var(--shadow-sm);
}

.request-info {
  flex: 1;
  min-width: 0;
}

.request-user {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}

.request-name {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.request-email {
  font-size: 12px;
  color: #909399;
}

.request-message {
  font-size: 13px;
  color: #606266;
  font-style: italic;
  margin-bottom: 4px;
}

.request-time {
  font-size: 12px;
  color: #c0c4cc;
}

.request-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
  margin-left: 16px;
}

/* ===== 详情抽屉 ===== */
.detail-section {
  margin-bottom: 24px;
}

.detail-section h4 {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid #ebeef5;
}

.detail-item {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  font-size: 13px;
}

.detail-item .label {
  color: #909399;
  flex-shrink: 0;
  margin-right: 12px;
}

.detail-tags {
  padding: 6px 0;
  font-size: 13px;
}

.detail-tags .label {
  color: #909399;
  display: block;
  margin-bottom: 6px;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tags-list .el-tag {
  border-radius: 20px;
}

.detail-actions {
  padding-top: 16px;
  border-top: 1px solid #ebeef5;
}

.detail-actions .el-button {
  width: 100%;
}

/* ===== 版本列表（抽屉内） ===== */
.versions-loading {
  font-size: 13px;
  color: #909399;
  padding: 8px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.versions-empty {
  font-size: 13px;
  color: #c0c4cc;
  padding: 8px 0;
}

.versions-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.version-item {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed #ebeef5;
}

.version-item:last-child {
  border-bottom: none;
}

.version-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 60px;
}

.version-num {
  font-weight: 600;
  color: #303133;
}

.version-meta {
  color: #909399;
  flex: 1;
}

.version-note {
  color: #606266;
  font-style: italic;
}

/* ===== 拉取对话框 ===== */
.pull-source-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #606266;
  padding: 10px 14px;
  background: #f5f7fa;
  border-radius: 6px;
  border: 1px solid #ebeef5;
}
</style>
