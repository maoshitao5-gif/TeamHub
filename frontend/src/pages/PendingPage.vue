<template>
  <div class="pending-page">
    <div class="page-header">
      <div class="page-heading">
        <h2>待整理 <span v-if="total > 0" class="count-badge">{{ total }}</span></h2>
        <p>先收录，再整理。AI 可以准备方案，你确认后进入文档库。</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" :loading="importing" @click="openImportDialog">收录本机文件</el-button>
        <el-button
          v-if="documents.length > 0"
          size="default"
          text
          :disabled="loading"
          @click="handleSelectAll"
        >{{ allSelected ? '取消全选' : '全选本页' }}</el-button>
        <el-button size="default" @click="handleAnalyze" :loading="analyzeLoading" :disabled="appStore.pendingCount === 0" title="扫描全部待整理资料，不受搜索条件影响">
          <el-icon><MagicStick /></el-icon>
          智能扫描
        </el-button>
      </div>
    </div>

    <PreorganizePanel ref="preorganizePanel" :selected-documents="selectedDocuments" :counts="preorganizeCounts"
      :filter="preorganizeFilter" :tags="allTags" @update:filter="changePreorganizeFilter" @refresh="refreshAfterPreorganize" />

    <form class="pending-search" role="search" @submit.prevent="submitPendingSearch">
      <el-input v-model="searchInput" clearable :prefix-icon="Search"
        aria-label="搜索待整理资料" placeholder="搜索名称、描述、已索引正文或 AI 线索"
        @clear="clearPendingSearch" />
      <el-button type="primary" native-type="submit" :loading="loading">搜索</el-button>
    </form>
    <div v-if="activeKeyword" class="pending-search-summary">
      <span>{{ loading ? '正在查找…' : `找到 ${total} 项待整理资料` }} · “{{ activeKeyword }}”</span>
      <el-button link type="primary" @click="clearPendingSearch">清除搜索</el-button>
    </div>
    <el-alert v-if="pendingSearchError" :title="pendingSearchError" type="error" :closable="false" class="pending-search-error">
      <el-button link type="primary" @click="fetchPending">重新加载</el-button>
    </el-alert>

    <el-dialog v-model="showImportDialog" title="收录本机文件" width="560px"
      :close-on-click-modal="false" :close-on-press-escape="!importing && !pickingSource"
      :show-close="!importing && !pickingSource">
      <div class="import-source-actions" v-if="hasNativePicker">
        <el-button :disabled="importing || pickingSource" @click="pickImportSource('file')">
          <el-icon><Document /></el-icon>选择文件
        </el-button>
        <el-button :disabled="importing || pickingSource" @click="pickImportSource('folder')">
          <el-icon><Folder /></el-icon>选择文件夹
        </el-button>
      </div>
      <p v-if="importSourcePath && hasNativePicker" class="import-selected-name">{{ importSourcePath.split(/[/\\]/).filter(Boolean).at(-1) }}</p>
      <el-form label-position="top" :disabled="importing || pickingSource" @submit.prevent="importLocalFile">
        <el-form-item :label="hasNativePicker ? '已选路径' : '本机路径'">
          <el-input v-model="importSourcePath" :readonly="hasNativePicker" :title="importSourcePath"
            :placeholder="hasNativePicker ? '点击上方按钮选择文件或文件夹' : '请输入本机文件或文件夹的完整路径'" />
        </el-form-item>
      </el-form>
      <p v-if="!hasNativePicker" class="import-hint">浏览器无法取得本机完整路径；桌面端支持直接选择文件或文件夹。</p>
      <div class="import-notice">
        <p v-if="importing" role="status">正在校验并收录，文件夹较大时需要一些时间，请勿重复操作。</p>
        <p>收录后将移动到「待整理」，原位置不再保留。可在待整理中撤销，移回原位置。</p>
        <p>选择文件夹时，整个文件夹作为一份资料收录。</p>
      </div>
      <template #footer>
        <el-button :disabled="importing || pickingSource" @click="showImportDialog = false">取消</el-button>
        <el-button type="primary" :loading="importing" :disabled="!importSourcePath.trim() || pickingSource" @click="importLocalFile">{{ importing ? '正在收录…' : '移动到待整理' }}</el-button>
      </template>
    </el-dialog>

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
          <p class="group-file-meta">仅按名称提示相似资料，请分别核对内容；不会自动合并版本。</p>
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

    <!-- 缺失文件警告 -->
    <el-alert v-if="missingPendingDocs.length > 0" type="warning" :closable="false" style="margin-bottom: 16px;">
      <template #title>
        <div style="display:flex; align-items:center; justify-content:space-between; width:100%;">
          <span>{{ missingPendingDocs.length }} 个待整理文件已缺失（文件可能被移动或删除）</span>
          <el-button size="small" :loading="scanLoading" @click="handleScan" style="margin-left:12px;">重新扫描</el-button>
        </div>
      </template>
      <div class="missing-list">
        <div v-for="doc in missingPendingDocs" :key="doc.id" class="missing-item">
          <el-icon style="color:#e6a23c; flex-shrink:0;"><WarningFilled /></el-icon>
          <span class="missing-name">{{ doc.name }}</span>
          <span class="missing-path">{{ doc.storage_path }}</span>
          <div class="missing-actions">
            <el-button size="small" type="danger" text @click="handleRemoveMissingPending(doc)">移除</el-button>
          </div>
        </div>
      </div>
    </el-alert>

    <!-- 文件列表 -->
    <div class="pending-list" v-loading="loading">
      <template v-if="documents.length > 0">
        <div
          v-for="doc in documents"
          :key="doc.id"
          class="pending-card"
          @click="openDetail(doc)"
        >
          <el-checkbox v-model="doc._selected" class="card-checkbox" @click.stop />
          <div class="doc-icon">
            <el-icon :size="22" :color="doc.is_folder ? '#e67e22' : getFileTypeColor(doc.name)">
              <Folder v-if="doc.is_folder" />
              <component v-else :is="fileIconComponent(doc.name)" />
            </el-icon>
          </div>
          <div class="doc-info">
            <div class="doc-name">{{ doc.name }}</div>
            <div class="doc-preorganize" :data-testid="`preorganize-status-${doc.id}`">
              <el-tag size="small" :type="preorganizeState(doc.preorganize?.state).type" effect="light">{{ preorganizeState(doc.preorganize?.state).label }}</el-tag>
              <span v-if="doc.preorganize?.phase === 'queued'">已排队，轮到后自动处理</span>
              <span v-if="doc.preorganize?.error" class="doc-preorganize-error" :title="doc.preorganize.error">{{ doc.preorganize.error }}</span>
              <span v-else-if="doc.preorganize?.gaps?.length">有 {{ doc.preorganize.gaps.length }} 项内容需留意</span>
            </div>
            <div v-if="doc.is_folder || doc.tags?.length" class="doc-context">
              <span v-if="doc.is_folder" class="folder-context">成套资料 · 保留目录结构</span>
              <el-tag v-for="tag in doc.tags" :key="tag.id" size="small" effect="plain">{{ tag.name }}</el-tag>
            </div>
          </div>
          <div class="doc-meta">
            <span v-if="doc.is_folder">{{ doc.file_count }} 个文件</span>
            <span v-else>{{ formatFileSize(doc.total_size) }}</span>
            <span class="meta-sep">·</span>
            <span>{{ formatDateTime(doc.created_at) }}</span>
          </div>
          <div class="doc-actions">
            <el-button size="small" text @click.stop="handleOpen(doc)">打开</el-button>
            <el-button v-if="['copy', 'move'].includes(doc.storage_mode)" size="small" text @click.stop="handleUndo(doc)">撤销收录</el-button>
            <el-button v-if="doc.preorganize?.state === 'ready'" size="small" type="primary" :data-testid="`preorganize-review-${doc.id}`" @click.stop="preorganizePanel?.review([doc])">确认方案</el-button>
            <el-button v-else-if="doc.preorganize?.state === 'processing'" size="small" :data-testid="`preorganize-cancel-${doc.id}`" @click.stop="preorganizePanel?.cancel([doc])">取消预整理</el-button>
            <el-button v-else size="small" :data-testid="`preorganize-start-${doc.id}`" @click.stop="preorganizePanel?.start([doc])">{{ doc.preorganize?.state === 'failed' ? '重试 AI' : 'AI 预整理' }}</el-button>
            <el-button size="small" text :data-testid="`manual-organize-${doc.id}`" @click.stop="organizeOne(doc)">手工整理</el-button>
          </div>
        </div>
      </template>

      <div v-else-if="!loading && !pendingSearchError" class="empty-state">
        <el-icon :size="64" color="#bdc3c7"><Search v-if="activeKeyword || preorganizeFilter" /><CircleCheck v-else /></el-icon>
        <p>{{ activeKeyword || preorganizeFilter ? '没有符合当前条件的待整理资料' : '没有待整理的文件' }}</p>
        <el-button v-if="preorganizeFilter" text type="primary" @click="changePreorganizeFilter('')">查看全部状态</el-button>
        <p v-if="activeKeyword" class="empty-search-hint">试试更短的关键词；尚未提取的内容暂不可搜索，也可清除搜索查看全部资料。</p>
      </div>
    </div>

    <!-- 分页 -->
    <div class="pagination" v-if="total > pageSize">
      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="fetchPending"
      />
    </div>

    <!-- 批量操作栏 -->
    <div class="batch-bar" v-if="selectedIds.length > 0">
      <span>已选 {{ selectedIds.length }} 项</span>
      <el-button size="small" @click="batchOrganize">批量整理</el-button>
      <el-button size="small" @click="batchTag">批量打标签</el-button>
      <el-button size="small" type="danger" @click="batchDelete">删除</el-button>
    </div>

    <!-- 单个与批量使用相同的标签、可选文件夹控件 -->
    <el-dialog v-model="showBatchOrganizeDialog" title="批量整理" width="560px"
      class="organize-dialog" top="6vh" destroy-on-close :close-on-click-modal="false"
      :close-on-press-escape="!batchOrganizeLoading" :show-close="!batchOrganizeLoading">
      <div class="organize-summary">
        <el-icon class="summary-icon"><CollectionTag /></el-icon>
        <div><strong>{{ batchOrganizeSnapshot.length }} 项资料</strong><p>添加共同标签，已有标签会保留。文件夹资料仍作为整体整理。</p>
          <p class="batch-snapshot-names" :title="batchOrganizeSnapshot.map(doc => doc.name).join('、')">{{ batchOrganizeSnapshot.map(doc => doc.name).join('、') }}</p>
        </div>
      </div>
      <el-form label-position="top" class="organize-form" :disabled="batchOrganizeLoading" @submit.prevent="submitBatchOrganize">
        <el-form-item label="查找标签" required>
          <OrganizeTags v-model="batchOrganizeForm.tags" :tags="allTags" />
        </el-form-item>
        <OrganizeLocation v-model="batchOrganizeForm.target_dir" />
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button :disabled="batchOrganizeLoading" @click="showBatchOrganizeDialog = false">取消</el-button>
          <el-button type="primary" @click="submitBatchOrganize" :loading="batchOrganizeLoading">
            完成 {{ batchOrganizeSnapshot.length }} 项整理
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 批量打标签对话框 -->
    <el-dialog v-model="showBatchTagDialog" title="批量打标签" width="480px" :close-on-click-modal="false"
      :close-on-press-escape="!batchTagLoading" :show-close="!batchTagLoading">
      <div class="organize-summary"><div><strong>{{ batchTagSnapshot.length }} 项资料</strong>
        <p class="batch-snapshot-names" :title="batchTagSnapshot.map(doc => doc.name).join('、')">{{ batchTagSnapshot.map(doc => doc.name).join('、') }}</p>
      </div></div>
      <el-form label-width="100px" :disabled="batchTagLoading">
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
        <el-button :disabled="batchTagLoading" @click="showBatchTagDialog = false">取消</el-button>
        <el-button type="primary" @click="submitBatchTag" :loading="batchTagLoading">
          应用到 {{ batchTagSnapshot.length }} 个文档
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

    <!-- 整理：标签是主要步骤，目录为可选安排 -->
    <el-dialog v-model="showOrganizeDialog" title="整理资料" width="560px"
      class="organize-dialog" top="6vh" destroy-on-close :close-on-click-modal="false"
      :close-on-press-escape="!organizeLoading" :show-close="!organizeLoading">
      <div class="organize-summary">
        <el-icon class="summary-icon"><Folder v-if="currentDoc?.is_folder" /><Document v-else /></el-icon>
        <div>
          <strong>{{ currentDoc?.name }}</strong>
          <p>{{ currentDoc?.is_folder ? '整个文件夹作为一项资料，内部文件和目录结构保持完整。' : '加上你熟悉的查找线索，即可完成整理。' }}</p>
        </div>
      </div>
      <div v-if="currentDoc && !currentDoc.is_folder" class="organize-ai-action">
        <el-button size="small" :disabled="organizeLoading" @click="showAIAssist = true" data-testid="pending-ai-assist">AI 辅助整理</el-button>
        <span>按需推荐标签，确认后填入表单</span>
      </div>
      <el-form label-position="top" class="organize-form" :disabled="organizeLoading" @submit.prevent="submitOrganize">
        <el-form-item label="查找标签" required>
          <OrganizeTags v-model="organizeForm.tags" :tags="allTags" />
        </el-form-item>
        <el-form-item label="资料名称">
          <el-input v-model="organizeForm.name"
            :placeholder="currentDoc?.is_folder ? '文件夹名称' : '文件名称（不含扩展名）'" />
          <div v-if="currentDoc && !currentDoc.is_folder" class="form-item-hint">可选修改，文件扩展名会保留</div>
        </el-form-item>
        <OrganizeLocation v-model="organizeForm.target_dir" />
      </el-form>
      <DocumentUnderstandingCard v-if="currentDoc" :visible="showOrganizeDialog" :document="currentDoc" />
      <DocumentIndexCard :visible="showOrganizeDialog" :document="currentDoc" />
      <template #footer>
        <div class="dialog-footer">
          <el-button :disabled="organizeLoading" @click="showOrganizeDialog = false">取消</el-button>
          <el-button type="primary" @click="submitOrganize" :loading="organizeLoading">
            <el-icon v-if="!organizeLoading"><Check /></el-icon>完成整理
          </el-button>
        </div>
      </template>
    </el-dialog>

    <AIAssistDialog v-model="showAIAssist" :document="currentDoc" apply-label="填入整理表单" @apply="applyAITags" />

    <!-- 查重警告对话框 -->
    <DuplicateWarningDialog
      v-model="showDupDialog"
      :existing-doc="dupExistingDoc"
      @confirm="dupConfirmCallback && dupConfirmCallback()"
      @cancel="dupCancelCallback && dupCancelCallback()"
      @navigate="onDupNavigate"
    />

  </div>
</template>

<script setup>
import { quickAddToPending } from '@/api/document'
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { searchDocuments, organizeDocument, deleteDocument, batchOrganizeDocuments, batchUpdateTags, analyzePending, getDocument, batchDeleteDocuments, undoPending, checkDocLibraryDuplicate } from '@/api/document'
import { getTags } from '@/api/tag'
import { scanLibrary } from '@/api/settings'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Folder, Document, CollectionTag, CircleCheck, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook, MagicStick, Check, WarningFilled, Search } from '@element-plus/icons-vue'
import DocumentDetailDrawer from '@/components/DocumentDetailDrawer.vue'
import OrganizeTags from '@/components/OrganizeTags.vue'
import OrganizeLocation from '@/components/OrganizeLocation.vue'
import DuplicateWarningDialog from '@/components/DuplicateWarningDialog.vue'
import AIAssistDialog from '@/components/AIAssistDialog.vue'
import DocumentIndexCard from '@/components/DocumentIndexCard.vue'
import DocumentUnderstandingCard from '@/components/DocumentUnderstandingCard.vue'
import PreorganizePanel from '@/components/PreorganizePanel.vue'
import { listPreorganizeDocuments } from '@/api/preorganize'
import { mergePendingSelection, preorganizeState, splitOrganizationName } from '@/utils/preorganize'
import { appendSelectedTags } from '@/utils/aiAssist'
import { getFileTypeIcon, getFileTypeColor } from '@/utils/fileIcons'

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()

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

const iconComponents = { Document, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook }
const fileIconComponent = (filename) => {
  const iconName = getFileTypeIcon(filename)
  return iconComponents[iconName] || Document
}

const documents = ref([])
const loading = ref(false)
const total = ref(0)
const currentPage = ref(1)
const searchInput = ref('')
const activeKeyword = ref('')
const pendingSearchError = ref('')
const preorganizePanel = ref(null)
const preorganizeFilter = ref('')
const preorganizeCounts = ref({})
let pendingSearchSequence = 0
let pageGeneration = 0, pendingController, pendingTimer, disposed = false
const changePreorganizeFilter = value => {
  preorganizeFilter.value = value
  currentPage.value = 1
  documents.value = []
  fetchPending()
}
const refreshAfterPreorganize = () => {
  fetchPending()
  loadAllTags()
  appStore.fetchPendingCount()
}
const submitPendingSearch = () => {
  activeKeyword.value = searchInput.value.trim()
  currentPage.value = 1
  documents.value = []
  fetchPending()
}
const clearPendingSearch = () => {
  searchInput.value = ''
  submitPendingSearch()
}
const pageSize = computed(() => appStore.settings.items_per_page || 15)
const showOrganizeDialog = ref(false)
const organizeLoading = ref(false)
const showAIAssist = ref(false)
watch(showOrganizeDialog, visible => { if (!visible) showAIAssist.value = false })
function applyAITags(value) {
  if (!showOrganizeDialog.value || currentDocId.value !== value.documentId || appStore.libraryPath !== value.libraryPath) return
  organizeForm.value.tags = appendSelectedTags(organizeForm.value.tags, value.candidates, value.selected)
  ElMessage.success('所选标签已填入，请确认后完成整理')
}
const currentDocId = ref(null)
// 编辑对象使用打开时快照，避免后台轮询或翻页使表单突然消失。
const currentDoc = ref(null)

const allTags = ref([])

// 缺失的待整理文档
const missingPendingDocs = ref([])
const scanLoading = ref(false)

// 文档详情 Drawer
const showDetailDrawer = ref(false)
const detailDoc = ref(null)

const openDetail = async (doc) => {
  const generation = pageGeneration
  // 始终从 API 获取最新数据
  try {
    const latest = await getDocument(doc.id)
    if (generation !== pageGeneration || disposed) return
    detailDoc.value = { ...latest }
  } catch {
    if (generation !== pageGeneration || disposed) return
    detailDoc.value = { ...doc }
  }
  showDetailDrawer.value = true
}

const handleOpen = (doc) => {
  if (!window.electron?.openPath) {
    ElMessage.info('此功能仅在 Electron 桌面应用中可用')
    return
  }

  let fullPath = doc.working_path || null
  if (fullPath) {
    // 后端根据资料归属解析位置。
  } else if (doc.storage_path && appStore.libraryPath) {
    const separator = appStore.libraryPath.includes('\\') ? '\\' : '/'
    fullPath = `${appStore.libraryPath.replace(/[/\\]+$/, '')}${separator}${doc.storage_path.replace(/[/\\]/g, separator)}`
  } else if (doc.original_path) {
    // 索引模式使用原始路径
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

const importing = ref(false)
const showImportDialog = ref(false)
const importSourcePath = ref('')
const pickingSource = ref(false)
const hasNativePicker = Boolean(window.electron?.selectFile && window.electron?.selectDirectory)
const openImportDialog = () => {
  if (importing.value) return
  importSourcePath.value = ''
  showImportDialog.value = true
}
const pickImportSource = async (kind) => {
  if (pickingSource.value || importing.value) return
  pickingSource.value = true
  try {
    const path = kind === 'folder'
      ? await window.electron.selectDirectory()
      : await window.electron.selectFile()
    if (path) importSourcePath.value = path
  } catch (error) {
    ElMessage.error(error.message || '无法打开路径选择窗口，请重试')
  } finally { pickingSource.value = false }
}
const importLocalFile = async () => {
  if (importing.value || pickingSource.value || !importSourcePath.value.trim()) return
  importing.value = true
  try {
    const result = await quickAddToPending({ source_path: hasNativePicker ? importSourcePath.value : importSourcePath.value.trim() })
    showImportDialog.value = false
    if (result.error?.code === 'DUPLICATE_CONTENT') {
      ElMessage.info(`内容已存在于「${result.document.name}」的 v${result.version.version_number}，原文件保留`)
      openDetail(result.document)
    } else {
      ElMessage.success('已移动到待整理')
      await fetchPending()
      appStore.fetchPendingCount()
      openDetail(result)
    }
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '收录失败')
  } finally { importing.value = false }
}

const handleUndo = async (doc) => {
  try {
    await undoPending(doc.id)
    ElMessage.success('已撤销收录')
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    ElMessage.error(e.message || '撤销失败（原始位置可能已有同名文件）')
  }
}

const organizeForm = ref({
  name: '',
  target_dir: '',
  tags: [],
})

const selectedIds = computed(() =>
  documents.value.filter(d => d._selected).map(d => d.id)
)
const selectedDocuments = computed(() => documents.value.filter(doc => doc._selected))

const allSelected = computed(() =>
  documents.value.length > 0 && documents.value.every(d => d._selected)
)

const handleSelectAll = () => {
  const shouldSelect = !allSelected.value
  documents.value.forEach(d => { d._selected = shouldSelect })
}

// 当待整理数量变化时（App.vue 添加了文件），刷新列表
watch(() => appStore.pendingCount, () => {
  fetchPending()
})

const fetchPending = async (background = false) => {
  if (disposed) return
  const sequence = ++pendingSearchSequence
  pendingController?.abort()
  pendingController = new AbortController()
  clearTimeout(pendingTimer)
  if (background !== true) loading.value = true
  try {
    const data = await listPreorganizeDocuments({
      search: activeKeyword.value || undefined,
      state: preorganizeFilter.value || undefined,
      page: currentPage.value,
      page_size: pageSize.value,
    }, pendingController.signal)
    if (sequence !== pendingSearchSequence) return
    pendingSearchError.value = ''
    total.value = data.total
    preorganizeCounts.value = data.counts || {}
    const lastPage = Math.max(1, Math.ceil(data.total / pageSize.value))
    if (currentPage.value > lastPage) {
      currentPage.value = lastPage
      return fetchPending()
    }
    documents.value = mergePendingSelection(documents.value, data.items)
  } catch (e) {
    if (sequence !== pendingSearchSequence) return
    pendingSearchError.value = e.message || '待整理资料加载失败，请重试'
  } finally {
    if (sequence === pendingSearchSequence) {
      loading.value = false
      pendingTimer = setTimeout(() => fetchPending(true), 4000)
    }
  }
}

const organizeOne = async (doc) => {
  const generation = pageGeneration
  currentDocId.value = doc.id
  currentDoc.value = JSON.parse(JSON.stringify(doc))
  const { stem: displayName } = splitOrganizationName(doc.name, doc.is_folder)
  organizeForm.value = { name: displayName, target_dir: '', tags: (doc.tags || []).map(tag => tag.name) }
  await loadAllTags()
  if (generation === pageGeneration && !disposed) showOrganizeDialog.value = true
}

const submitOrganize = async () => {
  if (organizeLoading.value) return
  // 验证标签必填
  if (!organizeForm.value.tags || organizeForm.value.tags.length === 0) {
    ElMessage.warning('请至少添加一个标签')
    return
  }

  const generation = pageGeneration, documentId = currentDocId.value
  organizeLoading.value = true
  // === 整理前查重：检查同内容文件是否已在文档库 ===
  try {
    const dupRes = await checkDocLibraryDuplicate(documentId)
    if (generation !== pageGeneration || disposed) return
    if (dupRes?.is_duplicate && dupRes.check_succeeded) {
      const existDoc = dupRes.existing_document || dupRes.pending_document
      const confirmed = await showDuplicateWarning(existDoc)
      if (!confirmed) { organizeLoading.value = false; return }
    }
  } catch { /* 查重失败不阻塞整理 */ }

  if (generation !== pageGeneration || disposed) return
  const targetDir = organizeForm.value.target_dir || ''
  try {
    await organizeDocument(documentId, {
      target_dir: targetDir,
      tags: organizeForm.value.tags,
      name: organizeForm.value.name,
    })
    if (generation !== pageGeneration || disposed) return
    ElMessage.success('已整理，可在文档库通过标签找到')
    showOrganizeDialog.value = false
    showDetailDrawer.value = false
    
    // 刷新标签列表，确保新创建的标签能够在下拉列表中显示
    await loadAllTags()
    
    // 如果当前页没有数据了，重置到第一页
    if (currentPage.value > 1 && documents.value.length === 1) {
      currentPage.value = 1
    }
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    if (generation === pageGeneration && !disposed) ElMessage.error(e.message || '整理失败')
  } finally {
    if (generation === pageGeneration) organizeLoading.value = false
  }
}

const handleDelete = async (doc) => {
  try {
    await ElMessageBox.confirm(`确定删除"${doc.name}"？`, '确认', { type: 'warning' })
    await deleteDocument(doc.id, true)
    ElMessage.success('已删除')
    // 如果当前页没有数据了，重置到第一页
    if (currentPage.value > 1 && documents.value.length === 1) {
      currentPage.value = 1
    }
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}



const loadAllTags = async () => {
  const generation = pageGeneration
  try {
    const result = await getTags()
    if (generation === pageGeneration && !disposed) allTags.value = result
  } catch (e) {
    if (generation === pageGeneration && !disposed) allTags.value = []
  }
}

const fetchMissingPendingDocs = async () => {
  const generation = pageGeneration
  try {
    const data = await searchDocuments({ status: 'missing', page: 1, page_size: 100 })
    if (generation !== pageGeneration || disposed) return
    missingPendingDocs.value = data.documents.filter(d => {
      return d.storage_area === 'pending'
    })
  } catch {
    if (generation === pageGeneration && !disposed) missingPendingDocs.value = []
  }
}

const handleRemoveMissingPending = async (doc) => {
  try {
    await ElMessageBox.confirm(
      `文件"${doc.name}"已从磁盘缺失，确定永久移除该记录？此操作不可恢复。`,
      '确认移除', { type: 'error' }
    )
    await deleteDocument(doc.id, true)
    ElMessage.success('已移除')
    fetchMissingPendingDocs()
    appStore.fetchPendingCount()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '移除失败')
  }
}

const handleScan = async () => {
  scanLoading.value = true
  try {
    const result = await scanLibrary()
    const msg = `扫描完成：发现 ${result.missing} 个缺失` +
      (result.restored > 0 ? `，已恢复 ${result.restored} 个` : '')
    ElMessage.success(msg)
    fetchMissingPendingDocs()
    fetchPending()
  } catch {
    ElMessage.error('扫描失败')
  } finally {
    scanLoading.value = false
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

// ========== 批量整理 ==========
const showBatchOrganizeDialog = ref(false)
const batchOrganizeLoading = ref(false)
const batchOrganizeForm = ref({ target_dir: '', tags: [] })
const batchOrganizeSnapshot = ref([])
let batchOrganizeSequence = 0
watch(showBatchOrganizeDialog, visible => {
  if (!visible) { batchOrganizeSequence++; batchOrganizeSnapshot.value = []; batchOrganizeLoading.value = false }
}, { flush: 'sync' })

const batchOrganize = async () => {
  if (batchOrganizeLoading.value) return
  const generation = pageGeneration, sequence = ++batchOrganizeSequence
  const snapshot = selectedDocuments.value.map(doc => ({ id: doc.id, name: doc.name }))
  if (!snapshot.length) return
  batchOrganizeSnapshot.value = snapshot
  batchOrganizeForm.value = { target_dir: '', tags: [] }
  await loadAllTags()
  if (generation === pageGeneration && sequence === batchOrganizeSequence && !disposed) showBatchOrganizeDialog.value = true
}

const submitBatchOrganize = async () => {
  if (batchOrganizeLoading.value || !showBatchOrganizeDialog.value || !batchOrganizeSnapshot.value.length) return
  // 验证标签必填
  if (!batchOrganizeForm.value.tags || batchOrganizeForm.value.tags.length === 0) {
    ElMessage.warning('请至少添加一个标签')
    return
  }

  const generation = pageGeneration, sequence = batchOrganizeSequence
  const ids = batchOrganizeSnapshot.value.map(doc => doc.id)
  batchOrganizeLoading.value = true
  try {
    await batchOrganizeDocuments({
      document_ids: ids,
      target_dir: batchOrganizeForm.value.target_dir || '',
      tags: [...batchOrganizeForm.value.tags],
    })
    if (generation !== pageGeneration || sequence !== batchOrganizeSequence || disposed) return
    ElMessage.success(`已完成 ${ids.length} 项整理`)
    showBatchOrganizeDialog.value = false
    showDetailDrawer.value = false
    
    // 刷新标签列表，确保新创建的标签能够在下拉列表中显示
    await loadAllTags()
    if (generation !== pageGeneration || disposed) return
    
    // 如果当前页没有数据了，重置到第一页
    if (currentPage.value > 1 && documents.value.length <= ids.length) {
      currentPage.value = 1
    }
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    if (generation === pageGeneration && sequence === batchOrganizeSequence && !disposed) ElMessage.error(e.message || '批量整理失败')
  } finally {
    if (generation === pageGeneration && sequence === batchOrganizeSequence) batchOrganizeLoading.value = false
  }
}

// ========== 批量打标签 ==========
const showBatchTagDialog = ref(false)
const batchTagLoading = ref(false)
const batchTagForm = ref({ add_tags: [], remove_tags: [] })
const batchTagSnapshot = ref([])
let batchTagSequence = 0
watch(showBatchTagDialog, visible => {
  if (!visible) { batchTagSequence++; batchTagSnapshot.value = []; batchTagLoading.value = false }
}, { flush: 'sync' })

const batchTag = async () => {
  if (batchTagLoading.value) return
  const generation = pageGeneration, sequence = ++batchTagSequence
  const snapshot = selectedDocuments.value.map(doc => ({ id: doc.id, name: doc.name }))
  if (!snapshot.length) return
  batchTagSnapshot.value = snapshot
  batchTagForm.value = { add_tags: [], remove_tags: [] }
  await loadAllTags()
  if (generation === pageGeneration && sequence === batchTagSequence && !disposed) showBatchTagDialog.value = true
}

// ========== 批量删除 ==========
const batchDelete = async () => {
  const ids = selectedIds.value
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定永久删除选中的 ${ids.length} 个文档？此操作不可恢复。`,
      '批量删除',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'error' }
    )
    await batchDeleteDocuments(ids)
    ElMessage.success(`已删除 ${ids.length} 个文档`)
    if (currentPage.value > 1 && documents.value.length <= ids.length) {
      currentPage.value = 1
    }
    fetchPending()
    appStore.fetchPendingCount()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '批量删除失败')
  }
}

const submitBatchTag = async () => {
  if (batchTagLoading.value || !showBatchTagDialog.value || !batchTagSnapshot.value.length) return
  if (batchTagForm.value.add_tags.length === 0 && batchTagForm.value.remove_tags.length === 0) {
    ElMessage.warning('请选择要添加或移除的标签')
    return
  }
  const generation = pageGeneration, sequence = batchTagSequence
  const ids = batchTagSnapshot.value.map(doc => doc.id)
  batchTagLoading.value = true
  try {
    await batchUpdateTags({
      document_ids: ids,
      add_tags: [...batchTagForm.value.add_tags],
      remove_tags: [...batchTagForm.value.remove_tags],
    })
    if (generation !== pageGeneration || sequence !== batchTagSequence || disposed) return
    ElMessage.success(`已为 ${ids.length} 项资料更新标签`)
    showBatchTagDialog.value = false
    fetchPending()
  } catch (e) {
    if (generation === pageGeneration && sequence === batchTagSequence && !disposed) ElMessage.error(e.message || '批量打标签失败')
  } finally {
    if (generation === pageGeneration && sequence === batchTagSequence) batchTagLoading.value = false
  }
}

function resetPendingPage() {
  pageGeneration++; pendingSearchSequence++
  pendingController?.abort(); clearTimeout(pendingTimer)
  documents.value = []; total.value = 0; preorganizeCounts.value = {}; pendingSearchError.value = ''
  currentPage.value = 1; searchInput.value = ''; activeKeyword.value = ''; preorganizeFilter.value = ''
  showOrganizeDialog.value = false; showAIAssist.value = false; currentDoc.value = null; currentDocId.value = null
  showBatchOrganizeDialog.value = false; showBatchTagDialog.value = false; showDetailDrawer.value = false; detailDoc.value = null
  batchOrganizeSequence++; batchTagSequence++; batchOrganizeSnapshot.value = []; batchTagSnapshot.value = []
  showImportDialog.value = false; showAnalysisResult.value = false; missingPendingDocs.value = []; allTags.value = []
  organizeLoading.value = false; batchOrganizeLoading.value = false; batchTagLoading.value = false
  if (showDupDialog.value) dupCancelCallback.value?.()
  showDupDialog.value = false
}
watch(() => appStore.libraryPath, () => {
  resetPendingPage()
  fetchPending(); loadAllTags(); fetchMissingPendingDocs()
}, { flush: 'sync' })
onUnmounted(() => { disposed = true; resetPendingPage() })

onMounted(async () => {
  const generation = pageGeneration
  try {
    fetchPending()
    loadAllTags()
    fetchMissingPendingDocs()

    // 处理 ?highlight=docId（由查重"前往查看"跳转而来）
    const highlightId = route.query.highlight
    if (highlightId) {
      try {
        const doc = await getDocument(String(highlightId))
        if (doc && generation === pageGeneration && !disposed) {
          detailDoc.value = { ...doc }
          showDetailDrawer.value = true
        }
      } catch (e) {
        console.warn('[PendingPage] highlight 文档获取失败:', e)
      }
    }
  } catch (e) {
    console.error('PendingPage onMounted error:', e)
  }
})
</script>

<style scoped>
.doc-preorganize { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; margin-top: 7px; }
.doc-preorganize>span { font-size: 11px; color: #8a97a7; }
.doc-preorganize .doc-preorganize-error { color: #b96257; max-width: 320px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.pending-card .doc-meta { max-width: 120px; white-space: normal; line-height: 1.7; text-align: right; }
.pending-card .doc-actions { flex-wrap: wrap; justify-content: flex-end; max-width: 300px; }
.pending-card .doc-actions :deep(.el-button + .el-button) { margin-left: 0; }
.organize-ai-action { display:flex; align-items:center; gap:10px; margin:0 0 16px; }
.organize-ai-action span { color:#7f8b99; font-size:12px; }
.pending-search { display: flex; gap: 12px; padding: 16px; margin-bottom: 16px; background: #fff; border: 1px solid #e8edf3; border-radius: 10px; }
.pending-search .el-input { flex: 1; min-width: 0; }
.pending-search-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 0 0 16px; color: #606266; font-size: 13px; overflow-wrap: anywhere; }
.pending-search-error { margin-bottom: 16px; }
.empty-search-hint { font-size: 13px; color: #909399; }

.import-source-actions { display: flex; gap: 10px; margin-bottom: 20px; }
.import-source-actions .el-button + .el-button { margin-left: 0; }
.import-source-actions .el-icon { margin-right: 6px; }
.import-selected-name { margin: 0 0 14px; font-weight: 600; color: #303133; overflow-wrap: anywhere; }
.import-hint { color: #909399; font-size: 12px; line-height: 1.6; }
.import-notice { padding: 12px 16px; border-radius: 8px; background: #f4f7fb; color: #606266; font-size: 13px; line-height: 1.7; }
.import-notice p { margin: 0; }
.import-notice p + p { margin-top: 6px; }

.page-header {
  margin-bottom: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-heading p { margin: 8px 0 0; font-size: 13px; line-height: 1.6; color: #68788a; }

.page-header h2 {
  font-size: 22px;
  color: #2c3e50;
}

.count-badge {
  background: #ecf5ff;
  color: #337ecc;
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
  padding: 9px 16px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 5px;
  border: 1px solid #ebeef5;
  box-shadow: var(--shadow-sm);
  transition: var(--transition-base);
  cursor: pointer;
}

.pending-card:hover {
  box-shadow: inset 3px 0 0 #e6a23c, var(--shadow-hover);
  transform: translateY(-2px);
  border-color: #faecd8;
}

.card-checkbox {
  margin-right: 10px;
}

.doc-icon {
  margin-right: 12px;
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

.doc-context { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 5px; }
.folder-context { font-size: 12px; color: #7d6a4f; }
.organize-summary { display: flex; gap: 12px; padding: 14px 16px; margin-bottom: 22px; border-radius: 8px; background: #f4f7fb; }
.organize-summary > div { min-width: 0; }
.organize-summary strong { display: block; font-size: 14px; color: #25364a; overflow-wrap: anywhere; }
.organize-summary p { margin: 6px 0 0; font-size: 12px; line-height: 1.6; color: #68788a; }
.organize-summary .batch-snapshot-names { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 440px; }
.summary-icon { flex: 0 0 auto; padding: 9px; border-radius: 8px; background: #e8f1ff; color: #337ecc; font-size: 20px; box-sizing: content-box; }
.organize-form :deep(.el-form-item__label) { font-weight: 600; }
@media (max-width: 800px) {
  .page-header { align-items: flex-start; flex-direction: column; gap: 14px; }
  .header-actions, .batch-bar { flex-wrap: wrap; }
  .pending-card { flex-wrap: wrap; gap: 8px; }
  .doc-meta { display: none; }
}
:deep(.organize-dialog) {
  max-width: calc(100vw - 32px);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
}
:deep(.organize-dialog .el-dialog__body) { overflow-y: auto; min-height: 0; }
:deep(.organize-dialog .el-dialog__header),
:deep(.organize-dialog .el-dialog__footer) { flex-shrink: 0; }

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

.doc-actions {
  display: flex;
  gap: 6px;
  margin-left: 4px;
}

.batch-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: #2c3e50;
  color: #fff;
  padding: 12px 32px;
  border-radius: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.15);
  z-index: 100;
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

.form-item-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
  display: flex;
  align-items: center;
  gap: 4px;
}

.form-item-hint .el-icon {
  font-size: 14px;
  color: #909399;
}

/* 整理对话框样式优化 */
:deep(.organize-dialog) {
  .el-dialog__header {
    padding: 20px 24px 16px;
    border-bottom: 1px solid #f0f0f0;
  }

  .el-dialog__title {
    font-size: 18px;
    font-weight: 600;
    color: #2c3e50;
  }

  .el-dialog__body {
    padding: 20px 24px;
  }

  .el-dialog__footer {
    padding: 16px 24px;
    border-top: 1px solid #f0f0f0;
  }
}

.organize-dialog-content {
  padding: 0;
}

.organize-form {
  .form-item-spacing {
    margin-bottom: 20px;
  }

  .el-form-item__label {
    font-weight: 500;
    color: #606266;
    font-size: 14px;
  }

  .el-input,
  .el-select {
    font-size: 14px;
  }

  .el-divider {
    margin: 20px 0;
    border-color: #f0f0f0;
  }
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.dialog-footer .el-button {
  min-width: 100px;
  font-size: 14px;
}

.pagination {
  margin-top: 24px;
  display: flex;
  justify-content: center;
}

.missing-list { margin-top: 8px; }
.missing-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid #faecd8;
}
.missing-item:last-child { border-bottom: none; }
.missing-name { font-weight: 500; color: #2c3e50; min-width: 120px; }
.missing-path {
  flex: 1;
  font-size: 12px;
  color: #95a5a6;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.missing-actions { display: flex; gap: 4px; flex-shrink: 0; }
</style>
