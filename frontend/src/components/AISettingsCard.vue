<template>
  <el-card class="settings-card ai-settings-card" shadow="hover" v-loading="loading">
    <template #header><div class="ai-card-header"><span>AI 服务</span><el-tag size="small" :type="saved.has_api_key ? 'success' : 'info'">{{ saved.has_api_key ? '已配置密钥' : '未配置' }}</el-tag></div></template>
    <p class="ai-help">供伙伴对话、资料分析与辅助整理使用。伙伴按会话中选择的范围读取资料；待整理页可为选中批次启动 AI 预整理，或主动开启当前资料库的自动预整理。配置密钥不会自动发送资料，整理方案仍由你确认后入库。</p>
    <p class="ai-help">伙伴与预整理需要模型支持工具调用，阅读图像还需要图像理解能力。修改服务或模型后，已开启的自动预整理会暂停，需要在待整理页重新开启。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="ai-alert" />
    <el-form label-position="top" :disabled="saving || testing || loading" @submit.prevent="save">
      <el-form-item label="服务地址"><el-input v-model="form.base_url" placeholder="https://api.deepseek.com" autocomplete="off" data-testid="ai-base-url" /></el-form-item>
      <el-form-item label="模型"><el-input v-model="form.model" placeholder="deepseek-flash" autocomplete="off" data-testid="ai-model" /></el-form-item>
      <el-form-item label="API 密钥">
        <el-input v-model="form.api_key" type="password" autocomplete="new-password" :placeholder="saved.has_api_key ? '已保存；留空保留，输入新密钥可替换' : '输入服务商提供的 API 密钥'" data-testid="ai-api-key" />
      </el-form-item>
      <p class="ai-key-storage">{{ keyStorageLabel }}</p>
      <div class="ai-settings-actions">
        <el-button type="primary" native-type="submit" :loading="saving" data-testid="ai-settings-save">保存 AI 设置</el-button>
        <el-button :loading="testing && testingMode === 'connection'" :disabled="!saved.has_api_key || dirty" @click="testConnection" data-testid="ai-settings-test">测试连接</el-button>
        <el-button :loading="testing && testingMode === 'vision'" :disabled="!saved.has_api_key || dirty" @click="testVision" data-testid="ai-settings-test-vision">测试图像理解</el-button>
        <el-button v-if="saved.has_api_key" type="danger" text @click="clearKey">清除密钥</el-button>
      </div>
      <p class="ai-help">测试连接会发送一条简短测试消息，可能产生少量费用，不发送文件内容。修改配置后请先保存。</p>
      <p class="ai-help">测试图像理解会向当前 AI 服务发送一张临时生成的合成测试图，可能产生少量费用，不发送任何个人资料。结果只说明本次测试表现。</p>
      <p v-if="testMessage" role="status" class="ai-test-result">{{ testMessage }}</p>
      <p v-if="visionMessage" role="status" data-testid="ai-settings-vision-result" class="ai-test-result" :class="{ 'ai-test-unverified': visionPassed === false }">{{ visionMessage }}</p>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { getAISettings, updateAISettings, testAIConnection, testAIVision } from '@/api/ai'
import { buildAISettingsUpdate } from '@/utils/aiAssist'

const appStore = useAppStore()
const form = reactive({ base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: '' })
const saved = ref({ has_api_key: false, base_url: '', model: '', key_storage: 'memory', can_persist_key: false })
const loading = ref(false), saving = ref(false), testing = ref(false), error = ref(''), testMessage = ref('')
const testingMode = ref(''), visionMessage = ref(''), visionPassed = ref(null)
let generation = 0, controller
const dirty = computed(() => form.base_url !== saved.value.base_url || form.model !== saved.value.model || Boolean(form.api_key))
const keyStorageLabel = computed(() => {
  if (saved.value.key_storage === 'environment') return '当前使用运行环境提供的密钥，输入框不会显示已有密钥。'
  if (saved.value.can_persist_key) return '密钥由本机系统钥匙串保存，不会写入资料库或在页面回显。'
  return '此运行环境的密钥仅在本次服务会话中保留；重启后需要重新填写。'
})
function resetRequest() { generation++; controller?.abort(); controller = new AbortController(); return generation }
function clearTestResults() { testMessage.value = ''; visionMessage.value = ''; visionPassed.value = null }
function applySettings(value) { saved.value = value; form.base_url = value.base_url; form.model = value.model; form.api_key = '' }
async function load() {
  const current = resetRequest(); testing.value = false; testingMode.value = ''; form.api_key = ''; loading.value = true; error.value = ''; clearTestResults(); saving.value = false
  try { const value = await getAISettings(controller.signal); if (current === generation) applySettings(value) }
  catch (e) { if (current === generation) error.value = e.message || '无法读取 AI 设置，请重新打开设置页' }
  finally { if (current === generation) loading.value = false }
}
async function save() {
  if (saving.value || testing.value || loading.value) return
  let payload
  try { payload = buildAISettingsUpdate(form, saved.value) } catch (e) { error.value = e.message; return }
  const current = resetRequest(); saving.value = true; error.value = ''; clearTestResults(); form.api_key = ''
  try { const value = await updateAISettings(payload, controller.signal); if (current === generation) { applySettings(value); ElMessage.success('AI 设置已保存') } }
  catch (e) { if (current === generation) error.value = e.message || 'AI 设置保存失败，请重试' }
  finally { payload.api_key = undefined; if (current === generation) saving.value = false }
}
async function clearKey() {
  if (saving.value || testing.value || loading.value) return
  const originalGeneration = generation
  try { await ElMessageBox.confirm('清除后将无法调用 AI，已有资料和标签保持不变。', '清除 AI 密钥', { confirmButtonText: '清除密钥', cancelButtonText: '取消' }) } catch { return }
  if (originalGeneration !== generation) return
  const current = resetRequest(); saving.value = true; error.value = ''; clearTestResults(); form.api_key = ''
  try { const value = await updateAISettings(buildAISettingsUpdate({ ...saved.value, api_key: '' }, saved.value, true), controller.signal); if (current === generation) { applySettings(value); ElMessage.success('AI 密钥已清除') } }
  catch (e) { if (current === generation) error.value = e.message || '密钥清除失败' }
  finally { if (current === generation) saving.value = false }
}
async function runTest(mode) {
  if (testing.value || saving.value || loading.value || dirty.value || !saved.value.has_api_key) return
  const current = resetRequest(); testing.value = true; testingMode.value = mode; error.value = ''
  if (mode === 'vision') { visionMessage.value = ''; visionPassed.value = null } else testMessage.value = ''
  try {
    const value = await (mode === 'vision' ? testAIVision(controller.signal) : testAIConnection(controller.signal))
    if (current !== generation) return
    if (mode === 'vision') {
      visionPassed.value = value.ok === true
      visionMessage.value = value.message || '本次测试未取得可验证的图像识别结果，请核对配置后重试'
    } else testMessage.value = value.message || '连接成功'
  }
  catch (e) { if (current === generation) error.value = e.message || '测试失败，请核对设置后重试' }
  finally { if (current === generation) { testing.value = false; testingMode.value = '' } }
}
const testConnection = () => runTest('connection')
const testVision = () => runTest('vision')
watch(() => [form.base_url, form.model, form.api_key], () => {
  clearTestResults()
  if (testing.value) { resetRequest(); testing.value = false; testingMode.value = '' }
}, { flush: 'sync' })
watch(() => appStore.libraryPath, load)
onMounted(load)
onUnmounted(() => { generation++; controller?.abort(); testing.value = false; form.api_key = '' })
</script>

<style scoped>
.ai-card-header { display:flex; align-items:center; justify-content:space-between; gap:12px; font-weight:600; }
.ai-help,.ai-key-storage { color:#697586; font-size:13px; line-height:1.7; margin:0 0 16px; }
.ai-settings-card :deep(.el-form) { max-width:650px; }
.ai-settings-actions { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 12px; }
.ai-settings-actions :deep(.el-button + .el-button) { margin-left:0; }
.ai-alert { margin-bottom:16px; }
.ai-test-result { color:#248149; margin-bottom:0; }
.ai-test-unverified { color:#936314; }
</style>
