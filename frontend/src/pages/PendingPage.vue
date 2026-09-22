<template>
  <div class="pending-page">
    <div class="page-header">
      <h2>待整理 <span v-if="total > 0" class="count-badge">{{ total }}</span></h2>
      <div class="header-actions">
        <el-button
          v-if="documents.length > 0"
          size="default"
          text
          @click="handleSelectAll"
        >{{ allSelected ? '取消全选' : '全选' }}</el-button>
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
          </div>
          <div class="doc-meta">
            <span v-if="doc.is_folder">{{ doc.file_count }} 个文件</span>
            <span v-else>{{ formatFileSize(doc.total_size) }}</span>
            <span class="meta-sep">·</span>
            <span>{{ formatDateTime(doc.created_at) }}</span>
          </div>
          <div class="doc-actions">
            <el-button size="small" text @click.stop="handleOpen(doc)">打开</el-button>
            <el-button v-if="doc.storage_mode === 'move'" size="small" text @click.stop="handleUndo(doc)">撤销</el-button>
            <el-button size="small" type="primary" @click.stop="organizeOne(doc)">整理</el-button>
          </div>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <el-icon :size="64" color="#bdc3c7"><CircleCheck /></el-icon>
        <p>没有待整理的文件</p>
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
      <el-button size="small" @click="openMergeDialog" :disabled="selectedIds.length < 2">合并为版本</el-button>
      <el-button size="small" type="danger" @click="batchDelete">删除</el-button>
    </div>

    <!-- 批量整理对话框 -->
    <el-dialog v-model="showBatchOrganizeDialog" title="批量整理" width="520px">
      <el-form label-width="80px">
        <el-form-item label="存放到" required>
          <DirectoryTreeSelector v-model="batchOrganizeForm.target_dir" />
        </el-form-item>
        <el-form-item label="标签" required>
          <el-select
            ref="batchOrganizeTagSelectRef"
            v-model="batchOrganizeForm.tags"
            multiple
            filterable
            allow-create
            :filter-method="handleBatchOrganizeTagFilter"
            :reserve-keyword="true"
            placeholder="选择或输入标签（必填）"
            style="width: 100%"
            @change="handleBatchOrganizeTagChange"
            @visible-change="handleBatchOrganizeTagVisibleChange"
            @keydown.enter="handleBatchOrganizeTagEnter"
          >
            <el-option v-for="tag in filteredBatchOrganizeTags" :key="tag.id" :label="tag.name" :value="tag.name" />
          </el-select>
          <div class="form-item-hint">至少需要添加一个标签</div>
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
    <el-dialog 
      v-model="showOrganizeDialog" 
      title="整理文档" 
      width="560px"
      :close-on-click-modal="false"
      class="organize-dialog"
    >
      <div class="organize-dialog-content">
        <el-form label-width="100px" label-position="left" class="organize-form">
          <el-form-item label="文档名称" class="form-item-spacing">
            <el-input 
              v-model="organizeForm.name" 
              :placeholder="currentDoc?.is_folder ? '输入文件夹名称' : '输入文件名称（不含扩展名）'"
              size="default"
              clearable
            />
            <div v-if="currentDoc && !currentDoc.is_folder" class="form-item-hint">
              <el-icon><InfoFilled /></el-icon>
              <span>可重命名，文件扩展名将自动保留</span>
            </div>
          </el-form-item>
          
          <el-divider />

          <el-form-item label="存放到" required class="form-item-spacing">
            <DirectoryTreeSelector v-model="organizeForm.target_dir" />
          </el-form-item>

          <el-divider />
          
          <el-form-item label="标签" required class="form-item-spacing">
            <el-select
              ref="organizeTagSelectRef"
              v-model="organizeForm.tags"
              multiple
              filterable
              allow-create
              :filter-method="handleOrganizeTagFilter"
              :reserve-keyword="true"
              placeholder="选择或输入标签（必填）"
              size="default"
              style="width: 100%"
              @change="handleOrganizeTagChange"
              @visible-change="handleOrganizeTagVisibleChange"
              @keydown.enter="handleOrganizeTagEnter"
            >
              <el-option v-for="tag in filteredOrganizeTags" :key="tag.id" :label="tag.name" :value="tag.name" />
            </el-select>
            <div class="form-item-hint">
              <el-icon><InfoFilled /></el-icon>
              <span>至少需要添加一个标签</span>
            </div>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showOrganizeDialog = false" size="default">取消</el-button>
          <el-button type="primary" @click="submitOrganize" :loading="organizeLoading" size="default">
            <el-icon v-if="!organizeLoading"><Check /></el-icon>
            完成整理
          </el-button>
        </div>
      </template>
    </el-dialog>

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
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { searchDocuments, organizeDocument, deleteDocument, batchOrganizeDocuments, batchUpdateTags, analyzePending, getDocument, batchDeleteDocuments, undoPending, checkDocLibraryDuplicate } from '@/api/document'
import { getTags } from '@/api/tag'
import { scanLibrary } from '@/api/settings'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { formatFileSize, formatDateTime } from '@/utils/format'
import { Folder, Document, Delete, CircleCheck, Grid, DataBoard, Picture, VideoCamera, Headset, Box, Notebook, MagicStick, InfoFilled, Check, WarningFilled } from '@element-plus/icons-vue'
import DocumentDetailDrawer from '@/components/DocumentDetailDrawer.vue'
import DirectoryTreeSelector from '@/components/DirectoryTreeSelector.vue'
import DuplicateWarningDialog from '@/components/DuplicateWarningDialog.vue'
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
const pageSize = computed(() => appStore.settings.items_per_page || 15)
const showOrganizeDialog = ref(false)
const organizeLoading = ref(false)
const currentDocId = ref(null)
const organizeTagSelectRef = ref(null)
const batchOrganizeTagSelectRef = ref(null)

// 标签输入关键字状态
const organizeTagKeyword = ref('')
const batchOrganizeTagKeyword = ref('')

// 计算当前正在整理的文档
const currentDoc = computed(() => {
  if (!currentDocId.value) return null
  return documents.value.find(d => d.id === currentDocId.value) || null
})

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

// 计算匹配的标签（用于批量整理对话框）
const filteredBatchOrganizeTags = computed(() => {
  try {
    if (!Array.isArray(allTags.value)) {
      return []
    }
    const currentTags = batchOrganizeForm.value?.tags || []
    if (!Array.isArray(currentTags)) {
      return []
    }
    if (!batchOrganizeTagKeyword.value) {
      return allTags.value.filter(tag => tag && tag.name && !currentTags.includes(tag.name))
    }
    const keyword = batchOrganizeTagKeyword.value.toLowerCase()
    return allTags.value.filter(tag => {
      if (!tag || !tag.name) return false
      const tagName = tag.name.toLowerCase()
      return tagName.includes(keyword) && !currentTags.includes(tag.name)
    })
  } catch (e) {
    console.error('filteredBatchOrganizeTags error:', e)
    return []
  }
})

// 缺失的待整理文档
const missingPendingDocs = ref([])
const scanLoading = ref(false)

// 文档详情 Drawer
const showDetailDrawer = ref(false)
const detailDoc = ref(null)

const openDetail = async (doc) => {
  // 始终从 API 获取最新数据
  try {
    const latest = await getDocument(doc.id)
    detailDoc.value = { ...latest }
  } catch {
    detailDoc.value = { ...doc }
  }
  showDetailDrawer.value = true
}

const handleOpen = (doc) => {
  if (!window.electron?.openPath) {
    ElMessage.info('此功能仅在 Electron 桌面应用中可用')
    return
  }

  let fullPath = null
  if (doc.storage_path && appStore.libraryPath) {
    // 有存储路径，拼接文件库路径
    // 规范化路径：统一使用反斜杠，确保 Windows 路径格式正确
    const libPath = appStore.libraryPath.replace(/\//g, '\\').replace(/\\+$/, '') // 移除末尾的反斜杠
    const storagePath = doc.storage_path.replace(/\//g, '\\')
    fullPath = `${libPath}\\${storagePath}`
  } else if (doc.original_path) {
    // 索引模式：使用原始路径（已经是正确的 Windows 路径格式）
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

const handleUndo = async (doc) => {
  try {
    await undoPending(doc.id)
    ElMessage.success('已撤销，文件已移回原始位置')
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

const fetchPending = async () => {
  loading.value = true
  try {
    const data = await searchDocuments({
      status: 'pending',
      sort_by: 'created_at',
      sort_order: 'desc',
      page: currentPage.value,
      page_size: pageSize.value,
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
  // 对于文件，显示不带扩展名的名称，方便用户重命名
  let displayName = doc.name
  if (!doc.is_folder && doc.name) {
    const lastDotIndex = doc.name.lastIndexOf('.')
    if (lastDotIndex > 0) {
      displayName = doc.name.substring(0, lastDotIndex)
    }
  }
  organizeForm.value = { name: displayName, target_dir: '', tags: [] }
  organizeTagKeyword.value = ''
  await loadAllTags()
  showOrganizeDialog.value = true
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

// 处理标签输入变化，清空输入框
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

// 处理批量整理标签过滤
const handleBatchOrganizeTagFilter = (val) => {
  batchOrganizeTagKeyword.value = val || ''
}

// 处理批量整理标签下拉框显示变化
const handleBatchOrganizeTagVisibleChange = (visible) => {
  if (visible) {
    const keyword = batchOrganizeTagKeyword.value.trim()
    if (keyword && filteredBatchOrganizeTags.value.length === 0) {
      // 没有匹配标签，延迟隐藏下拉框
      nextTick(() => {
        if (batchOrganizeTagSelectRef.value) {
          batchOrganizeTagSelectRef.value.blur()
        }
      })
    }
  }
}

// 处理批量整理标签回车事件
const handleBatchOrganizeTagEnter = async (event) => {
  const keyword = batchOrganizeTagKeyword.value.trim()
  if (!keyword) return
  
  // 阻止默认行为
  event.preventDefault()
  event.stopPropagation()
  
  // 检查是否有匹配的标签
  const matchedTags = filteredBatchOrganizeTags.value
  if (matchedTags.length > 0) {
    // 有匹配标签，选中第一个
    const firstTag = matchedTags[0].name
    if (!batchOrganizeForm.value?.tags?.includes(firstTag)) {
      if (!batchOrganizeForm.value) return
      if (!batchOrganizeForm.value.tags) {
        batchOrganizeForm.value.tags = []
      }
      batchOrganizeForm.value.tags.push(firstTag)
      batchOrganizeTagKeyword.value = ''
      await nextTick()
      handleBatchOrganizeTagChange()
    }
  } else {
    // 没有匹配标签，创建新标签
    if (!batchOrganizeForm.value?.tags?.includes(keyword)) {
      if (!batchOrganizeForm.value) return
      if (!batchOrganizeForm.value.tags) {
        batchOrganizeForm.value.tags = []
      }
      batchOrganizeForm.value.tags.push(keyword)
      batchOrganizeTagKeyword.value = ''
      await nextTick()
      handleBatchOrganizeTagChange()
    }
  }
}

// 处理批量整理标签输入变化，清空输入框
const handleBatchOrganizeTagChange = async () => {
  await nextTick()
  if (batchOrganizeTagSelectRef.value) {
    // 清空 el-select 的输入框
    const input = batchOrganizeTagSelectRef.value.$el?.querySelector('input')
    if (input) {
      input.value = ''
      batchOrganizeTagKeyword.value = ''
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

  // === 整理前查重：检查同内容文件是否已在文档库 ===
  try {
    const dupRes = await checkDocLibraryDuplicate(currentDocId.value)
    if (dupRes?.is_duplicate && dupRes.check_succeeded) {
      const existDoc = dupRes.existing_document || dupRes.pending_document
      const confirmed = await showDuplicateWarning(existDoc)
      if (!confirmed) return  // 用户取消
    }
  } catch { /* 查重失败不阻塞整理 */ }

  const targetDir = organizeForm.value.target_dir

  organizeLoading.value = true
  try {
    const organizeResult = await organizeDocument(currentDocId.value, {
      target_dir: targetDir,
      tags: organizeForm.value.tags,
      name: organizeForm.value.name,
    })
    ElMessage.success('整理完成')
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
  try {
    allTags.value = await getTags()
  } catch (e) {
    allTags.value = []
  }
}

const fetchMissingPendingDocs = async () => {
  try {
    const data = await searchDocuments({ status: 'missing', page: 1, page_size: 100 })
    const pfn = appStore.pendingFolderName || '待整理'
    missingPendingDocs.value = data.documents.filter(d => {
      const p = d.storage_path || ''
      return p.startsWith(pfn + '/') || p.startsWith(pfn + '\\')
    })
  } catch {
    missingPendingDocs.value = []
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
  ElMessage.info('版本合并功能已迁移至云端同步，请使用推送到云端来管理版本历史')
  showMergeDialog.value = false
}

// ========== 批量整理 ==========
const showBatchOrganizeDialog = ref(false)
const batchOrganizeLoading = ref(false)
const batchOrganizeForm = ref({ target_dir: '', tags: [] })

const batchOrganize = async () => {
  batchOrganizeForm.value = { target_dir: '', tags: [] }
  batchOrganizeTagKeyword.value = ''
  await loadAllTags()
  showBatchOrganizeDialog.value = true
}

const submitBatchOrganize = async () => {
  // 验证标签必填
  if (!batchOrganizeForm.value.tags || batchOrganizeForm.value.tags.length === 0) {
    ElMessage.warning('请至少添加一个标签')
    return
  }

  // target_dir 为空字符串表示放在文件库根目录，null 表示未选择
  if (batchOrganizeForm.value.target_dir === null || batchOrganizeForm.value.target_dir === undefined) {
    ElMessage.warning('请在目录树中选择存放位置')
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
    showDetailDrawer.value = false
    
    // 刷新标签列表，确保新创建的标签能够在下拉列表中显示
    await loadAllTags()
    
    // 如果当前页没有数据了，重置到第一页
    const selectedCount = selectedIds.value.length
    if (currentPage.value > 1 && documents.value.length <= selectedCount) {
      currentPage.value = 1
    }
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

onMounted(async () => {
  try {
    fetchPending()
    loadAllTags()
    fetchMissingPendingDocs()

    // 处理 ?highlight=docId（由查重"前往查看"跳转而来）
    const highlightId = route.query.highlight
    if (highlightId) {
      try {
        const doc = await getDocument(String(highlightId))
        if (doc) {
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
    padding: 24px;
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
