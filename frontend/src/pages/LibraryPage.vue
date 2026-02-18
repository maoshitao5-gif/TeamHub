<template>
  <div class="library-page"
    @dragover.prevent
    @dragenter="handleDragEnter"
    @dragleave="handleDragLeave"
    @drop.prevent="handleDrop"
  >
    <!-- 搜索栏 -->
    <div class="search-bar">
      <el-input
        v-model="keyword"
        placeholder="搜索文档..."
        size="large"
        clearable
        @keyup.enter="handleSearch"
        @clear="handleSearch"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>

      <div class="filter-bar">
        <el-select
          v-model="filterTags"
          multiple
          filterable
          collapse-tags
          collapse-tags-tooltip
          placeholder="标签筛选"
          clearable
          size="default"
          @change="handleSearch"
          style="min-width: 160px;"
        >
          <el-option v-for="tag in allTags" :key="tag.id" :label="tag.name" :value="tag.name" />
        </el-select>

        <el-select v-model="filterType" placeholder="类型" clearable size="default" @change="handleSearch">
          <el-option label="全部" value="" />
          <el-option label="文件" :value="false" />
          <el-option label="文件夹" :value="true" />
        </el-select>

        <el-select v-model="sortBy" size="default" @change="handleSearch">
          <el-option label="最近更新" value="updated_at" />
          <el-option label="名称" value="name" />
          <el-option label="大小" value="total_size" />
          <el-option label="收纳时间" value="created_at" />
        </el-select>

        <el-button @click="toggleSortOrder" size="default">
          <el-icon><Sort /></el-icon>
          {{ sortOrder === 'desc' ? '降序' : '升序' }}
        </el-button>

        <el-button type="primary" @click="showAddDialog = true" size="default">
          <el-icon><Plus /></el-icon>
          收纳
        </el-button>
      </div>
    </div>

    <!-- 缺失文件警告 -->
    <el-alert
      v-if="missingDocs.length > 0"
      type="warning"
      :closable="false"
      style="margin-bottom: 16px;"
    >
      <template #title>
        <span>{{ missingDocs.length }} 个文档的文件缺失</span>
      </template>
      <div class="missing-list">
        <div v-for="doc in missingDocs" :key="doc.id" class="missing-item">
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
            <el-icon :size="32" :color="doc.is_folder ? '#e67e22' : getFileTypeColor(doc.name)">
              <Folder v-if="doc.is_folder" />
              <component v-else :is="fileIconComponent(doc.name)" />
            </el-icon>
          </div>
          <div class="doc-info">
            <div class="doc-name">{{ doc.name }}</div>
            <div class="doc-meta">
              <span v-if="doc.is_folder">{{ doc.file_count }} 个文件</span>
              <span v-else>{{ formatSize(doc.total_size) }}</span>
              <span class="meta-sep">·</span>
              <span>{{ formatDate(doc.updated_at) }}</span>
            </div>
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
                  <el-dropdown-item command="delete" divided>
                    <span style="color: #e74c3c">删除</span>
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
          <p class="empty-hint">拖入文件开始整理，或点击"收纳"按钮添加</p>
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

    <!-- 拖拽覆盖层 -->
    <div v-if="isDragging" class="drag-overlay">
      <div class="drag-hint">
        <el-icon :size="48"><Upload /></el-icon>
        <p>松开以收纳文件</p>
      </div>
    </div>

    <!-- 收纳对话框 -->
    <el-dialog v-model="showAddDialog" title="收纳文档" width="520px">
      <el-form label-width="80px">
        <el-form-item label="文件路径">
          <el-input v-model="addForm.source_path" placeholder="输入文件或文件夹的完整路径">
            <template #append>
              <el-button @click="selectSourceFile">浏览</el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="addForm.name" placeholder="文档名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="addForm.description" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
        <el-form-item label="存放到">
          <el-input v-model="addForm.target_dir" placeholder="文件库子目录，如：日常文档">
            <template #append>
              <el-button @click="selectTargetDir">浏览</el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="addForm.tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入标签"
            style="width: 100%"
          >
            <el-option v-for="tag in allTags" :key="tag.id" :label="tag.name" :value="tag.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="方式">
          <el-radio-group v-model="addForm.storage_mode">
            <el-radio value="move">移动</el-radio>
            <el-radio value="copy">复制</el-radio>
            <el-radio value="index">仅索引</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" @click="handleAdd" :loading="addLoading">收纳</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  searchDocuments, createDocument, deleteDocument,
  updateDocument, updateDocumentTags, relocateDocument,
  quickAddToPending
} from '@/api/document'
import { getTags } from '@/api/tag'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Search, Sort, Plus, Folder, Document, FolderOpened, MoreFilled, Upload, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook } from '@element-plus/icons-vue'
import DocumentDetailDrawer from '@/components/DocumentDetailDrawer.vue'
import { getFileTypeIcon, getFileTypeColor } from '@/utils/fileIcons'

const route = useRoute()
const appStore = useAppStore()
const documents = ref([])
const loading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20
const keyword = ref('')
const filterType = ref('')
const filterTags = ref([])
const sortBy = ref('updated_at')
const sortOrder = ref('desc')
const selectedDoc = ref(null)
const showAddDialog = ref(false)
const addLoading = ref(false)

// 拖拽状态
const isDragging = ref(false)
let dragCounter = 0

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

const addForm = ref({
  source_path: '',
  name: '',
  description: '',
  target_dir: '',
  tags: [],
  storage_mode: 'move',
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

const handleDragEnter = (e) => {
  dragCounter++
  isDragging.value = true
}

const handleDragLeave = (e) => {
  dragCounter--
  if (dragCounter <= 0) {
    dragCounter = 0
    isDragging.value = false
  }
}

const handleDrop = async (e) => {
  dragCounter = 0
  isDragging.value = false

  const files = e.dataTransfer?.files
  if (!files || files.length === 0) return

  const firstFile = files[0]
  if (!firstFile.path) {
    ElMessage.warning('拖拽上传仅在桌面应用中可用')
    return
  }

  if (files.length === 1) {
    // 单文件：打开收纳对话框
    const filePath = firstFile.path
    const parts = filePath.replace(/\\/g, '/').split('/')
    const fileName = parts[parts.length - 1]

    addForm.value = {
      source_path: filePath,
      name: fileName,
      description: '',
      target_dir: '',
      tags: [],
      storage_mode: 'move',
    }
    showAddDialog.value = true
  } else {
    // 多文件：全部 quick-add 到待整理区
    let successCount = 0
    let failCount = 0
    for (const file of files) {
      try {
        await quickAddToPending({ source_path: file.path, is_folder: false })
        successCount++
      } catch {
        failCount++
      }
    }
    if (successCount > 0) {
      ElMessage.success(`已添加 ${successCount} 个文件到待整理区`)
      appStore.fetchPendingCount()
    }
    if (failCount > 0) {
      ElMessage.warning(`${failCount} 个文件添加失败`)
    }
  }
}

const handleSearch = async () => {
  loading.value = true
  try {
    const data = await searchDocuments({
      keyword: keyword.value || undefined,
      status: 'organized',
      tags: filterTags.value.length > 0 ? filterTags.value : undefined,
      is_folder: filterType.value === '' ? undefined : filterType.value,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
      page: currentPage.value,
      page_size: pageSize,
    })
    documents.value = data.documents
    total.value = data.total
  } catch (e) {
    console.error('Search failed:', e)
  } finally {
    loading.value = false
  }
}

const selectTargetDir = async () => {
  if (window.electron?.selectDirectory) {
    const path = await window.electron.selectDirectory()
    if (path && appStore.libraryPath) {
      // 尝试获取相对于文件库的路径
      const libPath = appStore.libraryPath.replace(/\\/g, '/')
      const selectedPath = path.replace(/\\/g, '/')
      if (selectedPath.startsWith(libPath)) {
        addForm.value.target_dir = selectedPath.substring(libPath.length + 1)
      } else {
        addForm.value.target_dir = path
      }
    }
  } else {
    ElMessage.info('请手动输入路径（Electron 环境下可浏览选择）')
  }
}

const selectSourceFile = async () => {
  if (window.electron?.selectFile) {
    const path = await window.electron.selectFile()
    if (path) {
      addForm.value.source_path = path
      // 自动填充名称
      if (!addForm.value.name) {
        const parts = path.replace(/\\/g, '/').split('/')
        addForm.value.name = parts[parts.length - 1]
      }
    }
  } else {
    ElMessage.info('请手动输入路径（Electron 环境下可浏览选择）')
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

  console.log('[handleOpen] Opening:', fullPath)
  window.electron.openPath(fullPath).then(errMsg => {
    if (errMsg) {
      ElMessage.error(`打开失败: ${errMsg}`)
    }
  })
}

const openDetail = (doc) => {
  selectedDoc.value = doc
  showDetailDrawer.value = true
}

const handleCommand = async (cmd, doc) => {
  if (cmd === 'delete') {
    try {
      await ElMessageBox.confirm(`确定要删除"${doc.name}"吗？`, '确认删除', { type: 'warning' })
      await deleteDocument(doc.id)
      ElMessage.success('已移入回收站')
      handleSearch()
      appStore.fetchPendingCount()
    } catch (e) {
      if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
    }
  } else if (cmd === 'edit') {
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

const handleAdd = async () => {
  if (!addForm.value.source_path || !addForm.value.name) {
    ElMessage.warning('请填写文件路径和名称')
    return
  }
  addLoading.value = true
  try {
    await createDocument({
      source_path: addForm.value.source_path,
      name: addForm.value.name,
      description: addForm.value.description,
      target_dir: addForm.value.target_dir,
      tags: addForm.value.tags,
      storage_mode: addForm.value.storage_mode,
    })
    ElMessage.success('收纳成功')
    showAddDialog.value = false
    addForm.value = { source_path: '', name: '', description: '', target_dir: '', tags: [], storage_mode: 'move' }
    handleSearch()
  } catch (e) {
    ElMessage.error(e.message || '收纳失败')
  } finally {
    addLoading.value = false
  }
}

const fetchMissingDocs = async () => {
  try {
    const data = await searchDocuments({
      status: 'missing',
      page: 1,
      page_size: 50,
    })
    missingDocs.value = data.documents
  } catch {
    missingDocs.value = []
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
    if (e !== 'cancel') ElMessage.error(e.message || '移除失败')
  }
}

const loadAllTags = async () => {
  try {
    allTags.value = await getTags()
  } catch (e) {
    allTags.value = []
  }
}

onMounted(async () => {
  // 支持从 URL query 参数 ?tag=xxx 跳转过来
  if (route.query.tag) {
    filterTags.value = [route.query.tag]
  }
  await loadAllTags()
  handleSearch()
  fetchMissingDocs()
})
</script>

<style scoped>
.search-bar {
  margin-bottom: 24px;
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
  padding: 16px 20px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid #ebeef5;
}

.doc-card:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  border-color: #d0d7de;
}

.doc-icon {
  margin-right: 16px;
  flex-shrink: 0;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-size: 15px;
  font-weight: 500;
  color: #2c3e50;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  font-size: 13px;
  color: #95a5a6;
}

.meta-sep {
  margin: 0 6px;
}

.doc-tags {
  margin-top: 6px;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.doc-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: 12px;
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

.drag-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(52, 152, 219, 0.12);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.drag-hint {
  text-align: center;
  padding: 40px 60px;
  border: 3px dashed #3498db;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.95);
  color: #3498db;
}

.drag-hint p {
  margin-top: 12px;
  font-size: 18px;
  font-weight: 500;
}
</style>
