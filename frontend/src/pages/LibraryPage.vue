<template>
  <div class="library-page">
    <!-- 左侧目录树面板 -->
    <div class="tree-sidebar">
      <LibraryTreePanel ref="treePanelRef" :model-value="selectedFolder" :favorites-active="favoritesOnly" :show-folders="showTreeSidebar" @update:model-value="selectFolder" @favorites="selectFavorites" />
    </div>

    <!-- 右侧主内容区 -->
    <div class="library-main">

    <!-- 页头：标题 + 扫描按钮（始终可见） -->
    <div class="page-header">
      <div><div class="page-title">{{ favoritesOnly ? '我的收藏' : selectedFolder ? folderSegments.at(-1) : '全部资料' }}</div>
        <p class="scope-hint">{{ favoritesOnly ? '你收藏的资料，也保留在全部资料中' : selectedFolder ? `归入「${folderSegments.at(-1)}」文件夹的资料（含子文件夹）` : '所有已整理的资料都在这里' }}</p></div>
      <div style="display:flex;gap:8px;align-items:center;">
        <el-button size="small" :loading="scanLoading" @click="handleScan">
          <el-icon><RefreshRight /></el-icon>
          扫描文件库
        </el-button>
      </div>
    </div>

    <!-- 搜索面板（白色卡片包裹，与下方列表形成层次） -->
    <div class="search-panel">
      <SmartSearchBar
        ref="searchBarRef"
        @name-search="onNameSearch"
        @tag-search="onTagSearch"
      />
      <div class="filter-bar">
        <el-select v-model="filterType" placeholder="类型" clearable size="default" @change="handleSearch">
          <el-option label="全部" value="" />
          <el-option label="文件" :value="false" />
          <el-option label="文件夹" :value="true" />
        </el-select>

        <el-select v-model="sortBy" size="default" @change="handleSearch">
          <el-option v-if="keyword" label="相关程度" value="relevance" />
          <el-option label="最近更新" value="updated_at" />
          <el-option label="名称" value="name" />
          <el-option label="大小" value="total_size" />
          <el-option label="添加时间" value="created_at" />
        </el-select>

        <el-button @click="toggleSortOrder" size="default">
          <el-icon><Sort /></el-icon>
          {{ sortOrder === 'desc' ? '降序' : '升序' }}
        </el-button>
      </div>
    </div><!-- end search-panel -->

    <el-alert v-if="searchError" :title="searchError" type="error" :closable="false" />
    <el-button v-if="searchError" text @click="handleSearch">重试搜索</el-button>
    <!-- 目录路径面包屑 -->
    <div v-if="selectedFolder !== null" class="folder-breadcrumb">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>
          <span class="bc-link" @click="selectFolder(null)">全部资料</span>
        </el-breadcrumb-item>
        <template v-if="selectedFolder === '.'">
          <el-breadcrumb-item>根目录</el-breadcrumb-item>
        </template>
        <template v-else>
          <el-breadcrumb-item
            v-for="(seg, idx) in folderSegments"
            :key="idx"
          >
            <span
              v-if="idx < folderSegments.length - 1"
              class="bc-link"
              @click="selectFolderSegment(idx)"
            >{{ seg }}</span>
            <span v-else>{{ seg }}</span>
          </el-breadcrumb-item>
        </template>
      </el-breadcrumb>
      <el-button
        link
        size="small"
        style="margin-left: 8px; color: #909399;"
        @click="selectedFolder = null"
        title="清除目录过滤"
      >×</el-button>
    </div>

    <!-- 缺失文件警告 -->
    <el-alert v-if="missingDocs.length > 0" type="warning" :closable="false" style="margin-bottom: 16px;">
      <template #title>
        <div style="display:flex; align-items:center; justify-content:space-between; width:100%;">
          <span>{{ missingDocs.length }} 个文档的文件已缺失（文件可能被移动或删除）</span>
          <el-button size="small" :loading="scanLoading" @click="handleScan" style="margin-left:12px;">重新扫描</el-button>
        </div>
      </template>
      <div class="missing-list">
        <div v-for="doc in missingDocs" :key="doc.id" class="missing-item">
          <el-icon style="color:#e6a23c; flex-shrink:0;"><WarningFilled /></el-icon>
          <span class="missing-name">{{ doc.name }}</span>
          <span class="missing-path">{{ doc.storage_path || doc.original_path }}</span>
          <div class="missing-actions">
            <el-button size="small" @click="handleRelocate(doc)">重新定位</el-button>
            <el-button size="small" type="danger" text @click="handleRemoveMissing(doc)">移除</el-button>
          </div>
        </div>
      </div>
    </el-alert>

    <!-- 文档列表 -->
    <div class="document-list" v-loading="loading">
      <template v-if="documents.length > 0">
        <div
          v-for="doc in documents"
          :key="doc.id"
          class="doc-card"
          @click="openDetail(doc)"
        >
          <div class="doc-icon">
            <el-icon :size="22" :color="doc.is_folder ? '#e67e22' : getFileTypeColor(doc.name)">
              <Folder v-if="doc.is_folder" />
              <component v-else :is="fileIconComponent(doc.name)" />
            </el-icon>
          </div>
          <div class="doc-info">
            <div class="doc-name">{{ doc.name }}</div>
            <div class="doc-tags" v-if="doc.tags.length > 0">
              <el-tag
                v-for="tag in doc.tags"
                :key="tag.id"
                size="small"
                :color="tag.color || undefined"
                effect="light"
              >
                {{ tag.name }}
              </el-tag>
            </div>
            <div v-if="doc.cloud_pushed_at || doc.cloud_source === 'cloud_import'" class="cloud-origin-badges">
              <el-tag
                v-if="doc.cloud_pushed_at && cloudDocStatus(doc) === 'modified'"
                size="small"
                type="warning"
                effect="plain"
                class="cloud-badge"
              >● 本地已改</el-tag>
              <el-tag
                v-else-if="doc.cloud_pushed_at"
                size="small"
                type="success"
                effect="plain"
                class="cloud-badge"
              >✓ 已推送</el-tag>
              <el-tag
                v-if="doc.cloud_source === 'cloud_import'"
                size="small"
                type="info"
                effect="plain"
                class="cloud-badge"
              >↓ 来自云端</el-tag>
            </div>
            <SearchMatchExcerpt :match="doc.search_match" />
          </div>
          <div class="doc-meta">
            <span v-if="doc.is_folder">{{ doc.file_count }} 个文件</span>
            <span v-else>{{ formatSize(doc.total_size) }}</span>
            <span class="meta-sep">·</span>
            <span>{{ formatDate(doc.updated_at) }}</span>
            <span class="meta-sep">·</span>
          </div>
          <div class="doc-actions">
            <el-button text size="small" @click.stop="handleOpen(doc)">
              打开
            </el-button>
            <el-dropdown trigger="click" @command="(cmd) => handleCommand(cmd, doc)">
              <el-button text size="small" @click.stop>
                <el-icon><MoreFilled /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit">编辑信息</el-dropdown-item>
                  <el-dropdown-item command="tags">编辑标签</el-dropdown-item>
                  <el-dropdown-item command="versions">版本历史</el-dropdown-item>
                  <el-dropdown-item command="favorite">{{ favoriteIds[doc.id] ? '取消收藏' : '收藏' }}</el-dropdown-item>
                  <el-dropdown-item v-if="doc.storage_path && doc.storage_mode !== 'index'" command="move-folder">归入文件夹</el-dropdown-item>
                  <el-dropdown-item v-if="canRemoveFromFolder(doc)" command="remove-folder">移出文件夹</el-dropdown-item>
                  <el-dropdown-item divided command="push-to-cloud">
                    ↑ 发布 / 更新共享资料
                  </el-dropdown-item>
                  <el-dropdown-item command="check-doc" :disabled="!doc.cloud_doc_id">
                    ⊙ 查看共享状态
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </template>

      <!-- 空状态 -->
      <div v-else-if="!loading" class="empty-state">
        <el-icon :size="64" color="#bdc3c7"><FolderOpened /></el-icon>
        <p v-if="keyword">未找到匹配的文档</p>
        <template v-else>
          <p>{{ favoritesOnly ? '暂无匹配的收藏资料' : selectedFolder ? '此文件夹暂无匹配资料' : '暂无匹配的已整理资料' }}</p>
          <p class="empty-hint">{{ favoritesOnly ? '在已整理资料的菜单或详情中添加收藏' : '可调整名称或标签条件；新资料需先在待整理中完成整理' }}</p>
        </template>
      </div>
    </div>

    <!-- 分页 -->
    <div class="pagination" v-if="total > pageSize">
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="handleSearch"
      />
    </div>

    <el-dialog v-model="folderMoveVisible" title="归入文件夹" width="600px" :close-on-click-modal="false" :close-on-press-escape="!folderMoveBusy" :show-close="!folderMoveBusy" :before-close="closeFolderMove">
      <template v-if="folderMoveDoc">
        <p class="folder-move-name">{{ folderMoveDoc.name }}</p>
        <p class="scope-hint">当前分类：{{ currentCategory || '未归入文件夹' }}</p>
        <p class="scope-hint">选择或新建分类文件夹。资料仍保留在全部资料中，标签、版本与收藏不变。</p>
        <div class="folder-move-picker" :class="{ 'is-busy': folderMoveBusy }" :inert="folderMoveBusy">
          <DirectoryTreeSelector :key="folderMoveDoc.id" v-model="folderMoveTarget" :exclude-path="folderMoveDoc.is_folder ? folderMoveDoc.storage_path : ''" root-label="资料库（请在其下选择或新建分类）" />
        </div>
      </template>
      <template #footer>
        <el-button :disabled="folderMoveBusy" @click="folderMoveVisible = false">取消</el-button>
        <el-button type="primary" :loading="folderMoveBusy" :disabled="!folderMoveTarget || folderMoveTarget === currentCategory" @click="submitFolderMove">确认归入</el-button>
      </template>
    </el-dialog>

    <!-- 编辑信息对话框 -->
    <el-dialog v-model="showEditDialog" title="编辑文档信息" width="480px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" placeholder="文档名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="3" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" @click="submitEdit" :loading="editLoading">保存</el-button>
      </template>
    </el-dialog>

    <!-- 编辑标签对话框 -->
    <el-dialog v-model="showTagDialog" title="编辑标签" width="480px">
      <el-form label-width="80px">
        <el-form-item label="标签">
          <el-select
            v-model="tagForm.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入标签"
            style="width: 100%"
          >
            <el-option
              v-for="tag in allTags"
              :key="tag.id"
              :label="tag.name"
              :value="tag.name"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showTagDialog = false">取消</el-button>
        <el-button type="primary" @click="submitTags" :loading="tagLoading">保存</el-button>
      </template>
    </el-dialog>

    <!-- 文档详情 Drawer -->
    <DocumentDetailDrawer
      v-model="showDetailDrawer"
      :document="selectedDoc"
      @updated="refreshNavigation"
      @deleted="refreshNavigation"
    />


    <el-dialog v-model="shareVisible" title="发布共享资料" width="600px" :close-on-click-modal="false" :before-close="closeShareDialog">
      <div v-if="shareOpening" v-loading="true" style="height:100px" />
      <template v-if="shareTarget">
        <p>资料：{{ shareTarget.name }}</p>
        <el-radio-group v-if="shareLinked" v-model="shareAction" :disabled="shareLocked">
          <el-radio value="update" label="update" :disabled="!shareOriginal?.can_contribute">更新原资料</el-radio>
          <el-radio value="new" label="new">另建资料</el-radio>
        </el-radio-group>
        <div v-if="shareAction === 'update' && shareOriginal" class="publication-destination" data-testid="linked-publication-target">
          <div class="destination-label">更新到原位置</div>
          <strong>{{ shareOriginal.team_name }} / {{ shareOriginal.workspace_name }}</strong>
          <p>原云端资料：{{ shareOriginal.shared_document_name }}</p>
        </div>
        <el-alert v-if="shareOriginalError" :title="shareOriginalError" type="warning" :closable="false" style="margin:12px 0" />
        <p v-if="shareOriginal && !shareOriginal.can_contribute" class="publication-help">你可以查看原资料，但没有更新权限。另建资料可选择其他发布位置。</p>
        <template v-if="shareAction !== 'update'">
          <el-form label-position="top" style="margin:16px 0">
            <el-form-item label="发布到">
              <el-select v-model="shareWorkspaceId" data-testid="publication-library-picker" aria-label="发布到" placeholder="选择共享资料库" :disabled="shareLocked || shareLibrariesLoading" :loading="shareLibrariesLoading" style="width:100%">
                <el-option v-for="library in shareLibraries" :key="library.id" :value="library.id" :label="publicationLibraryLabel(library)" />
              </el-select>
            </el-form-item>
          </el-form>
          <el-alert v-if="shareLibrariesError" :title="shareLibrariesError" type="error" :closable="false" />
          <el-empty v-else-if="!shareLibrariesLoading && !shareLibraries.length" description="暂无可访问的资料库，请联系管理员" :image-size="50" />
          <el-radio-group v-model="shareScope" :disabled="shareLocked || !shareWorkspaceId"><el-radio label="workspace">资料库全体成员</el-radio><el-radio label="restricted">指定成员</el-radio></el-radio-group>
          <el-select v-if="shareScope==='restricted'" v-model="shareRecipients" multiple filterable placeholder="选择可查看成员" :disabled="shareLocked || shareMembersLoading || !!shareMembersError" :loading="shareMembersLoading" style="width:100%;margin:12px 0"><el-option v-for="m in shareMembers" :key="m.id" :value="m.id" :label="m.display_name"/></el-select>
          <p v-if="shareScope==='restricted'">所选成员可查看；你可以通过云端网页明确授予协作或管理权限。</p>
          <el-alert v-if="shareMembersError" :title="shareMembersError" type="error" :closable="false" />
        </template>
        <p>首次发布随行标签：{{ shareTarget.tags?.map(t => t.name).join('、') || '无' }}</p>
        <p>本机资料：{{ shareTarget.file_count }} 个文件，{{ formatSize(shareTarget.total_size) }}</p>
        <p v-if="shareTarget.is_folder">文件夹整体发布，包含隐藏项和空目录，排除 .teamhub 管理目录。</p>
        <p>{{ shareAction === 'update' ? '将本机工作文件当前内容提交到原云端资料；已有成员标签不变。' : '将本机工作文件当前内容发布到所选资料库，共享后由组织保留。' }} 请先在编辑软件中保存修改。首次确认后，重试沿用当时的内容与发布位置。</p>
        <p>云端已有相同文件时不重复新增；更新原资料的内容未变时保留当前版本。</p>
        <TransferDuplicateNotice v-if="shareDuplicate" :result="shareDuplicate" @open="openExistingCloud" />
        <el-alert v-if="shareError" :title="shareError" type="error" :closable="false" />
        <p v-if="shareRequestId">本次共享的内容和位置已确定，重试将核对原结果。</p>
      </template>
      <template #footer>
        <el-button :disabled="!!pushingDocId" @click="shareVisible = false">关闭</el-button>
        <el-button v-if="!shareDuplicate" type="primary" :disabled="!shareCanConfirm" :loading="!!pushingDocId" @click="confirmShare">{{ shareRequestId ? '重试本次共享' : '确认共享' }}</el-button>
      </template>
    </el-dialog>

    </div><!-- end library-main -->
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  searchDocuments, deleteDocument,
  updateDocument, updateDocumentTags, relocateDocument,
  getDocument, removeFromFolder, moveToFolder
} from '@/api/document'
import { getTags } from '@/api/tag'
import { scanLibrary } from '@/api/settings'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Sort, Folder, Document, FolderOpened, MoreFilled, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook, WarningFilled, RefreshRight } from '@element-plus/icons-vue'
import { deliveryTransport } from '@/api/delivery'
import TransferDuplicateNotice from '@/components/TransferDuplicateNotice.vue'
import { contentDuplicate } from '@/utils/transferDedup'
import { publishDoc, submitDocVersion, getShareLink, getShareTarget } from '@/api/sync'
import { choosePublicationLibrary, restorePublicationIntent } from '@/utils/publicationSelection'
import { useVersionAwareness } from '@/composables/useVersionAwareness'
import DocumentDetailDrawer from '@/components/DocumentDetailDrawer.vue'
import { getLocalFavorites, addLocalFavorite, removeFavorite } from '@/api/favorite'
import LibraryTreePanel from '@/components/LibraryTreePanel.vue'
import DirectoryTreeSelector from '@/components/DirectoryTreeSelector.vue'
import SmartSearchBar from '@/components/SmartSearchBar.vue'
import SearchMatchExcerpt from '@/components/SearchMatchExcerpt.vue'
import { getFileTypeIcon, getFileTypeColor } from '@/utils/fileIcons'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const authStore = useAuthStore()
const { registerWindowEvents } = useVersionAwareness()
let _cleanupWindowEvents = null

const documents = ref([])
const loading = ref(false)
const searchError = ref('')
let searchSequence = 0
const total = ref(0)
const currentPage = ref(1)
const pageSize = computed(() => appStore.settings.items_per_page || 15)
const keyword = ref('')
const filterType = ref('')
const filterTags = ref([])          // 标签名数组，传给 searchDocuments
const searchBarRef = ref(null)      // SmartSearchBar 实例引用
const sortBy = ref(appStore.settings.default_sort_by || 'updated_at')
const sortOrder = ref(appStore.settings.default_sort_order || 'desc')
let browseSort = null
function restoreBrowseSort() {
  sortBy.value = browseSort?.field || appStore.settings.default_sort_by || 'updated_at'
  sortOrder.value = browseSort?.order || appStore.settings.default_sort_order || 'desc'
  browseSort = null
}
const selectedDoc = ref(null)

// 展示模式设置
const showTreeSidebar = computed(() => appStore.settings.library_show_tree_view ?? true)

// 目录树面板 ref（用于整理/移回后刷新目录树）
const treePanelRef = ref(null)
function refreshNavigation() {
  handleSearch()
  treePanelRef.value?.reload()
}

// 目录树导航
const selectedFolder = ref(null)
const folderMoveVisible = ref(false)
const folderMoveBusy = ref(false)
const folderMoveDoc = ref(null)
const folderMoveTarget = ref('')
const currentCategory = computed(() => (folderMoveDoc.value?.storage_path || '').replace(/\\/g, '/').split('/').slice(0, -1).join('/'))
function closeFolderMove(done) { if (!folderMoveBusy.value) done() }
async function submitFolderMove() {
  if (folderMoveBusy.value || !folderMoveDoc.value || !folderMoveTarget.value || folderMoveTarget.value === currentCategory.value) return
  folderMoveBusy.value = true
  try {
    await moveToFolder(folderMoveDoc.value.id, folderMoveTarget.value)
    folderMoveVisible.value = false
    ElMessage.success('已归入文件夹，仍保留在全部资料中')
    refreshNavigation()
  } catch (e) { ElMessage.error(e.message || '归入文件夹失败，原位置保持不变') }
  finally { folderMoveBusy.value = false }
}
const favoritesOnly = ref(false)
const favoriteIds = ref({})
const actionBusy = ref(false)
function selectFolder(path) { favoritesOnly.value = false; selectedFolder.value = path }
function selectFavorites() { selectedFolder.value = null; favoritesOnly.value = true }
function canRemoveFromFolder(doc) {
  return doc.status === 'organized' && doc.storage_mode !== 'index' && /[/\\]/.test(doc.storage_path || '')
}

// 面包屑路径段（仅当 selectedFolder 非 null 且非 '.' 时使用）
const folderSegments = computed(() => {
  if (!selectedFolder.value || selectedFolder.value === '.') return []
  return selectedFolder.value.replace(/\\/g, '/').split('/')
})

const selectFolderSegment = (idx) => {
  const segs = folderSegments.value.slice(0, idx + 1)
  selectedFolder.value = segs.join('/')
}

// 编辑信息
const showEditDialog = ref(false)
const editLoading = ref(false)
const editForm = ref({ name: '', description: '' })
const editDocId = ref(null)

// 编辑标签
const showTagDialog = ref(false)
const tagLoading = ref(false)
const tagForm = ref({ tags: [] })
const tagDocId = ref(null)
const allTags = ref([])

// 文档详情 Drawer
const showDetailDrawer = ref(false)

// 缺失文档
const missingDocs = ref([])
const scanLoading = ref(false)

// 云端推送状态（单文档）
const pushingDocId = ref(null)
const checkingDocId = ref(null)

const formatSize = (bytes) => formatFileSize(bytes)
const formatDate = (date) => formatDateTime(date)

const iconComponents = { Document, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook }
const fileIconComponent = (filename) => {
  const iconName = getFileTypeIcon(filename)
  return iconComponents[iconName] || Document
}

const toggleSortOrder = () => {
  sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
  handleSearch()
}

const handleSearch = async () => {
  const sequence = ++searchSequence
  loading.value = true
  searchError.value = ''
  try {
    const [data, favorites] = await Promise.all([searchDocuments({
      keyword: keyword.value || undefined,
      status: 'organized',
      favorites_only: favoritesOnly.value,
      tags: filterTags.value.length > 0 ? filterTags.value : undefined,
      is_folder: filterType.value === '' ? undefined : filterType.value,
      folder: showTreeSidebar.value && selectedFolder.value !== null ? selectedFolder.value : undefined,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
      page: currentPage.value,
      page_size: pageSize.value,
    }), getLocalFavorites()])
    if (sequence !== searchSequence) return
    documents.value = data.documents
    favoriteIds.value = Object.fromEntries(favorites.map(item => [item.local_document_id, item.id]))
    total.value = data.total
  } catch (e) {
    if (sequence === searchSequence) searchError.value = '搜索失败，当前列表可能是上次结果；筛选条件已保留，请重试'
  } finally {
    if (sequence === searchSequence) loading.value = false
  }
}

// SmartSearchBar 文件名搜索事件
const onNameSearch = (kw) => {
  if (kw?.trim()) {
    if (!keyword.value?.trim()) browseSort = { field: sortBy.value, order: sortOrder.value }
    sortBy.value = 'relevance'
    sortOrder.value = 'desc'
  } else if (browseSort || sortBy.value === 'relevance') restoreBrowseSort()
  keyword.value = kw
  filterTags.value = []
  currentPage.value = 1
  handleSearch()
}

// SmartSearchBar 标签搜索事件（tags 为 [{id, name, color}] 数组）
const onTagSearch = (tags) => {
  filterTags.value = tags.map(t => t.name)
  keyword.value = ''
  if (browseSort || sortBy.value === 'relevance') restoreBrowseSort()
  currentPage.value = 1
  handleSearch()
}

const handleOpen = (doc) => {
  if (!window.electron?.openPath) {
    ElMessage.info('此功能仅在 Electron 桌面应用中可用')
    return
  }

  let fullPath = doc.working_path || null
  if (fullPath) {
    // 后端解析的实际工作位置。
  } else if (doc.storage_path && appStore.libraryPath) {
    // 有存储路径，拼接文件库路径
    fullPath = `${appStore.libraryPath}\\${doc.storage_path}`
  } else if (doc.original_path) {
    // 索引模式：使用原始路径
    fullPath = doc.original_path
  }

  if (!fullPath) {
    ElMessage.warning('该文档没有可用的文件路径')
    return
  }

  window.electron.openPath(fullPath).then(errMsg => {
    if (errMsg) {
      ElMessage.error(`打开失败: ${errMsg}`)
    }
  })
}

const openDetail = async (doc) => {
  // 始终从 API 获取最新数据，确保路径等信息是最新的
  try {
    const latest = await getDocument(doc.id)
    selectedDoc.value = { ...latest }
  } catch {
    selectedDoc.value = { ...doc }
  }
  showDetailDrawer.value = true
}

// 云端状态辅助（根据 cloud_pushed_at 和 cloud_hash 与当前版本哈希比较）
const cloudDocStatus = (doc) => {
  if (!doc.cloud_pushed_at) return 'not_pushed'
  // 若当前版本哈希与推送时记录的哈希不同，说明本地已修改
  const curHash = doc.current_version?.sha256_hash
  if (curHash && doc.cloud_hash && curHash !== doc.cloud_hash) return 'modified'
  return 'pushed'
}

const handleCommand = async (cmd, doc) => {
  if (cmd === 'move-folder') {
    folderMoveDoc.value = doc
    folderMoveTarget.value = currentCategory.value
    folderMoveVisible.value = true
    return
  }
  if (cmd === 'favorite' || cmd === 'remove-folder') {
    if (actionBusy.value) return
    actionBusy.value = true
    try {
      if (cmd === 'favorite') {
        const id = favoriteIds.value[doc.id]
        if (id) await removeFavorite(id)
        else await addLocalFavorite(doc.id)
        ElMessage.success(id ? '已取消收藏，资料仍保留在全部资料中' : '已加入我的收藏')
      } else {
        await ElMessageBox.confirm('移出后，仍可在全部资料中找到。标签、收藏和版本保持不变。', '移出文件夹', { confirmButtonText: '移出', cancelButtonText: '取消' })
        await removeFromFolder(doc.id)
        ElMessage.success('已移出文件夹，仍可在全部资料中找到')
      }
      await handleSearch()
      treePanelRef.value?.reload()
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message || '操作失败')
    } finally { actionBusy.value = false }
    return
  }
  if (cmd === 'edit') {
    editDocId.value = doc.id
    editForm.value = { name: doc.name, description: doc.description || '' }
    showEditDialog.value = true
  } else if (cmd === 'tags') {
    tagDocId.value = doc.id
    tagForm.value = { tags: doc.tags.map(t => t.name) }
    try {
      allTags.value = await getTags()
    } catch (e) {
      allTags.value = []
    }
    showTagDialog.value = true
  } else if (cmd === 'versions') {
    openDetail(doc)
  } else if (cmd === 'push-to-cloud') {
    await handlePushDoc(doc)
  } else if (cmd === 'check-doc') {
    await handleCheckDoc(doc)
  }
}

const shareScope = ref('workspace'), shareRecipients = ref([]), shareMembers = ref([])
const shareVisible = ref(false), shareOpening = ref(false), shareTarget = ref(null)
const shareLinked = ref(false), shareAction = ref('new'), shareRequestId = ref('')
const shareError = ref(''), shareDuplicate = ref(null), shareOriginal = ref(null), shareOriginalError = ref('')
const shareLibraries = ref([]), shareWorkspaceId = ref(''), shareLibrariesLoading = ref(false), shareLibrariesError = ref('')
const shareMembersLoading = ref(false), shareMembersError = ref('')
const shareLocked = computed(() => !!shareRequestId.value || !!pushingDocId.value || !!shareDuplicate.value)
const shareDestinationId = computed(() => shareAction.value === 'update' ? shareOriginal.value?.workspace_id : shareWorkspaceId.value)
const shareCanConfirm = computed(() => !!shareTarget.value && !shareOpening.value && !shareLibrariesLoading.value &&
  (shareAction.value === 'update' ? !!shareOriginal.value?.can_contribute :
    !!shareWorkspaceId.value && shareLibraries.value.some(library => library.id === shareWorkspaceId.value) &&
    (!!shareRequestId.value || (!shareLibrariesError.value && !shareMembersLoading.value && !shareMembersError.value))))
const publicationLibraryLabel = library => `${library.team_name ? library.team_name + ' / ' : ''}${library.name}`
let shareSessionKey = '', sharePendingIntent = null
let shareEpoch = 0, shareOpenSequence = 0, shareMembersSequence = 0, shareDisposed = false
const shareIdentity = computed(() => JSON.stringify([authStore.isLoggedIn, authStore.user?.id,
  authStore.serverId || appStore.syncStatus?.server_id, appStore.settings?.cloud_api_url, appStore.libraryPath]))
const shareScopeIsCurrent = scope => !shareDisposed && scope.epoch === shareEpoch && scope.identity === shareIdentity.value
const shareScopeSnapshot = () => ({ epoch: shareEpoch, identity: shareIdentity.value })
async function openExistingCloud() {
  if (!shareDuplicate.value?.documentId || !shareDestinationId.value) return
  const workspaceId = shareDestinationId.value, documentId = shareDuplicate.value.documentId
  const scope = shareScopeSnapshot()
  if (authStore.currentWorkspace?.id !== workspaceId) {
    const library = authStore.allWorkspaces.find(item => item.id === workspaceId)
    if (!library || !await authStore.switchWorkspace(library)) {
      if (shareScopeIsCurrent(scope)) shareError.value = authStore.workspaceError || '无法进入目标资料库，请刷新后重试'
      return
    }
  }
  if (!shareScopeIsCurrent(scope)) return
  shareVisible.value = false
  router.push({ path: '/shared', query: { highlight: documentId, workspace: workspaceId } })
}
function finishDuplicate(value, scope, key) {
  const duplicate = contentDuplicate(value, 'cloud')
  if (!duplicate || !shareScopeIsCurrent(scope)) return false
  shareDuplicate.value = duplicate; shareError.value = ''
  sessionStorage.removeItem(key); shareRequestId.value = ''; sharePendingIntent = null
  return true
}
function clearPublication() {
  shareVisible.value = false; shareOpening.value = false; shareTarget.value = null
  shareOriginal.value = null; shareOriginalError.value = ''; shareLibraries.value = []; shareWorkspaceId.value = ''
  shareMembers.value = []; shareRecipients.value = []; shareLibrariesError.value = ''; shareMembersError.value = ''
  shareMembersLoading.value = false; shareLibrariesLoading.value = false
  shareDuplicate.value = null; shareError.value = ''; shareRequestId.value = ''; sharePendingIntent = null; pushingDocId.value = null
}
watch(shareIdentity, () => { shareEpoch++; shareOpenSequence++; shareMembersSequence++; clearPublication() }, { flush: 'sync' })
watch(shareVisible, visible => { if (!visible) { shareOpenSequence++; shareMembersSequence++ } }, { flush: 'sync' })
onUnmounted(() => { shareDisposed = true; shareEpoch++; shareOpenSequence++; shareMembersSequence++ })
const closeShareDialog = done => { if (!pushingDocId.value) done() }
async function loadPublicationMembers() {
  const sequence = ++shareMembersSequence, scope = shareScopeSnapshot(), workspaceId = shareWorkspaceId.value
  shareMembers.value = []; shareMembersError.value = ''; shareMembersLoading.value = false
  if (!shareVisible.value || shareAction.value === 'update' || !workspaceId) return
  const savedRecipients = sharePendingIntent?.workspaceId === workspaceId ? [...sharePendingIntent.recipients] : []
  shareRecipients.value = savedRecipients
  shareMembersLoading.value = true
  try {
    const members = await deliveryTransport('GET', `/api/workspaces/${encodeURIComponent(workspaceId)}/delivery-members`)
    if (!shareScopeIsCurrent(scope) || sequence !== shareMembersSequence || workspaceId !== shareWorkspaceId.value || !shareVisible.value) return
    if (!Array.isArray(members)) throw new Error('成员列表响应异常')
    shareMembers.value = members
    // Frozen retries ask the server for the original receipt without editing its access scope.
    for (const id of savedRecipients.filter(id => !members.some(member => member.id === id))) {
      shareMembers.value.push({ id, display_name: '原请求成员（已不在当前列表）' })
    }
  } catch (error) {
    if (shareScopeIsCurrent(scope) && sequence === shareMembersSequence) {
      shareMembers.value = savedRecipients.map(id => ({ id, display_name: '原请求成员' }))
      shareMembersError.value = shareRequestId.value ? '当前成员列表暂不可用；可重试核对原请求结果，共享范围保持原样。' : '无法确认所选资料库的成员权限，请重新选择或稍后重试。'
    }
  } finally { if (shareScopeIsCurrent(scope) && sequence === shareMembersSequence) shareMembersLoading.value = false }
}
watch([shareWorkspaceId, shareAction], () => { if (!shareOpening.value) loadPublicationMembers() }, { flush: 'sync' })
const handlePushDoc = async (doc) => {
  if (pushingDocId.value) return
  if (!authStore.isLoggedIn) { ElMessage.warning('请先登录云服务'); return }
  clearPublication()
  shareVisible.value = true; shareOpening.value = true; shareLibrariesLoading.value = true
  const scope = shareScopeSnapshot(), sequence = ++shareOpenSequence
  try {
    const [detail, link, refreshed] = await Promise.all([getDocument(doc.id), getShareLink(doc.id), authStore.refreshWorkspaces()])
    if (!shareScopeIsCurrent(scope) || sequence !== shareOpenSequence || !shareVisible.value) return
    shareTarget.value = detail; shareLinked.value = link.linked
    shareLibraries.value = [...(authStore.allWorkspaces || [])]
    if (refreshed === false || authStore.workspaceError) shareLibrariesError.value = '暂时无法读取可访问的资料库，请关闭后重试。'
    if (link.linked) {
      try {
        const original = await getShareTarget(doc.id)
        if (!shareScopeIsCurrent(scope) || sequence !== shareOpenSequence || !shareVisible.value) return
        shareOriginal.value = original
      } catch {
        if (!shareScopeIsCurrent(scope) || sequence !== shareOpenSequence) return
        shareOriginalError.value = '无法确认原云端资料的访问权限或账号、服务身份，不能更新原资料。可选择另建资料。'
      }
    }
    const preferredId = authStore.currentWorkspace?.id || ''
    shareWorkspaceId.value = choosePublicationLibrary(shareLibraries.value, preferredId)
    const server = authStore.serverId || appStore.syncStatus?.server_id || appStore.settings?.cloud_api_url || ''
    shareSessionKey = `teamhub-publication:${authStore.user?.id}:${server}:${appStore.libraryPath}:${doc.id}`
    let encoded = sessionStorage.getItem(shareSessionKey)
    // Previous releases scoped a dialog by the browsing library; migrate only a known current identity.
    if (!encoded && preferredId) {
      const legacyKey = `teamhub-publication:${authStore.user?.id}:${server}:${appStore.libraryPath}:${preferredId}:${doc.id}`
      try {
        const previous = JSON.parse(sessionStorage.getItem(legacyKey) || 'null')
        if (previous) {
          previous.workspaceId = previous.action === 'update' ? shareOriginal.value?.workspace_id : preferredId
          encoded = JSON.stringify(previous)
          if (restorePublicationIntent(encoded, shareLibraries.value, shareOriginal.value)) {
            sessionStorage.setItem(shareSessionKey, encoded); sessionStorage.removeItem(legacyKey)
          }
        }
      } catch { /* Invalid legacy state is not an executable request. */ }
    }
    const pending = restorePublicationIntent(encoded, shareLibraries.value, shareOriginal.value)
    sharePendingIntent = pending
    shareAction.value = pending?.action || (shareOriginal.value?.can_contribute ? 'update' : 'new')
    if (pending?.action === 'new') shareWorkspaceId.value = pending.workspaceId
    shareScope.value = pending?.visibility || 'workspace'
    shareRecipients.value = pending?.recipients || []
    shareRequestId.value = pending?.id || ''
    if (encoded && !pending) shareError.value = '此前请求的目标或权限已变化，未自动重试。请确认本次发布位置。'
  } catch (error) {
    if (shareScopeIsCurrent(scope) && sequence === shareOpenSequence) {
      shareError.value = error.message || '读取共享状态失败'; ElMessage.error(shareError.value)
    }
  } finally {
    if (shareScopeIsCurrent(scope) && sequence === shareOpenSequence) {
      shareOpening.value = false; shareLibrariesLoading.value = false
      await loadPublicationMembers()
    }
  }
}
const confirmShare = async () => {
  if (pushingDocId.value || !shareCanConfirm.value || shareDuplicate.value) return
  if (shareAction.value !== 'update' && shareScope.value === 'restricted' && !shareRecipients.value.length) { ElMessage.warning('请选择可查看成员'); return }
  const doc = shareTarget.value, scope = shareScopeSnapshot(), key = shareSessionKey
  const intent = sharePendingIntent || { id: crypto.randomUUID(), action: shareAction.value,
    workspaceId: shareDestinationId.value, forceNew: shareLinked.value, visibility: shareScope.value,
    recipients: [...shareRecipients.value], originalDocumentId: shareAction.value === 'update' ? shareOriginal.value.shared_document_id : null }
  if (intent.action !== shareAction.value || intent.workspaceId !== shareDestinationId.value) {
    shareError.value = '本次请求的发布位置已冻结，请沿用原位置重试。'; return
  }
  sharePendingIntent = intent; shareRequestId.value = intent.id
  sessionStorage.setItem(key, JSON.stringify(intent))
  pushingDocId.value = doc.id; shareError.value = ''
  try {
    let result
    if (intent.action === 'update') {
      try { result = await submitDocVersion(doc.id, null, { requestId: intent.id, confirmed: true, expectedWorkspaceId: intent.workspaceId, expectedDocumentId: intent.originalDocumentId || shareOriginal.value.shared_document_id }) }
      catch (error) {
        if (!shareScopeIsCurrent(scope)) return
        if (error.response?.data?.error?.code !== 'OLD_CONTENT_CONFIRMATION_REQUIRED' && !JSON.stringify(error.response?.data || {}).includes('OLD_CONTENT_CONFIRMATION_REQUIRED')) throw error
        try { await ElMessageBox.confirm('当前内容来自历史版本，提交后会成为云端新的当前版本。是否继续？', '重新发布旧内容', { confirmButtonText: '确认提交', cancelButtonText: '取消' }) } catch { return }
        if (!shareScopeIsCurrent(scope)) return
        result = await submitDocVersion(doc.id, null, { requestId: intent.id, confirmed: true, confirmOldContent: true, expectedWorkspaceId: intent.workspaceId, expectedDocumentId: intent.originalDocumentId || shareOriginal.value.shared_document_id })
      }
    } else {
      result = await publishDoc(doc.id, { workspaceId: intent.workspaceId, requestId: intent.id, confirmed: true,
        forceNew: intent.forceNew, visibility: intent.visibility, access: intent.recipients.map(user_id => ({ user_id, role: 'reader' })) })
    }
    if (!shareScopeIsCurrent(scope) || finishDuplicate(result, scope, key)) return
    sessionStorage.removeItem(key); shareRequestId.value = ''; sharePendingIntent = null; shareVisible.value = false
    if (result?.code === 'CONTENT_UNCHANGED') ElMessage.info('内容未变，保留云端当前版本')
    else ElMessage.success('共享成功')
    handleSearch(); appStore.fetchSyncStatus()
  } catch (error) {
    if (!shareScopeIsCurrent(scope) || finishDuplicate(error, scope, key)) return
    const data = error.response?.data
    shareError.value = data?.error?.message || data?.detail || error.message || '共享未完成，请重试'
    if ([400, 409, 422].includes(error.response?.status)) {
      sessionStorage.removeItem(key); shareRequestId.value = ''; sharePendingIntent = null
    }
  } finally { if (shareScopeIsCurrent(scope)) pushingDocId.value = null }
}

const handleCheckDoc = async (doc) => {
  checkingDocId.value = doc.id
  try {
    const res = await getShareLink(doc.id)
    if (!res.linked) {
      ElMessageBox.alert('该资料尚未发布到共享空间', '共享状态', {
        confirmButtonText: '关闭',
        type: 'info',
      })
    } else {
      const info = `已发布到共享空间\n共享资料 ID：${res.shared_document_id}\n最近同步：${formatDateTime(res.updated_at)}`
      ElMessageBox.alert(info, '共享状态', {
        confirmButtonText: '关闭',
        type: 'success',
      })
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '检查失败')
  } finally {
    checkingDocId.value = null
  }
}

const submitEdit = async () => {
  if (!editForm.value.name) {
    ElMessage.warning('名称不能为空')
    return
  }
  editLoading.value = true
  try {
    await updateDocument(editDocId.value, editForm.value)
    ElMessage.success('已更新')
    showEditDialog.value = false
    handleSearch()
  } catch (e) {
    ElMessage.error(e.message || '更新失败')
  } finally {
    editLoading.value = false
  }
}

const submitTags = async () => {
  tagLoading.value = true
  try {
    await updateDocumentTags(tagDocId.value, tagForm.value.tags)
    ElMessage.success('标签已更新')
    showTagDialog.value = false
    handleSearch()
  } catch (e) {
    ElMessage.error(e.message || '更新标签失败')
  } finally {
    tagLoading.value = false
  }
}

const fetchMissingDocs = async () => {
  try {
    const data = await searchDocuments({ status: 'missing', page: 1, page_size: 100 })
    missingDocs.value = data.documents.filter(d => {
      return d.storage_area !== 'pending'
    })
  } catch {
    missingDocs.value = []
  }
}

const handleScan = async () => {
  scanLoading.value = true
  try {
    const result = await scanLibrary()
    const parts = [`扫描完成：发现 ${result.missing} 个缺失`]
    if (result.relocated > 0) parts.push(`已重定位 ${result.relocated} 个`)
    if (result.restored > 0) parts.push(`已恢复 ${result.restored} 个`)
    ElMessage.success(parts.join('，'))
    fetchMissingDocs()
    handleSearch()
    treePanelRef.value?.reload()
  } catch {
    ElMessage.error('扫描失败')
  } finally {
    scanLoading.value = false
  }
}

const handleRelocate = async (doc) => {
  let newPath = null
  if (window.electron?.selectFile) {
    newPath = await window.electron.selectFile()
  } else {
    // 非 Electron 环境用 prompt
    try {
      const { value } = await ElMessageBox.prompt('请输入文件新路径', '重新定位', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      })
      newPath = value
    } catch { return }
  }
  if (!newPath) return
  try {
    await relocateDocument(doc.id, newPath)
    ElMessage.success('文件已重新定位')
    fetchMissingDocs()
    handleSearch()
  } catch (e) {
    ElMessage.error(e.message || '重新定位失败')
  }
}

const handleRemoveMissing = async (doc) => {
  try {
    await ElMessageBox.confirm(`确定永久移除"${doc.name}"？此操作不可恢复。`, '确认移除', { type: 'error' })
    await deleteDocument(doc.id, true)
    ElMessage.success('已移除')
    fetchMissingDocs()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(e.message || '移除失败')
  }
}

const loadAllTags = async () => {
  try {
    allTags.value = await getTags()
  } catch (e) {
    allTags.value = []
  }
}

// 切换目录时重置到第一页并重新搜索
watch([selectedFolder, favoritesOnly], () => {
  currentPage.value = 1
  handleSearch()
})

onUnmounted(() => _cleanupWindowEvents?.())

onMounted(async () => {
  try {
    // 支持从 URL query 参数 ?tag=xxx 跳转过来，切换到标签模式并预选标签
    if (route.query.tag) {
      searchBarRef.value?.setTagFilter(route.query.tag)
    }
    await loadAllTags()
    handleSearch()
    fetchMissingDocs()

    // 注册窗口激活自动检测
    _cleanupWindowEvents = registerWindowEvents({
      onLocalChanged: () => handleSearch(),
    })

    // 处理 ?highlight=docId（由查重"前往查看"跳转而来）
    const highlightId = route.query.highlight
    if (highlightId) {
      try {
        const doc = await getDocument(String(highlightId))
        if (doc) {
          selectedDoc.value = { ...doc }
          showDetailDrawer.value = true
        }
      } catch (e) {
        console.warn('[LibraryPage] highlight 文档获取失败:', e)
      }
    }
  } catch (e) {
    console.error('LibraryPage onMounted error:', e)
  }
})
</script>

<style scoped>
.folder-move-name { font-weight: 600; font-size: 16px; overflow-wrap: anywhere; }
.folder-move-picker { margin-top: 18px; }
.folder-move-picker.is-busy { pointer-events: none; opacity: .65; }
.scope-hint { margin: 6px 0 0; color: #909399; font-size: 13px; }
/* 整体左右分栏布局 */
.library-page {
  display: flex;
  height: 100%;
  gap: 0;
  overflow: hidden;
}

.tree-sidebar {
  width: 220px;
  min-width: 160px;
  max-width: 280px;
  border-right: 1px solid #ebeef5;
  flex-shrink: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.library-main {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 0 20px;
  min-width: 0;
}

.library-main.no-sidebar {
  padding: 0 24px;
}

/* 面包屑导航 */
.folder-breadcrumb {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
  padding: 6px 10px;
  background: #f0f2f5;
  border-radius: 6px;
  font-size: 13px;
}

.bc-link {
  cursor: pointer;
  color: var(--color-accent);
}

.bc-link:hover {
  text-decoration: underline;
}

/* 页头 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  margin-bottom: 12px;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: #2c3e50;
}

/* 搜索面板（卡片包裹） */
.search-panel {
  background: #ffffff;
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 12px;
  box-shadow: var(--shadow-sm);
  border: 1px solid #ebeef5;
}

.filter-bar {
  display: flex;
  gap: 12px;
  margin-top: 12px;
  align-items: center;
}

.document-list {
  min-height: 300px;
}

.doc-card {
  display: flex;
  align-items: center;
  padding: 9px 16px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 5px;
  cursor: pointer;
  transition: var(--transition-base);
  border: 1px solid #ebeef5;
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
  color: #2c3e50;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 12px;
  color: #aab2bd;
  white-space: nowrap;
  flex-shrink: 0;
  margin: 0 14px;
}

.meta-sep {
  margin: 0 5px;
}

.doc-tags {
  margin-top: 2px;
  display: flex;
  gap: 3px;
  flex-wrap: wrap;
}

.doc-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: 4px;
}

.empty-state {
  text-align: center;
  padding: 80px 20px;
  color: #95a5a6;
}

.empty-state p {
  margin-top: 16px;
  font-size: 16px;
}

.empty-hint {
  font-size: 14px !important;
  color: #bdc3c7 !important;
  margin-top: 8px !important;
}

.missing-list {
  margin-top: 8px;
}

.missing-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid #faecd8;
}

.missing-item:last-child {
  border-bottom: none;
}

.missing-name {
  font-weight: 500;
  color: #2c3e50;
  min-width: 120px;
}

.missing-path {
  flex: 1;
  font-size: 12px;
  color: #95a5a6;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.missing-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.pagination {
  margin-top: 24px;
  display: flex;
  justify-content: center;
}

/* 共享对话框 */
.publication-destination { background: #f1f7ff; border: 1px solid #dbeafe; border-radius: 10px; padding: 16px; margin: 16px 0; }
.publication-destination p { margin: 8px 0; }
.publication-destination small, .publication-help { color: #68788a; overflow-wrap: anywhere; }
.destination-label { color: #68788a; font-size: 12px; margin-bottom: 6px; }
.share-dialog-content {
  padding: 4px 0;
}
.share-doc-name {
  font-size: 15px;
  font-weight: 600;
  color: #1a2332;
  margin: 0 0 12px;
}
.share-hint {
  font-size: 13px;
  color: #606266;
  margin: 0 0 14px;
}
.share-visibility-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.share-visibility-group :deep(.el-radio.is-bordered) {
  width: 100%;
  height: auto;
  padding: 12px 16px;
  margin: 0;
  border-radius: 8px;
}
.visibility-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.visibility-label strong {
  font-size: 14px;
  color: #303133;
}
.visibility-label span {
  font-size: 12px;
  color: #909399;
  font-weight: normal;
}

.cloud-origin-badges {
  display: flex;
  gap: 5px;
  margin-top: 5px;
  flex-wrap: wrap;
}

.cloud-badge {
  padding: 0 6px;
  height: 18px;
  line-height: 18px;
  font-size: 11px;
  border-radius: 4px;
}
</style>
