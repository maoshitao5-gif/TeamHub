<template>
  <el-dialog
    v-model="visible"
    title="解决同步冲突"
    width="680px"
    :close-on-click-modal="false"
    @open="loadConflicts"
  >
    <div v-if="loading" v-loading="true" style="min-height: 120px;" />

    <div v-else-if="conflicts.length === 0" class="no-conflicts">
      <el-icon :size="40" color="#67c23a"><CircleCheck /></el-icon>
      <p>没有待解决的冲突</p>
    </div>

    <div v-else class="conflict-list">
      <div
        v-for="item in conflicts"
        :key="item.id"
        class="conflict-item"
      >
        <div class="conflict-header">
          <el-icon color="#e6a23c"><Warning /></el-icon>
          <span class="conflict-name">{{ item.name }}</span>
        </div>

        <!-- 本地 vs 远端对比 -->
        <div class="conflict-compare">
          <div class="compare-col local-col">
            <div class="compare-label">本地</div>
            <div class="compare-detail">
              <span>版本数：{{ item.local_version_count }}</span>
              <span>更新：{{ formatDate(item.local_updated_at) }}</span>
            </div>
          </div>
          <div class="compare-divider">VS</div>
          <div
            v-for="remote in item.remote_snapshots"
            :key="remote.device_id"
            class="compare-col remote-col"
          >
            <div class="compare-label">远端（{{ remote.device_id.slice(0, 8) }}…）</div>
            <div class="compare-detail">
              <span>版本数：{{ remote.version_count }}</span>
              <span>更新：{{ formatDate(remote.updated_at) }}</span>
            </div>
          </div>
          <div v-if="item.remote_snapshots.length === 0" class="compare-col remote-col">
            <div class="compare-label">远端</div>
            <div class="compare-detail"><span>暂无快照信息</span></div>
          </div>
        </div>

        <!-- 解决按钮 -->
        <div class="conflict-actions">
          <el-button size="small" @click="resolve(item, 'keep_local')">
            保留本地
          </el-button>
          <el-button
            size="small"
            type="primary"
            @click="resolve(item, 'use_remote')"
          >
            使用远端
          </el-button>
          <el-button
            size="small"
            type="warning"
            @click="resolve(item, 'keep_both')"
          >
            两个都保留
          </el-button>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Warning, CircleCheck } from '@element-plus/icons-vue'
import { listSyncConflicts, resolveSyncConflict } from '@/api/sync'

const props = defineProps({
  modelValue: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue', 'resolved'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const conflicts = ref([])

const loadConflicts = async () => {
  loading.value = true
  try {
    conflicts.value = await listSyncConflicts()
  } catch (e) {
    ElMessage.error('加载冲突列表失败')
  } finally {
    loading.value = false
  }
}

const resolve = async (item, choice) => {
  try {
    await resolveSyncConflict(item.id, choice)
    const labels = { keep_local: '已保留本地版本', use_remote: '已使用远端版本', keep_both: '已保留双方版本' }
    ElMessage.success(labels[choice] || '冲突已解决')
    // 从列表中移除
    conflicts.value = conflicts.value.filter(c => c.id !== item.id)
    emit('resolved')
  } catch (e) {
    ElMessage.error(e.message || '解决冲突失败')
  }
}

const formatDate = (dateStr) => {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.no-conflicts {
  text-align: center;
  padding: 32px;
  color: #67c23a;
}
.no-conflicts p {
  margin-top: 12px;
  font-size: 15px;
  color: #606266;
}

.conflict-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 480px;
  overflow-y: auto;
}

.conflict-item {
  border: 1px solid #faecd8;
  border-radius: 8px;
  padding: 14px 16px;
  background: #fffbf5;
}

.conflict-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.conflict-name {
  font-size: 14px;
  font-weight: 600;
  color: #2c3e50;
}

.conflict-compare {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}

.compare-col {
  flex: 1;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12px;
}

.local-col {
  background: #f0f9eb;
  border: 1px solid #b3e19d;
}

.remote-col {
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
}

.compare-label {
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.compare-detail {
  display: flex;
  flex-direction: column;
  gap: 3px;
  color: #606266;
}

.compare-divider {
  font-size: 11px;
  color: #aab2bd;
  font-weight: 700;
  flex-shrink: 0;
}

.conflict-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
