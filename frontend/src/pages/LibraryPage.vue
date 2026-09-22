<template>
  <div class="library-page">
    <!-- 左侧目录树面板 -->
    <div v-if="showTreeSidebar" class="tree-sidebar">
      <LibraryTreePanel ref="treePanelRef" v-model="selectedFolder" :showSpecialItems="showFlatView" />
    </div>

    <!-- 右侧主内容区 -->
    <div class="library-main" :class="{ 'no-sidebar': !showTreeSidebar }">

    <!-- 页头：标题 + 扫描按钮（始终可见） -->
    <div class="page-header">
      <div class="page-title">文档库</div>
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

    <!-- 目录路径面包屑 -->
    <div v-if="selectedFolder !== null" class="folder-breadcrumb">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>
          <span class="bc-link" @click="selectedFolder = null">全部文档</span>
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
                  <el-dropdown-item divided command="push-to-cloud" :disabled="doc.is_folder">
                    ↑ 推送到云端
                  </el-dropdown-item>
                  <el-dropdown-item command="check-doc" :disabled="!doc.cloud_pushed_at">
                    ⊙ 检查与云端差异
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
          <p>文档库为空</p>
          <p class="empty-hint">拖入文件即可添加到待整理</p>
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
      @updated="handleSearch"
      @deleted="handleSearch"
    />


    <!-- 整理对话框（用于文件夹） -->
    <el-dialog v-model="showOrganizeDialog" title="整理文件夹" width="520px" :append-to-body="true">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="organizeForm.name" placeholder="文档名称" />
        </el-form-item>
        <el-form-item label="存放到" required>
          <DirectoryTreeSelector
            v-model="organizeForm.target_dir"
            :excludePath="currentOrganizeDoc?.storage_path || ''"
          />
        </el-form-item>
        <el-form-item label="标签" required>
          <el-select
            ref="organizeTagSelectRef"
            v-model="organizeForm.tags"
            multiple
            filterable
            allow-create
            :filter-method="handleOrganizeTagFilter"
            :reserve-keyword="true"
            placeholder="选择或输入标签（必填）"
            style="width: 100%"
            @change="handleOrganizeTagChange"
            @visible-change="handleOrganizeTagVisibleChange"
            @keydown.enter="handleOrganizeTagEnter"
          >
            <el-option v-for="tag in filteredOrganizeTags" :key="tag.id" :label="tag.name" :value="tag.name" />
          </el-select>
          <div class="form-item-hint">至少需要添加一个标签</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showOrganizeDialog = false">取消</el-button>
        <el-button type="primary" @click="submitOrganize" :loading="organizeLoading">整理</el-button>
      </template>
    </el-dialog>

    <DuplicateWarningDialog
      v-model="showDupDialog"
      :existing-doc="dupExistingDoc"
      @confirm="dupConfirmCallback && dupConfirmCallback()"
      @cancel="dupCancelCallback && dupCancelCallback()"
      @navigate="onDupNavigate"
    />

    </div><!-- end library-main -->
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  searchDocuments, deleteDocument,
  updateDocument, updateDocumentTags, relocateDocument,
  getDocument, organizeDocument, checkDocLibraryDuplicate
} from '@/api/document'
import { getTags } from '@/api/tag'
import { scanLibrary } from '@/api/settings'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Sort, Folder, Document, FolderOpened, MoreFilled, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook, WarningFilled, RefreshRight } from '@element-plus/icons-vue'
import { pushDoc, checkDoc } from '@/api/sync'
import { useVersionAwareness } from '@/composables/useVersionAwareness'
import DocumentDetailDrawer from '@/components/DocumentDetailDrawer.vue'
import DirectoryTreeSelector from '@/components/DirectoryTreeSelector.vue'
import LibraryTreePanel from '@/components/LibraryTreePanel.vue'
import SmartSearchBar from '@/components/SmartSearchBar.vue'
import DuplicateWarningDialog from '@/components/DuplicateWarningDialog.vue'
import { getFileTypeIcon, getFileTypeColor } from '@/utils/fileIcons'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const { registerWindowEvents } = useVersionAwareness()
let _cleanupWindowEvents = null

// ===== 查重对话框状态 =====
const showDupDialog = ref(false)
const dupExistingDoc = ref({})
const dupConfirmCallback = ref(null)
const dupCancelCallback = ref(null)

function showDuplicateWarning(existingDoc) {
  return new Promise((resolve) => {
    dupExistingDoc.value = existingDoc
    dupConfirmCallback.value = () => resolve(true)
    dupCancelCallback.value = () => resolve(false)
    showDupDialog.value = true
  })
}

const onDupNavigate = (doc) => {
  if (doc.status === 'pending') {
    router.push('/pending')
  } else {
    router.push('/library')
  }
}

const documents = ref([])
const loading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = computed(() => appStore.settings.items_per_page || 15)
const keyword = ref('')
const filterType = ref('')
const filterTags = ref([])          // 标签名数组，传给 searchDocuments
const searchBarRef = ref(null)      // SmartSearchBar 实例引用
const sortBy = ref(appStore.settings.default_sort_by || 'updated_at')
const sortOrder = ref(appStore.settings.default_sort_order || 'desc')
const selectedDoc = ref(null)

// 展示模式设置
const showTreeSidebar = computed(() => appStore.settings.library_show_tree_view ?? true)
const showFlatView = computed(() => appStore.settings.library_show_flat_view ?? true)

// 目录树面板 ref（用于整理/移回后刷新目录树）
const treePanelRef = ref(null)

// 目录树导航
const selectedFolder = ref(null)

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

// 计算匹配的标签（用于整理对话框）
const filteredOrganizeTags = computed(() => {
  try {
    if (!Array.isArray(allTags.value)) {
      return []
    }
    const currentTags = organizeForm.value?.tags || []
    if (!Array.isArray(currentTags)) {
      return []
    }
    if (!organizeTagKeyword.value) {
      return allTags.value.filter(tag => tag && tag.name && !currentTags.includes(tag.name))
    }
    const keyword = organizeTagKeyword.value.toLowerCase()
    return allTags.value.filter(tag => {
      if (!tag || !tag.name) return false
      const tagName = tag.name.toLowerCase()
      return tagName.includes(keyword) && !currentTags.includes(tag.name)
    })
  } catch (e) {
    console.error('filteredOrganizeTags error:', e)
    return []
  }
})

// 文档详情 Drawer
const showDetailDrawer = ref(false)

// 缺失文档
const missingDocs = ref([])
const scanLoading = ref(false)

// 云端推送状态（单文档）
const pushingDocId = ref(null)
const checkingDocId = ref(null)

// 整理对话框（用于文件夹）
const showOrganizeDialog = ref(false)
const organizeLoading = ref(false)
const currentDocId = ref(null)
const currentOrganizeDoc = ref(null)  // 当前正在整理的文档（用于排除自身路径）
const organizeTagSelectRef = ref(null)

// 标签输入关键字状态
const organizeTagKeyword = ref('')

const organizeForm = ref({
  name: '',
  target_dir: '',
  tags: [],
})

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
  loading.value = true
  try {
    const data = await searchDocuments({
      keyword: keyword.value || undefined,
      status: 'organized',
      tags: filterTags.value.length > 0 ? filterTags.value : undefined,
      is_folder: filterType.value === '' ? undefined : filterType.value,
      folder: showTreeSidebar.value && selectedFolder.value !== null ? selectedFolder.value : undefined,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
      page: currentPage.value,
      page_size: pageSize.value,
    })
    documents.value = data.documents
    total.value = data.total
  } catch (e) {
    console.error('Search failed:', e)
  } finally {
    loading.value = false
  }
}

// SmartSearchBar 文件名搜索事件
const onNameSearch = (kw) => {
  keyword.value = kw
  filterTags.value = []
  currentPage.value = 1
  handleSearch()
}

// SmartSearchBar 标签搜索事件（tags 为 [{id, name, color}] 数组）
const onTagSearch = (tags) => {
  filterTags.value = tags.map(t => t.name)
  keyword.value = ''
  currentPage.value = 1
  handleSearch()
}

// 处理整理标签过滤
const handleOrganizeTagFilter = (val) => {
  organizeTagKeyword.value = val || ''
}

// 处理整理标签下拉框显示变化
const handleOrganizeTagVisibleChange = (visible) => {
  if (visible) {
    const keyword = organizeTagKeyword.value.trim()
    // 如果有关键字且没有匹配的标签，立即隐藏下拉框
    if (keyword && filteredOrganizeTags.value.length === 0) {
      // 使用 nextTick 确保在下拉框完全显示后再隐藏
      nextTick(() => {
        if (organizeTagSelectRef.value) {
          try {
            organizeTagSelectRef.value.blur()
          } catch (e) {
            console.error('Error hiding dropdown:', e)
          }
        }
      })
    }
  }
}

// 处理整理标签回车事件
const handleOrganizeTagEnter = async (event) => {
  const keyword = organizeTagKeyword.value.trim()
  if (!keyword) return
  
  // 阻止默认行为
  event.preventDefault()
  event.stopPropagation()
  
  // 检查是否有匹配的标签
  const matchedTags = filteredOrganizeTags.value
  if (matchedTags.length > 0) {
    // 有匹配标签，选中第一个
    const firstTag = matchedTags[0].name
    if (!organizeForm.value?.tags?.includes(firstTag)) {
      if (!organizeForm.value) return
      if (!organizeForm.value.tags) {
        organizeForm.value.tags = []
      }
      organizeForm.value.tags.push(firstTag)
      organizeTagKeyword.value = ''
      await nextTick()
      handleOrganizeTagChange()
    }
  } else {
    // 没有匹配标签，创建新标签
    if (!organizeForm.value?.tags?.includes(keyword)) {
      if (!organizeForm.value) return
      if (!organizeForm.value.tags) {
        organizeForm.value.tags = []
      }
      organizeForm.value.tags.push(keyword)
      organizeTagKeyword.value = ''
      await nextTick()
      handleOrganizeTagChange()
    }
  }
}

// 处理整理标签输入变化，清空输入框
const handleOrganizeTagChange = async () => {
  await nextTick()
  if (organizeTagSelectRef.value) {
    // 清空 el-select 的输入框
    const input = organizeTagSelectRef.value.$el?.querySelector('input')
    if (input) {
      input.value = ''
      organizeTagKeyword.value = ''
      // 触发 input 事件以确保组件状态更新
      input.dispatchEvent(new Event('input', { bubbles: true }))
    }
  }
}

const submitOrganize = async () => {
  // 验证标签必填
  if (!organizeForm.value.tags || organizeForm.value.tags.length === 0) {
    ElMessage.warning('请至少添加一个标签')
    return
  }

  // target_dir 为空字符串表示放在文件库根目录，null 表示未选择
  if (organizeForm.value.target_dir === null || organizeForm.value.target_dir === undefined) {
    ElMessage.warning('请在目录树中选择存放位置')
    return
  }

  organizeLoading.value = true
  try {
    await organizeDocument(currentDocId.value, {
      target_dir: organizeForm.value.target_dir,
      tags: organizeForm.value.tags,
      name: organizeForm.value.name,
    })
    ElMessage.success('整理完成')
    showOrganizeDialog.value = false
    organizeTagKeyword.value = ''

    // 刷新标签列表，确保新创建的标签能够在下拉列表中显示
    await loadAllTags()

    handleSearch()
    treePanelRef.value?.reload()  // 刷新目录树（新目录可能出现）
    appStore.fetchPendingCount()
  } catch (e) {
    ElMessage.error(e.message || '整理失败')
  } finally {
    organizeLoading.value = false
  }
}

const handleOpen = (doc) => {
  if (!window.electron?.openPath) {
    ElMessage.info('此功能仅在 Electron 桌面应用中可用')
    return
  }

  let fullPath = null
  if (doc.storage_path && appStore.libraryPath) {
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

const handlePushDoc = async (doc) => {
  if (!appStore.syncStatus?.workspace_id) {
    ElMessage.warning('请先在设置页面绑定云端工作空间')
    return
  }
  pushingDocId.value = doc.id
  try {
    const res = await pushDoc(doc.id)
    ElMessage.success(res.message || '已推送到云端')
    handleSearch()
    appStore.fetchSyncStatus()
  } catch (e) {
    if (e.response?.status === 409) {
      ElMessage.info(e.response.data?.detail || '文件内容未变化，无需重复推送')
    } else {
      ElMessage.error(e.response?.data?.detail || e.message || '推送失败')
    }
  } finally {
    pushingDocId.value = null
  }
}

const handleCheckDoc = async (doc) => {
  checkingDocId.value = doc.id
  try {
    const res = await checkDoc(doc.id)
    const statusMap = {
      synced: '✓ 本地与云端一致',
      modified: '● 本地已修改，可以推送到云端',
      not_pushed: '该文档从未推送到云端',
      unknown: '无法获取云端或本地哈希',
    }
    const msg = statusMap[res.status] || res.message
    const versionInfo = res.cloud_version_count != null ? `\n云端历史版本数：${res.cloud_version_count}` : ''
    ElMessageBox.alert(`${msg}${versionInfo}`, '检查结果', {
      confirmButtonText: '关闭',
      type: res.status === 'synced' ? 'success' : (res.status === 'modified' ? 'warning' : 'info'),
    })
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
      const p = d.storage_path || ''
      return !p.startsWith('待整理/') && !p.startsWith('待整理\\')
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

// 监听对话框打开，重置关键字
watch(showOrganizeDialog, (newVal) => {
  if (newVal) {
    organizeTagKeyword.value = ''
  }
})

// 切换目录时重置到第一页并重新搜索
watch(selectedFolder, () => {
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

.form-item-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}

/* 共享对话框 */
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
