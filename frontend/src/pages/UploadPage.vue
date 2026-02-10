<template>
  <div class="upload-page">
    <FileUpload
      @upload-success="handleUploadSuccess"
      @jump-to-file="handleJumpToFile"
    />
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import FileUpload from '../components/FileUpload.vue'

const router = useRouter()

// 处理上传成功
const handleUploadSuccess = () => {
  ElMessage.success('文件上传成功！正在跳转到文件列表...')
  // 延迟跳转，让用户看到成功提示
  setTimeout(() => {
    router.push('/files')
  }, 1500)
}

// 处理跳转到文件
const handleJumpToFile = (fileName) => {
  // 跳转到文件列表页并搜索该文件
  router.push({
    path: '/files',
    query: { search: fileName }
  })
  ElMessage.info(`正在搜索文件: ${fileName}`)
}
</script>

<style scoped>
.upload-page {
  width: 100%;
}
</style>
