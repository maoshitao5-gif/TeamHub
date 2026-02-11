<template>
  <div class="file-upload-container">
    <el-card class="upload-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <el-icon><Upload /></el-icon>
          <span>文件上传</span>
        </div>
      </template>
      
      <!-- 标签输入区域 -->
      <div class="tag-input-section">
        <div class="tag-input-label">
          <el-icon><PriceTag /></el-icon>
          <span>添加标签（上传前必填）</span>
        </div>
        <div class="tag-input-wrapper">
          <el-tag
            v-for="tag in tags"
            :key="tag"
            closable
            @close="removeTag(tag)"
            type="primary"
            effect="plain"
            class="tag-item"
          >
            {{ tag }}
          </el-tag>
          <el-autocomplete
            v-model="tagInput"
            :fetch-suggestions="queryTags"
            placeholder="输入标签名称，支持自动完成"
            class="tag-input"
            @select="handleTagSelect"
            @keyup.enter="addTag"
            clearable
            :trigger-on-focus="true"
            popper-class="tag-autocomplete-popper"
          >
            <template #default="{ item }">
              <div class="tag-suggestion">
                <span class="tag-name">{{ item.name }}</span>
                <span class="tag-count" v-if="item.file_count > 0">
                  ({{ item.file_count }}个文件)
                </span>
              </div>
            </template>
          </el-autocomplete>
        </div>
        <div class="tag-hint">
          <el-text type="info" size="small">
            提示：输入标签名称会自动提示已存在的标签，按回车键添加，点击标签可删除
          </el-text>
        </div>
      </div>

      <!-- 上传模式选择 -->
      <div class="upload-mode-section">
        <el-radio-group v-model="uploadMode" @change="handleModeChange">
          <el-radio-button label="single">单文件上传</el-radio-button>
          <el-radio-button label="folder">文件夹上传</el-radio-button>
        </el-radio-group>
        <el-text type="info" size="small" style="margin-left: 12px;">
          {{ uploadMode === 'single' ? '上传单个文件' : '上传整个文件夹，保持项目结构' }}
        </el-text>
      </div>

      <!-- 单文件上传区域 -->
      <div 
        v-if="uploadMode === 'single'"
        class="single-file-upload-wrapper"
        @drop.prevent="handleSingleFileDrop"
        @dragover.prevent="handleDragOver"
        @dragenter.prevent="handleDragEnter"
        @dragleave.prevent="handleDragLeave"
      >
        <el-upload
          ref="uploadRef"
          class="upload-dragger"
          drag
          :auto-upload="false"
          :file-list="fileList"
          :on-change="handleFileChange"
          :limit="1"
          :before-upload="beforeUpload"
          accept="*/*"
          :disabled="isDragging"
        >
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">
            将文件拖到此处，或<em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              支持任意格式文件，文件大小无限制
            </div>
          </template>
        </el-upload>
      </div>

      <!-- 文件夹上传区域 -->
      <div v-if="uploadMode === 'folder'" class="folder-upload-area">
        <div 
          class="folder-upload-dragger" 
          :class="{ 'drag-over': isDragging }"
          @click="triggerFolderSelect" 
          @drop.prevent="handleFolderDrop" 
          @dragover.prevent="handleDragOver"
          @dragenter.prevent="handleDragEnter"
          @dragleave.prevent="handleDragLeave"
        >
          <input
            ref="folderInputRef"
            type="file"
            webkitdirectory
            directory
            multiple
            style="display: none"
            @change="handleFolderInputChange"
          />
          <el-icon class="el-icon--upload"><FolderOpened /></el-icon>
          <div class="el-upload__text">
            将文件夹拖到此处，或<em>点击选择文件夹</em>
          </div>
          <div class="el-upload__tip">
            选择文件夹后，将保持文件夹结构上传所有文件
          </div>
        </div>
        
        <!-- 文件夹文件列表预览 -->
        <div v-if="folderFileList.length > 0" class="folder-preview">
          <el-divider>文件夹内容预览（{{ folderFileList.length }} 个文件）</el-divider>
          <el-scrollbar height="200px">
            <div class="folder-file-list">
              <div
                v-for="(file, index) in folderFileList"
                :key="index"
                class="folder-file-item"
              >
                <el-icon><Document /></el-icon>
                <span class="file-path">{{ getFileRelativePath(file) }}</span>
                <el-text type="info" size="small">
                  {{ formatFileSize(file.size) }}
                </el-text>
              </div>
            </div>
          </el-scrollbar>
        </div>
      </div>

      <!-- 上传进度和状态 -->
      <div v-if="uploadStatus !== 'idle'" class="upload-status-section">
        <div class="status-info">
          <el-icon v-if="uploadStatus === 'compressing'" class="status-icon compressing">
            <Loading />
          </el-icon>
          <el-icon v-else-if="uploadStatus === 'uploading'" class="status-icon uploading">
            <Loading />
          </el-icon>
          <el-icon v-else-if="uploadStatus === 'validating'" class="status-icon validating">
            <Search />
          </el-icon>
          <el-icon v-else-if="uploadStatus === 'success'" class="status-icon success">
            <CircleCheck />
          </el-icon>
          <el-icon v-else-if="uploadStatus === 'duplicate'" class="status-icon duplicate">
            <Warning />
          </el-icon>
          <el-icon v-else-if="uploadStatus === 'error'" class="status-icon error">
            <CircleClose />
          </el-icon>
          
          <span class="status-text">{{ statusText }}</span>
        </div>
        
        <!-- 上传进度条 -->
        <el-progress
          v-if="uploadStatus === 'compressing' || uploadStatus === 'uploading' || uploadStatus === 'validating'"
          :percentage="uploadProgress"
          :status="uploadStatus === 'validating' ? 'warning' : uploadStatus === 'compressing' ? 'success' : undefined"
          :stroke-width="8"
          striped
          striped-flow
        />
      </div>

      <!-- 上传按钮 -->
      <div class="upload-actions">
        <el-button
          type="primary"
          :loading="uploading || validating || compressing"
          :disabled="!canUpload || uploading || validating || compressing"
          @click="handleUpload"
          size="large"
        >
          <el-icon v-if="!uploading && !validating"><Upload /></el-icon>
          {{ uploadButtonText }}
        </el-button>
        <el-button @click="handleReset" size="large" :disabled="uploading || validating">重置</el-button>
      </div>
      
      <!-- 调试信息（可选，帮助用户了解状态） -->
      <div v-if="(uploadMode === 'single' && fileList.length > 0) || (uploadMode === 'folder' && folderFileList.length > 0) || tags.length > 0" class="upload-status">
        <el-text type="info" size="small">
          <span v-if="uploadMode === 'single' && fileList.length > 0">✓ 已选择文件: {{ fileList[0]?.name }}</span>
          <span v-if="uploadMode === 'folder' && folderFileList.length > 0">✓ 已选择文件夹: {{ folderRootPath || '文件夹' }} ({{ folderFileList.length }} 个文件)</span>
          <span v-if="tags.length > 0"> | ✓ 已添加 {{ tags.length }} 个标签</span>
          <span v-if="uploadMode === 'single' && fileList.length === 0">⚠ 请选择文件</span>
          <span v-if="uploadMode === 'folder' && folderFileList.length === 0">⚠ 请选择文件夹</span>
          <span v-if="tags.length === 0"> | ⚠ 请添加至少一个标签</span>
        </el-text>
      </div>
    </el-card>

    <!-- 删除原文件确认对话框 -->
    <el-dialog
      v-model="showDeleteSourceDialog"
      title="是否删除原路径文件"
      width="500px"
    >
      <div class="delete-source-info">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          style="margin-bottom: 20px;"
        >
          <template #title>
            <div style="font-size: 14px;">
              文件已添加到上传队列
            </div>
          </template>
        </el-alert>
        
        <div class="file-path-display">
          <el-text type="primary" style="font-weight: 600;">文件路径：</el-text>
          <el-text type="info" style="word-break: break-all; font-family: monospace; font-size: 12px;">
            {{ sourceFilePath || '未知路径' }}
          </el-text>
        </div>
        
        <div style="margin-top: 16px;">
          <el-text type="warning" size="small">
            ⚠️ 注意：删除操作将在文件上传成功后执行。如果上传失败，原文件不会被删除。
          </el-text>
        </div>
      </div>
      <template #footer>
        <el-button @click="handleCancelDeleteSource">保留原文件</el-button>
        <el-button type="danger" @click="handleConfirmDeleteSource">删除原文件</el-button>
      </template>
    </el-dialog>

    <!-- 重复文件警告对话框 -->
    <el-dialog
      v-model="duplicateDialogVisible"
      :title="uploadMode === 'folder' ? '文件夹已存在' : '文件已存在'"
      width="600px"
    >
      <div class="duplicate-info">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 20px;"
        >
          <template #title>
            <div style="font-size: 16px; font-weight: 600; margin-bottom: 8px;">
              {{ uploadMode === 'folder' ? '系统已存在相同内容的文件夹压缩包，无需重复上传' : '系统已存在相同内容的文件，无需重复上传' }}
            </div>
          </template>
        </el-alert>
        
        <div v-if="duplicateFileInfo" class="file-details">
          <el-descriptions :column="1" border>
            <el-descriptions-item :label="uploadMode === 'folder' ? '压缩包名称' : '文件名'">
              <span class="file-name">{{ duplicateFileInfo.original_filename }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="文件大小">
              {{ formatFileSize(duplicateFileInfo.file_size) }}
            </el-descriptions-item>
            <el-descriptions-item label="上传时间">
              {{ formatDateTime(duplicateFileInfo.upload_time) }}
            </el-descriptions-item>
            <el-descriptions-item label="标签" v-if="duplicateFileInfo.tags && duplicateFileInfo.tags.length > 0">
              <el-tag
                v-for="tag in duplicateFileInfo.tags"
                :key="tag"
                type="primary"
                effect="plain"
                style="margin-right: 8px; margin-bottom: 4px;"
              >
                {{ tag }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="标签" v-else>
              <el-text type="info" size="small">无标签</el-text>
            </el-descriptions-item>
            <el-descriptions-item :label="uploadMode === 'folder' ? '压缩包ID' : '文件ID'">
              <el-text type="info" size="small">#{{ duplicateFileInfo.id }}</el-text>
            </el-descriptions-item>
            <el-descriptions-item label="SHA-256 哈希值" v-if="duplicateFileInfo.sha256_hash">
              <el-text type="info" size="small" style="font-family: monospace;">
                {{ duplicateFileInfo.sha256_hash.substring(0, 16) }}...
              </el-text>
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </div>
      <template #footer>
        <el-button @click="handleCloseDuplicateDialog">取消上传</el-button>
        <el-button type="primary" @click="handleJumpToFile" v-if="duplicateFileInfo">
          跳转到{{ uploadMode === 'folder' ? '该压缩包' : '该文件' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { 
  Upload, UploadFilled, PriceTag, Loading, Search, 
  CircleCheck, Warning, CircleClose, FolderOpened, Document
} from '@element-plus/icons-vue'
import { uploadFile, uploadFolder, getTags } from '@/api/file'
import JSZip from 'jszip'

const emit = defineEmits(['upload-success', 'jump-to-file'])

// 标签相关
const tags = ref([])
const tagInput = ref('')
const allTags = ref([]) // 存储所有标签用于自动完成

// 上传模式
const uploadMode = ref('single') // 'single' 或 'folder'

// 文件上传相关
const uploadRef = ref(null)
const folderInputRef = ref(null) // 文件夹选择的原生 input 引用
const fileList = ref([])
const folderFileList = ref([]) // 文件夹文件列表
const folderRootPath = ref('') // 文件夹根路径
const uploading = ref(false)
const validating = ref(false) // 校验中状态
const duplicateDialogVisible = ref(false)
const duplicateFileInfo = ref(null) // 存储已存在文件的详细信息

// 上传状态和进度
const uploadStatus = ref('idle') // idle, compressing, uploading, validating, success, duplicate, error
const uploadProgress = ref(0) // 0-100
const compressing = ref(false) // 压缩中状态

// 拖拽状态
const isDragging = ref(false) // 是否正在拖拽
const dragCounter = ref(0) // 拖拽计数器，用于处理嵌套元素

// 删除原文件相关
const showDeleteSourceDialog = ref(false) // 是否显示删除原文件对话框
const sourceFileHandle = ref(null) // 存储文件句柄（用于File System Access API）
const sourceFilePath = ref('') // 存储文件路径（用于显示）
const pendingDeleteAfterUpload = ref(false) // 标记是否在上传成功后删除原文件

// 计算是否可以上传
const canUpload = computed(() => {
  const hasTags = tags.value.length > 0
  if (uploadMode.value === 'single') {
    const hasFile = fileList.value && fileList.value.length > 0
    return hasTags && hasFile && uploadStatus.value === 'idle'
  } else {
    const hasFolder = folderFileList.value && folderFileList.value.length > 0
    return hasTags && hasFolder && uploadStatus.value === 'idle' && !compressing.value
  }
})

// 上传按钮文本
const uploadButtonText = computed(() => {
  if (compressing.value) return '压缩中...'
  if (uploading.value) return '上传中...'
  if (validating.value) return '校验中...'
  return '开始上传'
})

// 状态文本
const statusText = computed(() => {
  switch (uploadStatus.value) {
    case 'compressing':
      return `正在压缩文件夹... ${uploadProgress.value}%`
    case 'uploading':
      return `正在上传文件... ${uploadProgress.value}%`
    case 'validating':
      return `正在校验文件（计算SHA-256）... ${uploadProgress.value}%`
    case 'success':
      return '文件上传成功！'
    case 'duplicate':
      return '文件已存在，请检查是否重复上传'
    case 'error':
      return '上传失败，请重试'
    default:
      return ''
  }
})

// 加载所有标签（用于自动完成）
const loadAllTags = async () => {
  try {
    const result = await getTags()
    allTags.value = result.tags || []
  } catch (error) {
    console.error('加载标签失败:', error)
  }
}

// 标签自动完成查询
const queryTags = (queryString, cb) => {
  const results = queryString
    ? allTags.value.filter(tag => 
        tag.name.toLowerCase().includes(queryString.toLowerCase()) &&
        !tags.value.includes(tag.name) // 排除已添加的标签
      )
    : allTags.value.filter(tag => !tags.value.includes(tag.name))
  
  // 限制显示数量，优先显示使用频率高的
  cb(results.slice(0, 10))
}

// 处理标签选择（从下拉列表选择）
const handleTagSelect = (item) => {
  if (item && item.name) {
    const tagName = item.name.trim()
    if (tagName && !tags.value.includes(tagName)) {
      tags.value.push(tagName)
      tagInput.value = ''
    } else if (tags.value.includes(tagName)) {
      ElMessage.warning('标签已添加')
      tagInput.value = ''
    }
  }
}

// 添加标签
const addTag = () => {
  const tag = tagInput.value.trim()
  if (tag && !tags.value.includes(tag)) {
    tags.value.push(tag)
    tagInput.value = ''
  } else if (tags.value.includes(tag)) {
    ElMessage.warning('标签已存在')
  }
}

// 删除标签
const removeTag = (tag) => {
  const index = tags.value.indexOf(tag)
  if (index > -1) {
    tags.value.splice(index, 1)
  }
}

// 文件变化处理
const handleFileChange = (uploadFile, uploadFiles) => {
  // Element Plus 的 on-change 事件会传递 (uploadFile, uploadFiles)
  // 手动更新 fileList，确保响应式更新
  if (uploadFiles && Array.isArray(uploadFiles) && uploadFiles.length > 0) {
    // 如果超过一个文件，只保留最后一个（因为 limit=1）
    fileList.value = uploadFiles.slice(-1)
  } else if (uploadFile) {
    // 如果只有单个文件对象，包装成数组
    fileList.value = [uploadFile]
  } else {
    // 如果没有文件，清空列表
    fileList.value = []
  }
  
  // 强制触发响应式更新
  fileList.value = [...fileList.value]
  
  // 如果文件是通过点击选择（非拖拽），也需要显示删除对话框
  // 检查是否是点击选择（没有文件句柄和路径信息）
  if (fileList.value.length > 0 && uploadMode.value === 'single') {
    const fileObj = fileList.value[0]
    const file = fileObj.raw || fileObj
    
    // 如果文件对象中没有存储文件句柄信息，说明是通过点击选择的
    if (file && !fileObj._fileHandle && !fileObj._fileEntry && !fileObj._filePath) {
      // 尝试获取文件路径信息
      let filePath = file.name
      if (file.path) {
        filePath = file.path
      }
      
      // 存储文件信息
      sourceFileHandle.value = null // 点击选择无法获取文件句柄
      sourceFilePath.value = filePath
      
      // 延迟显示对话框，确保文件已添加到列表
      setTimeout(() => {
        showDeleteSourceDialog.value = true
        console.log('通过点击选择文件，显示删除原文件对话框')
      }, 100)
    }
  }
}

// 触发文件夹选择
const triggerFolderSelect = () => {
  if (folderInputRef.value) {
    folderInputRef.value.click()
  }
}

// 处理文件夹输入变化
const handleFolderInputChange = (event) => {
  const files = Array.from(event.target.files || [])
  console.log('文件夹输入变化，文件数量:', files.length)
  console.log('文件列表:', files.map(f => ({ name: f.name, path: f.webkitRelativePath })))
  
  if (files.length > 0) {
    // 将 FileList 转换为数组并包装成 Element Plus 需要的格式
    folderFileList.value = files.map(file => ({
      name: file.name,
      size: file.size,
      raw: file,
      webkitRelativePath: file.webkitRelativePath || file.name
    }))
    
    console.log('处理后的文件列表:', folderFileList.value.length, '个文件')
    console.log('文件详情:', folderFileList.value.map(f => f.webkitRelativePath))
    
    // 提取文件夹根路径（第一个文件的 webkitRelativePath 的目录部分）
    if (files[0] && files[0].webkitRelativePath) {
      const firstPath = files[0].webkitRelativePath
      const pathParts = firstPath.split('/')
      if (pathParts.length > 1) {
        folderRootPath.value = pathParts[0]
      }
    }
  } else {
    folderFileList.value = []
    folderRootPath.value = ''
  }
}

// 检测拖拽类型（文件或文件夹）
const detectDragType = async (event) => {
  const items = event.dataTransfer.items
  if (!items || items.length === 0) {
    return { type: 'unknown', isFile: false, isFolder: false }
  }
  
  // 检查第一个条目
  const firstItem = items[0]
  const entry = firstItem.webkitGetAsEntry()
  
  if (entry) {
    if (entry.isFile) {
      return { type: 'file', isFile: true, isFolder: false }
    } else if (entry.isDirectory) {
      return { type: 'folder', isFile: false, isFolder: true }
    }
  }
  
  // 如果没有 webkitGetAsEntry，尝试使用 files
  const files = event.dataTransfer.files
  if (files && files.length > 0) {
    // 检查是否有 webkitRelativePath（文件夹中的文件会有这个属性）
    const hasRelativePath = Array.from(files).some(file => file.webkitRelativePath && file.webkitRelativePath.includes('/'))
    if (hasRelativePath) {
      return { type: 'folder', isFile: false, isFolder: true }
    }
    // 单个文件
    if (files.length === 1) {
      return { type: 'file', isFile: true, isFolder: false }
    }
  }
  
  return { type: 'unknown', isFile: false, isFolder: false }
}

// 处理拖拽进入
const handleDragEnter = (event) => {
  event.preventDefault()
  dragCounter.value++
  isDragging.value = true
}

// 处理拖拽离开
const handleDragLeave = (event) => {
  event.preventDefault()
  dragCounter.value--
  if (dragCounter.value === 0) {
    isDragging.value = false
  }
}

// 处理拖拽悬停
const handleDragOver = (event) => {
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
}

// 处理单文件上传区域的拖拽
const handleSingleFileDrop = async (event) => {
  event.preventDefault()
  event.stopPropagation()
  isDragging.value = false
  dragCounter.value = 0
  
  // 检测拖拽类型
  const dragType = await detectDragType(event)
  
  if (dragType.isFolder) {
    // 拖入的是文件夹，但当前是单文件上传模式
    ElMessageBox.confirm(
      '检测到您拖入的是文件夹，但当前是"单文件上传"模式。是否切换到"文件夹上传"模式？',
      '上传类型不匹配',
      {
        confirmButtonText: '切换到文件夹上传',
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true
      }
    ).then(() => {
      // 切换到文件夹上传模式
      uploadMode.value = 'folder'
      // 延迟处理文件夹拖拽，确保模式切换完成
      setTimeout(() => {
        handleFolderDrop(event)
      }, 100)
    }).catch(() => {
      // 用户取消
      ElMessage.info('已取消，请拖入单个文件')
    })
    return
  }
  
  if (dragType.isFile) {
    // 拖入的是文件，正确处理
    const files = event.dataTransfer.files
    if (files && files.length > 0) {
      // 只取第一个文件
      const file = files[0]
      
      // 尝试获取文件句柄（File System Access API）
      let fileHandle = null
      let filePath = file.name
      let fileEntry = null
      
      // 检查是否支持 File System Access API
      if (event.dataTransfer.items && event.dataTransfer.items.length > 0) {
        const item = event.dataTransfer.items[0]
        
        // 方法1: 尝试使用 getAsFileSystemHandle (File System Access API)
        if (typeof item.getAsFileSystemHandle === 'function') {
          try {
            const handle = await item.getAsFileSystemHandle()
            if (handle && handle.kind === 'file') {
              fileHandle = handle
              filePath = handle.name
              console.log('通过 getAsFileSystemHandle 获取文件句柄成功')
            }
          } catch (error) {
            console.warn('getAsFileSystemHandle 失败:', error)
          }
        }
        
        // 方法2: 尝试使用 webkitGetAsEntry (File System API)
        if (!fileHandle) {
          try {
            const entry = item.webkitGetAsEntry()
            if (entry && entry.isFile) {
              fileEntry = entry
              filePath = entry.fullPath || entry.name || file.name
              console.log('通过 webkitGetAsEntry 获取文件条目成功:', filePath)
            }
          } catch (error) {
            console.warn('webkitGetAsEntry 失败:', error)
          }
        }
      }
      
      // 如果无法获取文件句柄，尝试从文件路径获取
      if (!fileHandle && !fileEntry && file.path) {
        filePath = file.path
        console.log('使用文件路径:', filePath)
      }
      
      // 如果都没有，至少使用文件名
      if (!filePath) {
        filePath = file.name
      }
      
      // 创建符合 Element Plus el-upload 组件格式的文件对象
      const uploadFileObj = {
        name: file.name,
        size: file.size,
        raw: file,
        uid: Date.now(),
        status: 'ready',
        _fileHandle: fileHandle, // 存储文件句柄（File System Access API）
        _fileEntry: fileEntry, // 存储文件条目（File System API）
        _filePath: filePath // 存储文件路径
      }
      
      // 调用 handleFileChange 来确保 el-upload 组件正确更新
      handleFileChange(uploadFileObj, [uploadFileObj])
      
      // 总是显示删除原文件对话框（无论是否有文件句柄）
      // 存储文件信息用于后续删除
      sourceFileHandle.value = fileHandle || fileEntry // 存储文件句柄或条目
      sourceFilePath.value = filePath
      
      console.log('文件信息:', {
        name: file.name,
        size: file.size,
        hasFileHandle: !!fileHandle,
        hasFileEntry: !!fileEntry,
        filePath: filePath
      })
      
      // 延迟显示对话框，确保文件已添加到列表
      setTimeout(() => {
        showDeleteSourceDialog.value = true
        console.log('显示删除原文件对话框')
      }, 100)
    }
  } else {
    ElMessage.warning('无法识别拖拽的内容，请重试')
  }
}

// 处理文件夹拖拽
const handleFolderDrop = async (event) => {
  event.preventDefault()
  event.stopPropagation()
  isDragging.value = false
  dragCounter.value = 0
  
  // 检测拖拽类型
  const dragType = await detectDragType(event)
  
  if (dragType.isFile && !dragType.isFolder) {
    // 拖入的是单个文件，但当前是文件夹上传模式
    ElMessageBox.confirm(
      '检测到您拖入的是单个文件，但当前是"文件夹上传"模式。是否切换到"单文件上传"模式？',
      '上传类型不匹配',
      {
        confirmButtonText: '切换到单文件上传',
        cancelButtonText: '取消',
        type: 'warning',
        distinguishCancelAndClose: true
      }
    ).then(() => {
      // 切换到单文件上传模式
      uploadMode.value = 'single'
      // 延迟处理文件拖拽，确保模式切换完成
      setTimeout(() => {
        handleSingleFileDrop(event)
      }, 100)
    }).catch(() => {
      // 用户取消
      ElMessage.info('已取消，请拖入文件夹')
    })
    return
  }
  
  const items = event.dataTransfer.items
  if (!items || items.length === 0) {
    console.warn('拖拽事件中没有项目')
    ElMessage.warning('请拖拽文件夹，而不是单个文件')
    return
  }
  
  const allFiles = []
  let folderName = ''
  
  // 递归处理目录条目
  const processEntry = async (entry, path = '') => {
    if (entry.isFile) {
      return new Promise((resolve) => {
        entry.file(file => {
          // 构建完整的相对路径
          const relativePath = path ? `${path}/${file.name}` : file.name
          
          // 创建新的 File 对象，设置 webkitRelativePath
          const fileWithPath = new File([file], file.name, {
            type: file.type,
            lastModified: file.lastModified
          })
          
          // 使用 Object.defineProperty 设置 webkitRelativePath
          Object.defineProperty(fileWithPath, 'webkitRelativePath', {
            value: relativePath,
            writable: false,
            enumerable: true,
            configurable: true
          })
          
          allFiles.push(fileWithPath)
          console.log(`添加文件: ${relativePath}`)
          resolve()
        })
      })
    } else if (entry.isDirectory) {
      // 记录文件夹名称（第一个目录）
      if (!folderName) {
        folderName = entry.name
      }
      
      const currentPath = path ? `${path}/${entry.name}` : entry.name
      const reader = entry.createReader()
      
      // 递归读取目录中的所有条目
      return new Promise((resolve) => {
        const allEntries = []
        
        const readDir = () => {
          reader.readEntries((entries) => {
            if (entries.length === 0) {
              // 所有条目都已读取，处理所有条目
              Promise.all(allEntries.map(subEntry => processEntry(subEntry, currentPath)))
                .then(() => resolve())
                .catch((error) => {
                  console.error('处理目录条目失败:', error)
                  resolve()
                })
              return
            }
            
            // 收集所有条目
            allEntries.push(...entries)
            
            // 继续读取（目录可能很大，需要多次读取）
            readDir()
          }, (error) => {
            console.error('读取目录失败:', error)
            // 即使出错，也处理已收集的条目
            Promise.all(allEntries.map(subEntry => processEntry(subEntry, currentPath)))
              .then(() => resolve())
              .catch(() => resolve())
          })
        }
        
        readDir()
      })
    } else {
      return Promise.resolve()
    }
  }
  
  // 处理所有拖拽的条目
  try {
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      const entry = item.webkitGetAsEntry()
      if (entry) {
        await processEntry(entry)
      }
    }
    
    console.log(`拖拽文件夹处理完成，共 ${allFiles.length} 个文件`)
    console.log('文件列表:', allFiles.map(f => f.webkitRelativePath))
    
    if (allFiles.length === 0) {
      ElMessage.warning('文件夹中没有文件')
      return
    }
    
    // 将文件列表转换为需要的格式
    folderFileList.value = allFiles.map(file => ({
      name: file.name,
      size: file.size,
      raw: file,
      webkitRelativePath: file.webkitRelativePath || file.name
    }))
    
    // 设置文件夹根路径
    if (folderName) {
      folderRootPath.value = folderName
    } else if (allFiles[0] && allFiles[0].webkitRelativePath) {
      const firstPath = allFiles[0].webkitRelativePath
      const pathParts = firstPath.split('/')
      if (pathParts.length > 1) {
        folderRootPath.value = pathParts[0]
      }
    }
    
    console.log('处理后的文件列表:', folderFileList.value.length, '个文件')
    ElMessage.success(`已加载 ${folderFileList.value.length} 个文件`)
  } catch (error) {
    console.error('处理拖拽文件夹失败:', error)
    ElMessage.error('处理文件夹失败，请重试')
  }
}

// 获取文件的相对路径
const getFileRelativePath = (file) => {
  if (file.webkitRelativePath) {
    return file.webkitRelativePath
  }
  return file.name
}

// 上传模式切换处理
const handleModeChange = () => {
  // 切换模式时清空文件列表和拖拽状态
  fileList.value = []
  folderFileList.value = []
  folderRootPath.value = ''
  uploadStatus.value = 'idle'
  uploadProgress.value = 0
  isDragging.value = false
  dragCounter.value = 0
  if (uploadRef.value) {
    uploadRef.value.clearFiles()
  }
  if (folderInputRef.value) {
    folderInputRef.value.value = ''
  }
}

// 上传前检查
const beforeUpload = () => {
  if (tags.value.length === 0) {
    ElMessage.warning('请先添加至少一个标签')
    return false
  }
  return true
}

// 上传文件
const handleUpload = async () => {
  if (!canUpload.value) {
    ElMessage.warning('请选择文件并添加标签')
    return
  }

  if (uploadMode.value === 'single') {
    await handleSingleUpload()
  } else {
    await handleFolderUpload()
  }
}

// 单文件上传
const handleSingleUpload = async () => {
  // 确保有文件
  if (!fileList.value || fileList.value.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }

  // 检查文件大小
  const fileObj = fileList.value[0]
  const file = fileObj.raw || fileObj
  
  if (!file) {
    ElMessage.error('文件对象无效')
    return
  }
  
  if (file.size === 0) {
    ElMessage.error('不能上传空文件（0字节）')
    return
  }

  // 重置状态
  uploadStatus.value = 'uploading'
  uploadProgress.value = 0
  uploading.value = true
  validating.value = false
  
  try {
    const formData = new FormData()
    formData.append('file', file)
    
    // 添加所有标签
    tags.value.forEach(tag => {
      formData.append('tags', tag)
    })

    // 上传进度回调
    const onUploadProgress = (progressEvent) => {
      if (progressEvent.total) {
        // 上传进度：0-90%（预留10%给校验阶段）
        const uploadPercent = Math.round((progressEvent.loaded / progressEvent.total) * 90)
        uploadProgress.value = Math.min(uploadPercent, 90)
      } else if (progressEvent.loaded) {
        // 如果没有 total，使用已加载的字节数估算
        uploadProgress.value = Math.min(Math.round((progressEvent.loaded / file.size) * 90), 90)
      }
    }

    // 开始上传
    const uploadPromise = uploadFile(formData, onUploadProgress)
    
    // 上传完成后，进入校验阶段（90-100%）
    uploadPromise.then(() => {
      uploadStatus.value = 'validating'
      uploadProgress.value = 90
      validating.value = true
      uploading.value = false
      
      // 模拟校验进度（实际校验在后端进行）
      let validateProgress = 90
      const validateInterval = setInterval(() => {
        validateProgress = Math.min(validateProgress + 2, 100)
        uploadProgress.value = validateProgress
        
        if (validateProgress >= 100) {
          clearInterval(validateInterval)
        }
      }, 50)
    })

    const result = await uploadPromise
    
    // 清除校验进度定时器（如果还在运行）
    if (validating.value) {
      uploadProgress.value = 100
    }
    
    // 上传成功
    uploadStatus.value = 'success'
    uploadProgress.value = 100
    uploading.value = false
    validating.value = false
    
    ElMessage.success('文件上传成功！')
    
    // 如果用户选择删除原文件，执行删除操作
    if (pendingDeleteAfterUpload.value) {
      await deleteSourceFile()
    }
    
    emit('upload-success')
    
    // 延迟重置表单，让用户看到成功状态
    setTimeout(() => {
      handleReset()
    }, 2000)
    
  } catch (error) {
    uploading.value = false
    validating.value = false
    
    // 检查是否是重复文件错误
    if (error.response?.status === 400 && error.response?.data?.detail) {
      const detail = error.response.data.detail
      
      // 检查是否是新的重复文件错误格式（包含 error_type 和 existing_file）
      if (typeof detail === 'object' && detail.error_type === 'duplicate_file' && detail.existing_file) {
        duplicateFileInfo.value = detail.existing_file
        uploadStatus.value = 'duplicate'
        uploadProgress.value = 0
        duplicateDialogVisible.value = true
        ElMessage.warning('检测到重复文件')
      } 
      // 兼容旧的错误格式（字符串格式）
      else if (typeof detail === 'string' && (detail.includes('already exists') || detail.includes('已存在'))) {
        // 提取文件名
        const match = detail.match(/Existing file: (.+)/)
        duplicateFileInfo.value = {
          original_filename: match ? match[1] : '未知文件',
          file_size: 0,
          upload_time: null,
          tags: [],
          id: null
        }
        uploadStatus.value = 'duplicate'
        uploadProgress.value = 0
        duplicateDialogVisible.value = true
        ElMessage.warning('检测到重复文件')
      } else if (detail.includes('Empty file') || detail.includes('0 bytes')) {
        uploadStatus.value = 'error'
        uploadProgress.value = 0
        ElMessage.error('不能上传空文件（0字节）')
      } else {
        uploadStatus.value = 'error'
        uploadProgress.value = 0
        ElMessage.error(detail || '上传失败')
      }
    } else {
      uploadStatus.value = 'error'
      uploadProgress.value = 0
      ElMessage.error('上传失败，请重试')
    }
  }
}

// 计算 Blob 的 SHA-256 哈希值（用于调试）
const calculateBlobHash = async (blob) => {
  try {
    const arrayBuffer = await blob.arrayBuffer()
    const hashBuffer = await crypto.subtle.digest('SHA-256', arrayBuffer)
    const hashArray = Array.from(new Uint8Array(hashBuffer))
    const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('')
    return hashHex
  } catch (error) {
    console.error('计算哈希值失败:', error)
    return '计算失败'
  }
}

// 压缩文件夹
const compressFolder = async (files) => {
  if (!files || files.length === 0) {
    throw new Error('文件列表为空')
  }
  
  const zip = new JSZip()
  let processedCount = 0
  const totalFiles = files.length
  
  console.log(`开始压缩文件夹，共 ${totalFiles} 个文件`)
  console.log('文件列表详情:', files.map(f => ({
    name: f.name,
    hasRaw: !!f.raw,
    webkitRelativePath: f.webkitRelativePath
  })))
  
  // 更新压缩进度
  const updateProgress = () => {
    const percent = Math.round((processedCount / totalFiles) * 100)
    uploadProgress.value = Math.min(percent, 95) // 压缩阶段最多到95%
  }
  
  // 处理每个文件
  const processFile = async (file) => {
    // 确保获取到原始文件对象
    let fileObj = file.raw || file
    
    // 如果 file 本身是 File 对象，直接使用
    if (file instanceof File) {
      fileObj = file
    }
    
    if (!fileObj) {
      console.warn(`跳过无效文件:`, file)
      processedCount++
      updateProgress()
      return
    }
    
    // 检查是否是 File 对象
    if (!(fileObj instanceof File) && !(fileObj instanceof Blob)) {
      console.warn(`文件对象类型不正确:`, fileObj)
      processedCount++
      updateProgress()
      return
    }
    
    if (fileObj.size === 0) {
      console.warn(`跳过空文件: ${file.name || fileObj.name}`)
      processedCount++
      updateProgress()
      return
    }
    
    try {
      // 获取相对路径 - 优先使用包装对象中的路径，然后是原始文件对象的路径
      let relativePath = file.webkitRelativePath || fileObj.webkitRelativePath || file.name || fileObj.name
      
      // 如果没有相对路径，使用文件名
      if (!relativePath) {
        relativePath = file.name || fileObj.name || `file_${processedCount}`
        console.warn(`文件缺少相对路径，使用文件名: ${relativePath}`)
      }
      
      console.log(`处理文件 [${processedCount + 1}/${totalFiles}]: ${relativePath} (${fileObj.size} bytes)`)
      
      // 读取文件内容并添加到 ZIP
      const fileData = await new Promise((resolveFile, rejectFile) => {
        const reader = new FileReader()
        reader.onload = (e) => {
          resolveFile(e.target.result)
        }
        reader.onerror = (error) => {
          console.error(`读取文件失败: ${relativePath}`, error)
          rejectFile(error)
        }
        reader.readAsArrayBuffer(fileObj)
      })
      
      // 添加到 ZIP（保持文件夹结构）
      // 使用固定的日期，确保相同文件每次压缩时生成相同的 ZIP 条目
      // 这对于查重功能至关重要
      const fixedDate = new Date(0) // 1970-01-01 00:00:00 UTC
      zip.file(relativePath, fileData, {
        date: fixedDate // 使用固定日期，而不是文件的 lastModified
      })
      processedCount++
      updateProgress()
      console.log(`✓ [${processedCount}/${totalFiles}] 已添加文件: ${relativePath}`)
    } catch (error) {
      console.error(`压缩文件失败: ${file.name || '未知文件'}`, error)
      processedCount++
      updateProgress()
    }
  }
  
  // 先对文件进行排序，确保顺序一致（按路径排序）
  // 这样可以保证相同文件夹每次压缩时文件顺序相同
  const sortedFiles = [...files].sort((a, b) => {
    const pathA = a.webkitRelativePath || a.raw?.webkitRelativePath || a.name || ''
    const pathB = b.webkitRelativePath || b.raw?.webkitRelativePath || b.name || ''
    return pathA.localeCompare(pathB)
  })
  
  console.log('文件排序完成，按路径排序:', sortedFiles.map(f => 
    f.webkitRelativePath || f.raw?.webkitRelativePath || f.name
  ))
  
  // 顺序处理文件（避免并发过多导致内存问题）
  for (let i = 0; i < sortedFiles.length; i++) {
    await processFile(sortedFiles[i])
  }
  
  console.log(`所有文件处理完成，共处理 ${processedCount}/${totalFiles} 个文件`)
  
  // 移除 ZIP 中的空文件夹条目，确保 ZIP 文件一致性
  // JSZip 会自动创建文件夹条目，但这些条目可能包含时间戳，影响哈希值
  const fixedDate = new Date(0) // 1970-01-01 00:00:00 UTC
  for (const path in zip.files) {
    const file = zip.files[path]
    // 如果是文件夹条目（以 / 结尾），设置固定日期或移除
    if (file.dir) {
      file.date = fixedDate
      // 确保文件夹条目的所有属性都一致
      file.unixPermissions = 0o755 // 固定权限
      file.dosPermissions = null
    } else {
      // 确保文件条目也使用固定日期
      file.date = fixedDate
      file.unixPermissions = 0o644 // 固定权限
      file.dosPermissions = null
    }
  }
  
  // 检查 ZIP 中的文件数量（排除空文件夹）
  const fileEntries = Object.keys(zip.files).filter(path => !zip.files[path].dir)
  const zipFileCount = fileEntries.length
  console.log(`ZIP 中包含 ${zipFileCount} 个文件（排除文件夹）`)
  console.log('ZIP 文件列表:', fileEntries)
  console.log('ZIP 完整条目列表:', Object.keys(zip.files))
  
  if (zipFileCount === 0) {
    throw new Error('压缩后 ZIP 文件中没有文件，请检查文件列表')
  }
  
  // 生成 ZIP 文件
  // 关键：使用固定的日期（Unix epoch 0），确保相同内容生成相同的 ZIP 文件
  // 这对于查重功能至关重要
  try {
    const blob = await zip.generateAsync({
      type: 'blob',
      compression: 'DEFLATE',
      compressionOptions: { level: 6 }, // 压缩级别 0-9，6 是平衡点
      date: fixedDate, // 使用固定日期，确保相同内容生成相同的 ZIP
      // 确保 ZIP 文件格式一致
      platform: 'UNIX', // 使用 UNIX 平台格式，更稳定
      createFolders: false, // 不创建空文件夹
      streamFiles: false, // 不使用流式处理，确保一致性
      // 确保 ZIP 文件元数据一致
      comment: '', // 空注释
      encodeFileName: (fileName) => fileName // 不编码文件名，保持原样
    })
    
    uploadProgress.value = 100
    console.log(`ZIP 文件生成完成，大小: ${blob.size} bytes`)
    
    // 计算 ZIP 文件的哈希值（用于调试，验证一致性）
    // 注意：实际查重在后端进行，这里只是用于调试
    const zipHash = await calculateBlobHash(blob)
    console.log(`ZIP 文件哈希值（前端计算，用于调试）: ${zipHash}`)
    console.log(`ZIP 文件哈希值前16位: ${zipHash.substring(0, 16)}...`)
    
    return blob
  } catch (error) {
    console.error('生成 ZIP 文件失败:', error)
    throw error
  }
}

// 文件夹上传
const handleFolderUpload = async () => {
  // 确保有文件
  if (!folderFileList.value || folderFileList.value.length === 0) {
    ElMessage.warning('请先选择文件夹')
    return
  }

  // 重置状态
  uploadStatus.value = 'compressing'
  uploadProgress.value = 0
  compressing.value = true
  uploading.value = false
  validating.value = false
  
  try {
    // 第一步：压缩文件夹
    console.log('文件夹文件列表:', folderFileList.value)
    console.log('文件数量:', folderFileList.value.length)
    ElMessage.info(`开始压缩文件夹，共 ${folderFileList.value.length} 个文件...`)
    const zipBlob = await compressFolder(folderFileList.value)
    
    // 获取文件夹名称（用于 ZIP 文件名）
    const folderName = folderRootPath.value || 'folder'
    const zipFileName = `${folderName}.zip`
    
    // 创建 ZIP 文件对象
    const zipFile = new File([zipBlob], zipFileName, { type: 'application/zip' })
    
    compressing.value = false
    uploadStatus.value = 'uploading'
    uploadProgress.value = 0
    uploading.value = true
    
    // 第二步：上传压缩后的 ZIP 文件
    const formData = new FormData()
    formData.append('file', zipFile)
    formData.append('is_folder_archive', 'true') // 标识这是文件夹压缩包
    formData.append('folder_name', folderName) // 文件夹名称
    
    // 添加所有标签
    tags.value.forEach(tag => {
      formData.append('tags', tag)
    })

    // 上传进度回调
    const onUploadProgress = (progressEvent) => {
      if (progressEvent.total) {
        // 上传进度：0-95%（预留5%给校验阶段）
        const uploadPercent = Math.round((progressEvent.loaded / progressEvent.total) * 95)
        uploadProgress.value = Math.min(uploadPercent, 95)
      }
    }

    // 开始上传
    console.log('========== 开始上传文件夹 ==========')
    console.log('文件名:', zipFileName)
    console.log('文件大小:', zipFile.size, 'bytes')
    console.log('文件夹名称:', folderName)
    console.log('标签:', tags.value)
    
    // 调用上传接口（包含查重逻辑）
    // 注意：查重逻辑在后端执行，后端会计算 ZIP 文件的 SHA-256 哈希值并查询数据库
    console.log('发送上传请求到后端，后端将执行查重...')
    const result = await uploadFile(formData, onUploadProgress)
    console.log('上传请求完成，响应:', result)
    
    // 上传完成，进入校验阶段
    uploadStatus.value = 'validating'
    uploadProgress.value = 95
    validating.value = true
    uploading.value = false
    
    // 模拟校验过程（实际校验在后端完成）
    await new Promise(resolve => setTimeout(resolve, 500))
    
    // 上传成功
    uploadStatus.value = 'success'
    uploadProgress.value = 100
    uploading.value = false
    validating.value = false
    
    ElMessage.success(`文件夹上传成功！已压缩为 ${zipFileName}`)
    
    // 如果用户选择删除原文件，执行删除操作
    if (pendingDeleteAfterUpload.value) {
      await deleteSourceFile()
    }
    
    emit('upload-success')
    
    // 延迟重置表单，让用户看到成功状态
    setTimeout(() => {
      handleReset()
    }, 2000)
    
  } catch (error) {
    compressing.value = false
    uploading.value = false
    validating.value = false
    
    console.error('文件夹上传错误:', error)
    console.error('错误响应:', error.response)
    console.error('错误状态码:', error.response?.status)
    console.error('错误数据:', error.response?.data)
    console.error('错误详情类型:', typeof error.response?.data?.detail)
    
    // 检查是否是重复文件错误
    // 注意：由于响应拦截器可能转换了错误，需要检查多种可能的格式
    let detail = null
    
    // 优先从 error.response.data.detail 获取（FastAPI 标准格式）
    if (error.response?.data) {
      if (error.response.data.detail) {
        detail = error.response.data.detail
      } else if (typeof error.response.data === 'object') {
        // 如果 data 本身就是 detail 对象
        detail = error.response.data
      } else if (typeof error.response.data === 'string') {
        try {
          detail = JSON.parse(error.response.data)
        } catch {
          detail = error.response.data
        }
      }
    }
    
    console.log('解析后的错误详情:', detail)
    console.log('错误详情类型:', typeof detail)
    console.log('是否为重复文件错误:', detail?.error_type === 'duplicate_file')
    
    // 检查是否是重复文件错误（HTTP 400 状态码）
    console.log('========== 错误处理 ==========')
    console.log('错误状态码:', error.response?.status)
    console.log('错误详情:', detail)
    console.log('错误详情类型:', typeof detail)
    console.log('是否为重复文件错误:', detail?.error_type === 'duplicate_file')
    
    if (error.response?.status === 400 && detail) {
      // 检查新的错误格式（对象格式，包含 error_type 和 existing_file）
      if (typeof detail === 'object' && detail.error_type === 'duplicate_file') {
        console.log('✅ 检测到重复文件错误！')
        console.log('已存在文件信息:', detail.existing_file)
        duplicateFileInfo.value = detail.existing_file || {
          original_filename: detail.message || '未知文件',
          file_size: 0,
          sha256_hash: '',
          upload_time: null,
          tags: []
        }
        uploadStatus.value = 'duplicate'
        uploadProgress.value = 0
        duplicateDialogVisible.value = true
        ElMessage.warning('检测到重复的文件夹压缩包，系统已存在相同内容的压缩文件')
        console.log('已显示重复文件对话框，阻止上传')
        return // 提前返回，不显示其他错误
      } 
      // 检查字符串格式的错误
      else if (typeof detail === 'string') {
        if (detail.includes('已存在') || detail.includes('already exists') || detail.includes('duplicate')) {
          console.log('检测到重复文件错误（字符串格式）')
          uploadStatus.value = 'duplicate'
          uploadProgress.value = 0
          duplicateDialogVisible.value = true
          ElMessage.warning('检测到重复的文件夹压缩包')
          return
        } else {
          ElMessage.error(detail)
        }
      }
      // 如果 detail 是对象但没有 error_type，尝试其他方式判断
      else if (typeof detail === 'object') {
        if (detail.message && (detail.message.includes('已存在') || detail.message.includes('duplicate'))) {
          console.log('检测到重复文件错误（通过 message 字段）')
          uploadStatus.value = 'duplicate'
          uploadProgress.value = 0
          duplicateDialogVisible.value = true
          ElMessage.warning('检测到重复的文件夹压缩包')
          return
        } else if (detail.message) {
          ElMessage.error(detail.message)
        } else {
          ElMessage.error('上传失败')
        }
      }
    } else {
      // 其他错误
      const errorMsg = error.response?.data?.detail || error.message || '上传失败，请重试'
      console.error('其他错误:', errorMsg)
      ElMessage.error(errorMsg)
    }
    
    uploadStatus.value = 'error'
    uploadProgress.value = 0
  }
}

// 处理取消删除原文件
const handleCancelDeleteSource = () => {
  showDeleteSourceDialog.value = false
  sourceFileHandle.value = null
  sourceFilePath.value = ''
  pendingDeleteAfterUpload.value = false
}

// 处理确认删除原文件
const handleConfirmDeleteSource = () => {
  pendingDeleteAfterUpload.value = true
  showDeleteSourceDialog.value = false
  ElMessage.success('已标记：文件上传成功后将删除原文件')
  console.log('用户选择删除原文件，已标记待删除:', sourceFilePath.value)
}

// 删除原路径文件
const deleteSourceFile = async () => {
  if (!pendingDeleteAfterUpload.value) {
    return
  }
  
  console.log('开始删除原文件:', {
    hasHandle: !!sourceFileHandle.value,
    filePath: sourceFilePath.value,
    handleType: sourceFileHandle.value?.constructor?.name
  })
  
  if (sourceFileHandle.value) {
    try {
      const handle = sourceFileHandle.value
      
      // 方法1: 尝试使用 File System Access API (FileSystemFileHandle)
      if (handle.kind === 'file' && typeof handle.remove === 'function') {
        try {
          await handle.remove()
          ElMessage.success('原文件已删除')
          console.log('✅ 通过 File System Access API 删除成功:', sourceFilePath.value)
          // 清理状态
          sourceFileHandle.value = null
          sourceFilePath.value = ''
          pendingDeleteAfterUpload.value = false
          return
        } catch (error) {
          console.warn('File System Access API 删除失败:', error)
        }
      }
      
      // 方法2: 尝试通过父目录删除 (File System Access API)
      if (handle.kind === 'file' && typeof handle.getParent === 'function') {
        try {
          const parentHandle = await handle.getParent()
          if (parentHandle && typeof parentHandle.removeEntry === 'function') {
            await parentHandle.removeEntry(handle.name, { recursive: false })
            ElMessage.success('原文件已删除')
            console.log('✅ 通过父目录删除成功:', sourceFilePath.value)
            // 清理状态
            sourceFileHandle.value = null
            sourceFilePath.value = ''
            pendingDeleteAfterUpload.value = false
            return
          }
        } catch (error) {
          console.warn('通过父目录删除失败:', error)
        }
      }
      
      // 方法3: 尝试使用 File System API (FileEntry)
      // 注意：FileEntry 的 remove 方法需要回调函数
      if (handle.isFile) {
        try {
          // FileEntry.remove() 使用回调函数
          await new Promise((resolve, reject) => {
            if (typeof handle.remove === 'function') {
              handle.remove(resolve, reject)
            } else {
              reject(new Error('FileEntry.remove 方法不存在'))
            }
          })
          ElMessage.success('原文件已删除')
          console.log('✅ 通过 File System API 删除成功:', sourceFilePath.value)
          // 清理状态
          sourceFileHandle.value = null
          sourceFilePath.value = ''
          pendingDeleteAfterUpload.value = false
          return
        } catch (error) {
          console.warn('File System API 删除失败:', error)
          // 继续尝试其他方法
        }
      }
      
      // 如果所有方法都失败，提示用户手动删除
      console.warn('所有自动删除方法都失败，提示用户手动删除')
      ElMessage.warning({
        message: `无法自动删除原文件，请手动删除: ${sourceFilePath.value}`,
        duration: 8000,
        showClose: true
      })
      
    } catch (error) {
      console.error('删除原文件时发生错误:', error)
      ElMessage.warning({
        message: `删除原文件失败，请手动删除: ${sourceFilePath.value || '未知路径'}`,
        duration: 8000,
        showClose: true
      })
    } finally {
      // 清理状态
      sourceFileHandle.value = null
      sourceFilePath.value = ''
      pendingDeleteAfterUpload.value = false
    }
  } else {
    // 没有文件句柄，提示用户手动删除
    console.warn('没有文件句柄，提示用户手动删除')
    if (sourceFilePath.value) {
      ElMessage.info({
        message: `文件上传成功！请手动删除原文件: ${sourceFilePath.value}`,
        duration: 5000,
        showClose: true
      })
    }
    sourceFilePath.value = ''
    pendingDeleteAfterUpload.value = false
  }
}

// 关闭重复文件对话框
const handleCloseDuplicateDialog = () => {
  duplicateDialogVisible.value = false
  duplicateFileInfo.value = null
  uploadStatus.value = 'idle'
  uploadProgress.value = 0
}

// 跳转到重复文件
const handleJumpToFile = () => {
  if (duplicateFileInfo.value) {
    duplicateDialogVisible.value = false
    uploadStatus.value = 'idle'
    uploadProgress.value = 0
    // 传递文件名或文件ID，让父组件处理跳转
    emit('jump-to-file', duplicateFileInfo.value.original_filename || duplicateFileInfo.value.id)
  }
}

// 格式化文件大小
const formatFileSize = (bytes) => {
  if (!bytes || bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

// 格式化日期时间
const formatDateTime = (dateTimeStr) => {
  if (!dateTimeStr) return '未知'
  try {
    const date = new Date(dateTimeStr)
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  } catch (e) {
    return dateTimeStr
  }
}

// 重置表单
const handleReset = () => {
  tags.value = []
  tagInput.value = ''
  fileList.value = []
  folderFileList.value = []
  folderRootPath.value = ''
  uploadStatus.value = 'idle'
  uploadProgress.value = 0
  uploading.value = false
  validating.value = false
  compressing.value = false
  sourceFileHandle.value = null
  sourceFilePath.value = ''
  pendingDeleteAfterUpload.value = false
  showDeleteSourceDialog.value = false
  if (uploadRef.value) {
    uploadRef.value.clearFiles()
  }
  if (folderInputRef.value) {
    folderInputRef.value.value = ''
  }
}

// 组件挂载时加载标签
onMounted(() => {
  loadAllTags()
})
</script>

<style scoped>
.file-upload-container {
  margin-bottom: 24px;
}

.upload-card {
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.upload-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.upload-card :deep(.el-card__body) {
  padding: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
}

.card-header .el-icon {
  color: #2c3e50;
  font-size: 18px;
}

.tag-input-section {
  margin-bottom: 24px;
  padding: 20px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.tag-input-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 14px;
  font-weight: 500;
  color: #34495e;
}

.tag-input-label .el-icon {
  color: #2c3e50;
}

.tag-input-wrapper {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.tag-item {
  margin: 0;
  font-size: 13px;
  padding: 6px 12px;
  border-radius: 4px;
}

.tag-input {
  width: 300px;
}

.tag-input :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #e4e7ed inset;
  border-radius: 6px;
}

.tag-input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #c0c4cc inset;
}

.tag-input :deep(.el-input.is-focus .el-input__wrapper) {
  box-shadow: 0 0 0 1px #1e88e5 inset;
}

.tag-suggestion {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tag-name {
  font-weight: 500;
  color: #2c3e50;
}

.tag-count {
  color: #7f8c8d;
  font-size: 12px;
}

/* 自动完成下拉框样式 */
:deep(.tag-autocomplete-popper) {
  max-height: 300px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  border-radius: 6px;
}

.tag-hint {
  margin-top: 10px;
}

.tag-hint :deep(.el-text) {
  color: #7f8c8d;
  font-size: 12px;
}

.upload-dragger {
  width: 100%;
  margin-bottom: 20px;
}

.upload-dragger :deep(.el-upload-dragger) {
  border: 2px dashed #d0d7de;
  border-radius: 6px;
  background: #fafbfc;
  transition: all 0.2s ease;
}

.upload-dragger :deep(.el-upload-dragger:hover) {
  border-color: #1e88e5;
  background: #f8f9fa;
}

.upload-dragger :deep(.el-icon--upload) {
  color: #95a5a6;
  font-size: 56px;
}

.upload-dragger :deep(.el-upload__text) {
  color: #34495e;
  font-size: 14px;
}

.upload-dragger :deep(.el-upload__text em) {
  color: #1e88e5;
  font-style: normal;
  font-weight: 500;
}

.upload-dragger :deep(.el-upload__tip) {
  color: #7f8c8d;
  font-size: 12px;
}

.upload-status-section {
  margin: 20px 0;
  padding: 18px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.status-info {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.status-icon {
  font-size: 20px;
}

.status-icon.uploading {
  color: #1e88e5;
  animation: rotate 1s linear infinite;
}

.status-icon.compressing {
  color: #2e7d32;
  animation: rotate 1s linear infinite;
}

.status-icon.validating {
  color: #f57c00;
  animation: pulse 1.5s ease-in-out infinite;
}

.status-icon.success {
  color: #2e7d32;
}

.status-icon.duplicate {
  color: #f57c00;
}

.status-icon.error {
  color: #c62828;
}

.status-text {
  font-size: 14px;
  font-weight: 500;
  color: #34495e;
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.upload-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 24px;
}

.upload-actions :deep(.el-button) {
  padding: 10px 24px;
  font-size: 14px;
  font-weight: 500;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.upload-actions :deep(.el-button--primary) {
  background: #2c3e50;
  border-color: #2c3e50;
}

.upload-actions :deep(.el-button--primary:hover) {
  background: #34495e;
  border-color: #34495e;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(44, 62, 80, 0.2);
}

.duplicate-info {
  margin: 20px 0;
}

.file-details {
  margin-top: 20px;
}

.file-name {
  font-weight: 600;
  color: #1e88e5;
  word-break: break-all;
  font-size: 14px;
}

.upload-status {
  margin-top: 12px;
  padding: 12px 16px;
  background: #f0f4f8;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  text-align: center;
  color: #34495e;
  font-size: 13px;
}

.upload-mode-section {
  margin-bottom: 24px;
  padding: 18px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.upload-mode-section :deep(.el-radio-group) {
  display: flex;
  gap: 0;
}

.upload-mode-section :deep(.el-radio-button__inner) {
  padding: 10px 20px;
  border-radius: 0;
  font-size: 14px;
  font-weight: 500;
}

.upload-mode-section :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-radius: 6px 0 0 6px;
}

.upload-mode-section :deep(.el-radio-button:last-child .el-radio-button__inner) {
  border-radius: 0 6px 6px 0;
}

.upload-mode-section :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: #2c3e50;
  border-color: #2c3e50;
  color: #ffffff;
}

.folder-upload-area {
  width: 100%;
}

.folder-upload-dragger {
  width: 100%;
  padding: 50px 40px;
  border: 2px dashed #d0d7de;
  border-radius: 6px;
  text-align: center;
  cursor: pointer;
  background-color: #fafbfc;
  transition: all 0.2s ease;
}

.folder-upload-dragger.drag-over {
  border-color: #1e88e5;
  background-color: #f0f4f8;
}

.folder-upload-dragger:hover {
  border-color: #1e88e5;
  background-color: #f8f9fa;
}

.folder-upload-dragger .el-icon--upload {
  font-size: 64px;
  color: #95a5a6;
  margin-bottom: 20px;
}

.folder-upload-dragger .el-upload__text {
  color: #34495e;
  font-size: 14px;
  margin-bottom: 10px;
  font-weight: 500;
}

.folder-upload-dragger .el-upload__text em {
  color: #1e88e5;
  font-style: normal;
  font-weight: 600;
}

.folder-upload-dragger .el-upload__tip {
  color: #7f8c8d;
  font-size: 12px;
}

.folder-preview {
  margin-top: 20px;
}

.folder-file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.folder-file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.folder-file-item .file-path {
  flex: 1;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  color: #34495e;
  word-break: break-all;
}

.delete-source-info {
  padding: 12px 0;
}

.file-path-display {
  margin: 16px 0;
  padding: 12px 16px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  word-break: break-all;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  color: #34495e;
}
</style>
