<template>
  <div class="settings-page">
    <div class="page-header">
      <h2>设置</h2>
    </div>

    <el-card class="settings-card" shadow="hover" v-loading="loading">
      <template #header>
        <div class="card-header">
          <el-icon><Setting /></el-icon>
          <span>基本设置</span>
        </div>
      </template>

      <el-form label-width="140px" label-position="left" class="settings-form">
        <el-form-item label="文件库路径">
          <el-input :model-value="appStore.libraryPath || '未设置'" disabled />
        </el-form-item>

        <el-form-item label="默认存储方式">
          <el-radio-group v-model="form.default_storage_mode">
            <el-radio value="move">移动</el-radio>
            <el-radio value="copy">复制</el-radio>
            <el-radio value="index">仅索引</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="最大版本数">
          <el-input-number v-model="form.max_versions" :min="0" :max="999" />
          <span class="form-tip">0 表示不限制</span>
        </el-form-item>

        <el-form-item label="版本保留天数">
          <el-input-number v-model="form.max_version_age_days" :min="0" :max="9999" />
          <span class="form-tip">0 表示永久保留</span>
        </el-form-item>

        <el-form-item label="回收站自动清理">
          <el-input-number v-model="form.trash_auto_clean_days" :min="0" :max="9999" />
          <span class="form-tip">天后自动清理，0 表示不自动清理</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleSave" :loading="saving">保存设置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="settings-card" shadow="hover" style="margin-top: 24px;">
      <template #header>
        <div class="card-header">
          <el-icon><Refresh /></el-icon>
          <span>维护操作</span>
        </div>
      </template>

      <div class="maintenance-section">
        <div class="maintenance-item">
          <div class="maintenance-info">
            <div class="maintenance-title">扫描文件库</div>
            <div class="maintenance-desc">检查所有文档的物理文件是否存在，将缺失的文件标记为"丢失"状态。</div>
          </div>
          <el-button @click="handleScan" :loading="scanning">开始扫描</el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { scanLibrary } from '@/api/settings'
import { ElMessage } from 'element-plus'
import { Setting, Refresh } from '@element-plus/icons-vue'

const appStore = useAppStore()
const loading = ref(false)
const saving = ref(false)
const scanning = ref(false)

const form = ref({
  default_storage_mode: 'move',
  max_versions: 0,
  max_version_age_days: 0,
  trash_auto_clean_days: 30,
})

const loadSettings = async () => {
  loading.value = true
  try {
    await appStore.fetchSettings()
    form.value = {
      default_storage_mode: appStore.settings.default_storage_mode || 'move',
      max_versions: appStore.settings.max_versions || 0,
      max_version_age_days: appStore.settings.max_version_age_days || 0,
      trash_auto_clean_days: appStore.settings.trash_auto_clean_days ?? 30,
    }
  } catch (e) {
    ElMessage.error('加载设置失败')
  } finally {
    loading.value = false
  }
}

const handleSave = async () => {
  saving.value = true
  try {
    await appStore.updateSettings(form.value)
    ElMessage.success('设置已保存')
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const handleScan = async () => {
  scanning.value = true
  try {
    const result = await scanLibrary()
    ElMessage.success(`扫描完成：共检查 ${result.scanned} 个文档，${result.missing} 个文件缺失`)
  } catch (e) {
    ElMessage.error(e.message || '扫描失败')
  } finally {
    scanning.value = false
  }
}

onMounted(() => {
  loadSettings()
})
</script>

<style scoped>
.settings-page {
  width: 100%;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h2 {
  font-size: 22px;
  color: #2c3e50;
}

.settings-card {
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.settings-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.settings-card :deep(.el-card__body) {
  padding: 24px;
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

.settings-form {
  max-width: 600px;
}

.form-tip {
  margin-left: 12px;
  font-size: 13px;
  color: #95a5a6;
}

.maintenance-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.maintenance-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  background: #fafbfc;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
}

.maintenance-info {
  flex: 1;
}

.maintenance-title {
  font-size: 15px;
  font-weight: 500;
  color: #2c3e50;
  margin-bottom: 4px;
}

.maintenance-desc {
  font-size: 13px;
  color: #95a5a6;
}
</style>
