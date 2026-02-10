<template>
  <div class="file-list-page">
    <!-- 文件搜索组件 -->
    <FileSearch @search="handleSearch" ref="searchRef" />

    <!-- 批量操作栏 -->
    <BatchOperation
      v-if="selectedFiles.length > 0"
      :selected-files="selectedFiles"
      @clear-selection="handleClearSelection"
      @refresh="loadFiles"
    />

    <!-- 文件列表组件 -->
    <FileList
      :files="fileList"
      :loading="loading"
      :total="total"
      @download="handleDownload"
      @delete="handleDelete"
      @refresh="loadFiles"
      @selection-change="handleSelectionChange"
      ref="fileListRef"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import FileSearch from '../components/FileSearch.vue'
import FileList from '../components/FileList.vue'
import BatchOperation from '../components/BatchOperation.vue'
import { searchFiles, downloadFile, deleteFile } from '../api/file'

const route = useRoute()
const searchRef = ref(null)
const fileListRef = ref(null)

// 文件列表数据
const fileList = ref([])
const loading = ref(false)
const total = ref(0)

// 选中的文件列表
const selectedFiles = ref([])

// 当前搜索条件
const currentSearchParams = ref({
  keywords: null,
  tags: null
})

// 加载文件列表
const loadFiles = async (params = null) => {
  loading.value = true
  try {
    const searchParams = params || currentSearchParams.value
    const result = await searchFiles(searchParams)
    fileList.value = result.files || []
    total.value = result.total || 0
  } catch (error) {
    console.error('加载文件列表失败:', error)
    ElMessage.error('加载文件列表失败')
  } finally {
    loading.value = false
  }
}

// 处理搜索
const handleSearch = (params) => {
  currentSearchParams.value = params
  loadFiles(params)
}

// 处理选择变化
const handleSelectionChange = (selection) => {
  selectedFiles.value = selection
}

// 清除选择
const handleClearSelection = () => {
  selectedFiles.value = []
  // 清除表格中的选择
  if (fileListRef.value && fileListRef.value.clearSelection) {
    fileListRef.value.clearSelection()
  }
}

// 处理下载
const handleDownload = async (file) => {
  try {
    await downloadFile(file.id, file.original_filename)
    ElMessage.success('下载成功')
  } catch (error) {
    console.error('下载失败:', error)
    // 错误信息已在 request.js 的拦截器中显示
  }
}

// 处理删除
const handleDelete = async (file) => {
  // 删除逻辑已在 FileList 组件中实现，这里只需要刷新列表
  loadFiles()
}

// 监听路由查询参数，支持从上传页跳转过来时自动搜索
watch(() => route.query.search, (searchValue) => {
  if (searchValue) {
    handleSearch({
      keywords: [searchValue],
      tags: null
    })
  }
}, { immediate: true })

// 监听标签查询参数，支持从标签管理页跳转过来
watch(() => route.query.tag, (tagValue) => {
  if (tagValue) {
    handleSearch({
      keywords: null,
      tags: [tagValue]
    })
  }
}, { immediate: true })

// 组件挂载时加载文件列表
onMounted(() => {
  // 如果有查询参数，使用查询参数搜索
  if (route.query.search) {
    handleSearch({
      keywords: [route.query.search],
      tags: null
    })
  } else {
    loadFiles()
  }
  
  // 重新加载标签（从上传页跳转过来时）
  if (searchRef.value) {
    searchRef.value.loadTags()
  }
})
</script>

<style scoped>
.file-list-page {
  width: 100%;
}
</style>
