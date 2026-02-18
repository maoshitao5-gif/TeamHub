<template>
  <div class="pending-page"
    @dragover.prevent
    @dragenter="handleDragEnter"
    @dragleave="handleDragLeave"
    @drop.prevent="handleDrop"
  >
    <div class="page-header">
      <h2>待整理 <span v-if="total > 0" class="count-badge">{{ total }}</span></h2>
      <div class="header-actions">
        <el-button size="default" @click="handleAnalyze" :loading="analyzeLoading" :disabled="total === 0">
          <el-icon><MagicStick /></el-icon>
          智能扫描
        </el-button>
      </div>
    </div>

    <!-- 智能扫描结果 -->
    <div v-if="showAnalysisResult" class="analysis-result">
      <div class="analysis-header">
        <h3>扫描结果</h3>
        <el-button text @click="showAnalysisResult = false">关闭</el-button>
      </div>

      <!-- 相似文件分组 -->
      <div v-if="analysisData.version_groups.length > 0" class="analysis-section">
        <h4>相似文件（可能是不同版本）</h4>
        <div v-for="(group, idx) in analysisData.version_groups" :key="idx" class="analysis-group-card">
          <div class="group-header">
            <span class="group-name">{{ group.base_name }}</span>
            <span class="group-count">{{ group.documents.length }} 个文件</span>
          </div>
          <div class="group-files">
            <div v-for="d in group.documents" :key="d.id" class="group-file-item">
              <span>{{ d.name }}</span>
              <span class="group-file-meta">{{ formatFileSize(d.total_size) }}</span>
            </div>
          </div>
          <el-button size="small" type="primary" @click="mergeGroup(group)">合并为版本</el-button>
        </div>
      </div>

      <!-- 标签建议 -->
      <div v-if="analysisData.tag_suggestions.length > 0" class="analysis-section">
        <h4>标签建议</h4>
        <div v-for="(suggestion, idx) in analysisData.tag_suggestions" :key="idx" class="analysis-tag-card">
          <div class="tag-suggestion-info">
            <el-tag size="default" effect="plain">{{ suggestion.tag }}</el-tag>
            <span class="tag-reason">{{ suggestion.reason }}</span>
          </div>
          <el-button size="small" @click="applyTagSuggestion(suggestion)">应用</el-button>
        </div>
      </div>

      <div v-if="analysisData.version_groups.length === 0 && analysisData.tag_suggestions.length === 0" class="empty-hint">
        未发现相似文件或可建议的标签
      </div>
    </div>

    <!-- 文件列表 -->
    <div class="pending-list" v-loading="loading">
      <template v-if="documents.length > 0">
        <div
          v-for="doc in documents"
          :key="doc.id"
          class="pending-card"
          @click="openDetail(doc)"
        >
          <el-checkbox v-model="doc._selected" class="card-checkbox" />
          <div class="doc-icon">
            <el-icon :size="28" :color="doc.is_folder ? '#e67e22' : getFileTypeColor(doc.name)">
              <Folder v-if="doc.is_folder" />
              <component v-else :is="fileIconComponent(doc.name)" />
            </el-icon>
          </div>
          <div class="doc-info">
            <div class="doc-name">{{ doc.name }}</div>
            <div class="doc-meta">
              <span v-if="doc.is_folder">{{ doc.file_count }} 个文件</span>
              <span v-else>{{ formatFileSize(doc.total_size) }}</span>
              <span class="meta-sep">·</span>
              <span>{{ formatDateTime(doc.created_at) }}</span>
            </div>
          </div>
          <div class="doc-actions">
            <el-button size="small" type="primary" @click="organizeOne(doc)">整理</el-button>
            <el-button size="small" text @click="handleDelete(doc)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <el-icon :size="64" color="#bdc3c7"><CircleCheck /></el-icon>
        <p>没有待整理的文件</p>
      </div>
    </div>

    <!-- 拖拽覆盖层 -->
    <div v-if="isDragging" class="drag-overlay">
      <div class="drag-hint">
        <el-icon :size="48"><Upload /></el-icon>
        <p>松开以添加到待整理</p>
      </div>
    </div>

    <!-- 批量操作栏 -->
    <div class="batch-bar" v-if="selectedIds.length > 0">
      <span>已选 {{ selectedIds.length }} 项</span>
      <el-button size="small" @click="batchOrganize">批量整理</el-button>
      <el-button size="small" @click="batchTag">批量打标签</el-button>
      <el-button size="small" @click="openMergeDialog" :disabled="selectedIds.length < 2">合并为版本</el-button>
    </div>

    <!-- 批量整理对话框 -->
    <el-dialog v-model="showBatchOrganizeDialog" title="批量整理" width="480px">
      <el-form label-width="80px">
        <el-form-item label="存放到">
          <el-input v-model="batchOrganizeForm.target_dir" placeholder="文件库子目录">
            <template #append>
              <el-button @click="selectTargetDir('batch')">浏览</el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="batchOrganizeForm.tags"
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
      </el-form>
      <template #footer>
        <el-button @click="showBatchOrganizeDialog = false">取消</el-button>
        <el-button type="primary" @click="submitBatchOrganize" :loading="batchOrganizeLoading">
          整理 {{ selectedIds.length }} 个文档
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量打标签对话框 -->
    <el-dialog v-model="showBatchTagDialog" title="批量打标签" width="480px">
      <el-form label-width="100px">
        <el-form-item label="添加标签">
          <el-select
            v-model="batchTagForm.add_tags"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择要添加的标签"
            style="width: 100%"
          >
            <el-option v-for="tag in allTags" :key="tag.id" :label="tag.name" :value="tag.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="移除标签">
          <el-select
            v-model="batchTagForm.remove_tags"
            multiple
            filterable
            placeholder="选择要移除的标签"
            style="width: 100%"
          >
            <el-option v-for="tag in allTags" :key="tag.id" :label="tag.name" :value="tag.name" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showBatchTagDialog = false">取消</el-button>
        <el-button type="primary" @click="submitBatchTag" :loading="batchTagLoading">
          应用到 {{ selectedIds.length }} 个文档
        </el-button>
      </template>
    </el-dialog>

    <!-- 文档详情 Drawer -->
    <DocumentDetailDrawer
      v-model="showDetailDrawer"
      :document="detailDoc"
      @updated="fetchPending"
      @deleted="() => { fetchPending(); appStore.fetchPendingCount() }"
    />

    <!-- 合并为版本对话框 -->
    <el-dialog v-model="showMergeDialog" title="合并为版本" width="520px">
      <p style="margin-bottom: 12px; color: #7f8c8d; font-size: 13px;">
        将选中的 {{ mergeSelectedDocs.length }} 个文档合并为同一个文档的多个版本。最新的文件将作为当前版本。
      </p>
      <div class="merge-doc-list">
        <div v-for="d in mergeSelectedDocs" :key="d.id" class="merge-doc-item">
          <span class="merge-doc-name">{{ d.name }}</span>
          <span class="merge-doc-meta">{{ formatFileSize(d.total_size) }} · {{ formatDateTime(d.created_at) }}</span>
        </div>
      </div>
      <el-form label-width="80px" style="margin-top: 16px;">
        <el-form-item label="合并名称">
          <el-input v-model="mergeForm.name" placeholder="合并后的文档名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="mergeForm.description" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMergeDialog = false">取消</el-button>
        <el-button type="primary" @click="submitMerge" :loading="mergeLoading">
          合并 {{ mergeSelectedDocs.length }} 个文档
        </el-button>
      </template>
    </el-dialog>

    <!-- 整理对话框 -->
    <el-dialog v-model="showOrganizeDialog" title="整理文档" width="480px">
      <el-form label-width="80px">
        <el-form-item label="文档名称">
          <el-input v-model="organizeForm.name" />
        </el-form-item>
        <el-form-item label="存放到">
          <el-input v-model="organizeForm.target_dir" placeholder="文件库子目录">
            <template #append>
              <el-button @click="selectTargetDir('single')">浏览</el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="organizeForm.tags"
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
      </el-form>
      <template #footer>
        <el-button @click="showOrganizeDialog = false">取消</el-button>
        <el-button type="primary" @click="submitOrganize" :loading="organizeLoading">完成整理</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { searchDocuments, organizeDocument, deleteDocument, batchOrganizeDocuments, batchUpdateTags, quickAddToPending, mergeDocumentsAsVersions, analyzePending } from '@/api/document'
import { getTags } from '@/api/tag'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Folder, Document, Delete, CircleCheck, Upload, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook, MagicStick } from '@element-plus/icons-vue'
import DocumentDetailDrawer from '@/components/DocumentDetailDrawer.vue'
import { getFileTypeIcon, getFileTypeColor } from '@/utils/fileIcons'

const appStore = useAppStore()

const iconComponents = { Document, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook }
const fileIconComponent = (filename) => {
  const iconName = getFileTypeIcon(filename)
  return iconComponents[iconName] || Document
}

const documents = ref([])
const loading = ref(false)
const total = ref(0)
const showOrganizeDialog = ref(false)
const organizeLoading = ref(false)
const currentDocId = ref(null)

const allTags = ref([])

// 文档详情 Drawer
const showDetailDrawer = ref(false)
const detailDoc = ref(null)
const openDetail = (doc) => {
  detailDoc.value = doc
  showDetailDrawer.value = true
}

// 拖拽状态
const isDragging = ref(false)
let dragCounter = 0

const organizeForm = ref({
  name: '',
  target_dir: '',
  tags: [],
})

const selectedIds = computed(() =>
  documents.value.filter(d => d._selected).map(d => d.id)
)

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

  // 检查是否在 Electron 环境
  if (!files[0].path) {
    ElMessage.warning('拖拽上传仅在桌面应用中可用')
    return
  }

  let successCount = 0
  let failCount = 0

  for (const file of files) {
    try {
      await quickAddToPending({
        source_path: file.path,
        is_folder: false,
      })
      successCount++
    } catch (err) {
      failCount++
      console.error('快速添加失败:', file.path, err)
    }
  }

  if (successCount > 0) {
    ElMessage.success(`已添加 ${successCount} 个文件到待整理`)
    fetchPending()
    appStore.fetchPendingCount()
  }
  if (failCount > 0) {
    ElMessage.warning(`${failCount} 个文件添加失败`)
  }
}

const fetchPending = async () => {
  loading.value = true
  try {
    const data = await searchDocuments({
      status: 'pending',
      sort_by: 'created_at',
      sort_order: 'desc',
      page: 1,
      page_size: 100,
    })
    documents.value = data.documents.map(d => ({ ...d, _selected: false }))
    total.value = data.total
  } catch (e) {
    console.error('Fetch pending failed:', e)
  } finally {
    loading.value = false
  }
}

const organizeOne = async (doc) => {
  currentDocId.value = doc.id
  organizeForm.value = { name: doc.name, target_dir: '', tags: [] }
  await loadAllTags()
  showOrganizeDialog.value = true
}

const submitOrganize = async () => {
  if (!organizeForm.value.target_dir) {
    ElMessage.warning('请指定存放目录')
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
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    ElMessage.error(e.message || '整理失败')
  } finally {
    organizeLoading.value = false
  }
}

const handleDelete = async (doc) => {
  try {
    await ElMessageBox.confirm(`确定删除"${doc.name}"？`, '确认', { type: 'warning' })
    await deleteDocument(doc.id, true)
    ElMessage.success('已删除')
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

const selectTargetDir = async (mode) => {
  if (window.electron?.selectDirectory) {
    const path = await window.electron.selectDirectory()
    if (path && appStore.libraryPath) {
      const libPath = appStore.libraryPath.replace(/\\/g, '/')
      const selectedPath = path.replace(/\\/g, '/')
      const relPath = selectedPath.startsWith(libPath)
        ? selectedPath.substring(libPath.length + 1)
        : path
      if (mode === 'batch') {
        batchOrganizeForm.value.target_dir = relPath
      } else {
        organizeForm.value.target_dir = relPath
      }
    }
  } else {
    ElMessage.info('请手动输入路径（Electron 环境下可浏览选择）')
  }
}

const loadAllTags = async () => {
  try {
    allTags.value = await getTags()
  } catch (e) {
    allTags.value = []
  }
}

// ========== 智能分析 ==========
const analyzeLoading = ref(false)
const showAnalysisResult = ref(false)
const analysisData = ref({ version_groups: [], tag_suggestions: [] })

const handleAnalyze = async () => {
  analyzeLoading.value = true
  try {
    const data = await analyzePending()
    analysisData.value = data
    showAnalysisResult.value = true
  } catch (e) {
    ElMessage.error(e.message || '分析失败')
  } finally {
    analyzeLoading.value = false
  }
}

const mergeGroup = (group) => {
  // 复用合并对话框
  mergeSelectedDocs.value = group.documents
  mergeForm.value.name = group.suggested_name
  mergeForm.value.description = ''
  showMergeDialog.value = true
}

const applyTagSuggestion = async (suggestion) => {
  try {
    await batchUpdateTags({
      document_ids: suggestion.document_ids,
      add_tags: [suggestion.tag],
      remove_tags: [],
    })
    ElMessage.success(`已为 ${suggestion.document_ids.length} 个文档添加标签"${suggestion.tag}"`)
    fetchPending()
  } catch (e) {
    ElMessage.error(e.message || '应用标签失败')
  }
}

// ========== 合并为版本 ==========
const showMergeDialog = ref(false)
const mergeLoading = ref(false)
const mergeForm = ref({ name: '', description: '' })
const mergeSelectedDocs = ref([])

const openMergeDialog = () => {
  const selected = documents.value.filter(d => d._selected)
  if (selected.length < 2) {
    ElMessage.warning('请至少选择 2 个文档')
    return
  }
  mergeSelectedDocs.value = selected
  // 默认名称取第一个文档的名称（去掉扩展名）
  const firstName = selected[0].name
  const dotIndex = firstName.lastIndexOf('.')
  mergeForm.value.name = dotIndex > 0 ? firstName.substring(0, dotIndex) : firstName
  mergeForm.value.description = ''
  showMergeDialog.value = true
}

const submitMerge = async () => {
  if (!mergeForm.value.name) {
    ElMessage.warning('请输入合并名称')
    return
  }
  mergeLoading.value = true
  try {
    await mergeDocumentsAsVersions({
      document_ids: mergeSelectedDocs.value.map(d => d.id),
      name: mergeForm.value.name,
      description: mergeForm.value.description || undefined,
    })
    ElMessage.success('合并成功')
    showMergeDialog.value = false
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    ElMessage.error(e.message || '合并失败')
  } finally {
    mergeLoading.value = false
  }
}

// ========== 批量整理 ==========
const showBatchOrganizeDialog = ref(false)
const batchOrganizeLoading = ref(false)
const batchOrganizeForm = ref({ target_dir: '', tags: [] })

const batchOrganize = async () => {
  batchOrganizeForm.value = { target_dir: '', tags: [] }
  await loadAllTags()
  showBatchOrganizeDialog.value = true
}

const submitBatchOrganize = async () => {
  if (!batchOrganizeForm.value.target_dir) {
    ElMessage.warning('请指定存放目录')
    return
  }
  batchOrganizeLoading.value = true
  try {
    await batchOrganizeDocuments({
      document_ids: selectedIds.value,
      target_dir: batchOrganizeForm.value.target_dir,
      tags: batchOrganizeForm.value.tags,
    })
    ElMessage.success('批量整理完成')
    showBatchOrganizeDialog.value = false
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    ElMessage.error(e.message || '批量整理失败')
  } finally {
    batchOrganizeLoading.value = false
  }
}

// ========== 批量打标签 ==========
const showBatchTagDialog = ref(false)
const batchTagLoading = ref(false)
const batchTagForm = ref({ add_tags: [], remove_tags: [] })

const batchTag = async () => {
  batchTagForm.value = { add_tags: [], remove_tags: [] }
  await loadAllTags()
  showBatchTagDialog.value = true
}

const submitBatchTag = async () => {
  if (batchTagForm.value.add_tags.length === 0 && batchTagForm.value.remove_tags.length === 0) {
    ElMessage.warning('请选择要添加或移除的标签')
    return
  }
  batchTagLoading.value = true
  try {
    await batchUpdateTags({
      document_ids: selectedIds.value,
      add_tags: batchTagForm.value.add_tags,
      remove_tags: batchTagForm.value.remove_tags,
    })
    ElMessage.success('批量打标签完成')
    showBatchTagDialog.value = false
    fetchPending()
  } catch (e) {
    ElMessage.error(e.message || '批量打标签失败')
  } finally {
    batchTagLoading.value = false
  }
}

onMounted(() => {
  fetchPending()
})
</script>

<style scoped>
.page-header {
  margin-bottom: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-header h2 {
  font-size: 22px;
  color: #2c3e50;
}

.count-badge {
  background: #e74c3c;
  color: #fff;
  font-size: 14px;
  padding: 2px 10px;
  border-radius: 12px;
  margin-left: 8px;
  font-weight: 500;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.analysis-result {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px 20px;
  margin-bottom: 20px;
}

.analysis-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.analysis-header h3 {
  font-size: 16px;
  color: #2c3e50;
  margin: 0;
}

.analysis-section {
  margin-bottom: 16px;
}

.analysis-section h4 {
  font-size: 14px;
  color: #7f8c8d;
  margin: 0 0 10px 0;
}

.analysis-group-card {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 12px;
  margin-bottom: 8px;
}

.group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.group-name {
  font-weight: 500;
  color: #2c3e50;
}

.group-count {
  font-size: 12px;
  color: #95a5a6;
}

.group-files {
  margin-bottom: 8px;
}

.group-file-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;
  color: #606266;
}

.group-file-meta {
  color: #bdc3c7;
  font-size: 12px;
}

.analysis-tag-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 6px;
}

.tag-suggestion-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tag-reason {
  font-size: 13px;
  color: #95a5a6;
}

.empty-hint {
  text-align: center;
  color: #bdc3c7;
  font-size: 13px;
  padding: 16px;
}

.pending-list {
  min-height: 300px;
}

.pending-card {
  display: flex;
  align-items: center;
  padding: 14px 20px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 8px;
  border: 1px solid #ebeef5;
}

.card-checkbox {
  margin-right: 12px;
}

.doc-icon {
  margin-right: 14px;
}

.doc-info {
  flex: 1;
  min-width: 0;
}

.doc-name {
  font-size: 15px;
  font-weight: 500;
  color: #2c3e50;
}

.doc-meta {
  font-size: 13px;
  color: #95a5a6;
  margin-top: 2px;
}

.meta-sep {
  margin: 0 6px;
}

.doc-actions {
  display: flex;
  gap: 8px;
  margin-left: 12px;
}

.batch-bar {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  background: #2c3e50;
  color: #fff;
  padding: 12px 24px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
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

.merge-doc-list {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.merge-doc-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #f0f2f5;
}

.merge-doc-item:last-child {
  border-bottom: none;
}

.merge-doc-name {
  font-size: 14px;
  color: #2c3e50;
}

.merge-doc-meta {
  font-size: 12px;
  color: #bdc3c7;
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
