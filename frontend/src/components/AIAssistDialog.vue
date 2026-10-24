<template>
  <el-dialog :model-value="modelValue" @update:model-value="emit('update:modelValue', $event)"
    title="AI 辅助整理" width="min(720px, 94vw)" append-to-body destroy-on-close :close-on-click-modal="false">
    <div class="ai-assist" data-testid="ai-assist-dialog" v-loading="preparing">
      <p class="ai-file-name">{{ prepared?.document_name || document?.name }}</p>
      <p class="ai-help">AI 建议供你参考，核对后再采用。摘要不会写入原文件或覆盖资料描述。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" class="ai-notice" />
      <div v-if="notConfigured" class="ai-config-guide">
        <p>请先在设置中配置 AI 服务和 API 密钥。</p>
        <el-button @click="openSettings">前往设置</el-button>
      </div>
      <template v-if="prepared">
        <div class="ai-section-heading"><h4>将发送的内容</h4><span>{{ prepared.text.length }} 字</span></div>
        <p class="ai-help">服务：{{ service?.base_url }} · 模型：{{ service?.model }}</p>
        <el-input :model-value="prepared.text" type="textarea" readonly :rows="7" aria-label="将发送给 AI 的资料摘录" data-testid="ai-source-text" />
        <p class="ai-help">将发送上述摘录和资料名称；不会发送原始文件。{{ prepared.truncated ? '内容较长，本次只分析摘录，结果可能不完整。' : '' }}</p>
        <ul v-if="notes.length" class="ai-notes"><li v-for="note in notes" :key="note">{{ note }}</li></ul>
        <template v-if="!result">
          <el-checkbox v-model="consent" :disabled="analyzing" class="ai-consent" data-testid="ai-send-consent">允许将以上内容和资料名称发送给所配 AI 服务</el-checkbox>
          <p class="ai-help">调用可能产生费用。请勿发送未经许可外发的敏感内容。</p>
          <el-button type="primary" :disabled="!consent || analyzing" :loading="analyzing" @click="analyze" data-testid="ai-analyze">{{ analyzing ? '正在分析…' : '生成标签建议与摘要' }}</el-button>
        </template>
        <div v-if="result" class="ai-result" data-testid="ai-result">
          <h4>简短摘要</h4><p class="ai-summary" data-testid="ai-summary">{{ result.summary }}</p>
          <div class="ai-section-heading"><h4>建议标签</h4><span>选择你认可的标签</span></div>
          <el-checkbox-group v-if="candidates.length" v-model="selected" class="ai-tag-choices">
            <el-checkbox v-for="tag in candidates" :key="tag" :value="tag" border>{{ tag }}</el-checkbox>
          </el-checkbox-group>
          <p v-else class="ai-help">本次未生成可用标签，可参考摘要自行补充。</p>
          <p class="ai-help">已有标签会保留。填入后仍需在原页面确认保存。</p>
        </div>
      </template>
      <el-button v-if="!prepared && !preparing && !notConfigured" @click="prepare">重新读取资料</el-button>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ analyzing ? '取消等待并关闭' : '关闭' }}</el-button>
      <el-button v-if="result" type="primary" :disabled="!selected.length" @click="apply" data-testid="ai-apply-tags">{{ applyLabel }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { getAISettings, prepareAIDocument, analyzeAIDocument } from '@/api/ai'
import { createAIRequestScope, normalizeSuggestedTags } from '@/utils/aiAssist'

const props = defineProps({ modelValue: Boolean, document: Object, applyLabel: { type: String, default: '填入标签编辑区' } })
const emit = defineEmits(['update:modelValue', 'apply'])
const router = useRouter(), appStore = useAppStore()
const prepared = ref(null), result = ref(null), service = ref(null), consent = ref(false), selected = ref([])
const preparing = ref(false), analyzing = ref(false), error = ref(''), notConfigured = ref(false)
const scope = createAIRequestScope()
let currentToken, controller
const candidates = computed(() => normalizeSuggestedTags(result.value?.tags))
const notes = computed(() => [...new Set([...(prepared.value?.notes || []), ...(result.value?.notes || [])])])
function current(token) { return props.modelValue && scope.isCurrent(token, props.document?.id, appStore.libraryPath) }
function clear() {
  scope.invalidate(); controller?.abort(); prepared.value = null; result.value = null; service.value = null
  consent.value = false; selected.value = []; error.value = ''; preparing.value = false; analyzing.value = false; notConfigured.value = false
}
async function prepare() {
  clear()
  if (!props.modelValue || !props.document?.id) return
  const token = currentToken = scope.begin(props.document.id, appStore.libraryPath)
  controller = new AbortController(); preparing.value = true
  try {
    const settings = await getAISettings(controller.signal)
    if (!current(token)) return
    if (!settings.has_api_key) { notConfigured.value = true; return }
    const value = await prepareAIDocument(token.documentId, controller.signal)
    if (!current(token)) return
    if (value.document_id !== token.documentId || !value.preparation_id || typeof value.text !== 'string' || !value.content_hash) throw new Error('资料准备响应不一致，请重新读取')
    if (typeof value.service?.base_url !== 'string' || !value.service.base_url || typeof value.service.model !== 'string' || !value.service.model) throw new Error('资料准备缺少绑定的 AI 服务，请重新读取')
    service.value = { base_url: value.service.base_url, model: value.service.model }
    prepared.value = value
  } catch (e) { if (current(token)) error.value = e.message || '资料内容读取失败，请重试' }
  finally { if (current(token)) preparing.value = false }
}
async function analyze() {
  if (!consent.value || !prepared.value || analyzing.value || !current(currentToken)) return
  const token = currentToken, preparationId = prepared.value.preparation_id
  analyzing.value = true; error.value = ''
  try {
    const value = await analyzeAIDocument(preparationId, controller.signal)
    if (!current(token) || prepared.value?.preparation_id !== preparationId) return
    if (value.preparation_id !== preparationId || value.document_id !== token.documentId || value.content_hash !== prepared.value.content_hash) throw new Error('分析结果与本次资料不一致，请重新读取')
    result.value = value; selected.value = []
  } catch (e) {
    if (!current(token)) return
    error.value = e.message || 'AI 分析失败，请重试'
    prepared.value = null; consent.value = false
  } finally { if (current(token)) analyzing.value = false }
}
function apply() {
  if (!result.value || !selected.value.length || !current(currentToken)) return
  emit('apply', { documentId: currentToken.documentId, libraryPath: currentToken.libraryPath, candidates: candidates.value, selected: selected.value.slice() })
  emit('update:modelValue', false)
}
function openSettings() { emit('update:modelValue', false); router.push('/settings') }
watch(() => [props.modelValue, props.document?.id, appStore.libraryPath], ([visible, id, library], previous) => {
  if (visible && previous?.[0] && (id !== previous[1] || library !== previous[2])) {
    clear(); emit('update:modelValue', false); return
  }
  if (visible) prepare(); else clear()
}, { immediate: true, flush: 'sync' })
onUnmounted(clear)
</script>

<style scoped>
.ai-file-name { color:#24364b; font-size:16px; font-weight:600; overflow-wrap:anywhere; margin:0 0 8px; }
.ai-help { color:#697586; font-size:13px; line-height:1.7; margin:8px 0 14px; overflow-wrap:anywhere; }
.ai-notice { margin:12px 0; }
.ai-section-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.ai-section-heading span { color:#8791a0; font-size:12px; }
h4 { color:#34465b; font-size:14px; margin:16px 0 10px; }
.ai-notes { padding-left:20px; color:#8a6425; font-size:13px; line-height:1.7; }
.ai-consent { height:auto; align-items:flex-start; margin-top:8px; }
.ai-consent :deep(.el-checkbox__label) { white-space:normal; line-height:1.6; }
.ai-consent :deep(.el-checkbox__input) { margin-top:4px; }
.ai-result { border-top:1px solid #e7ecf1; margin-top:20px; }
.ai-summary { white-space:pre-wrap; line-height:1.8; color:#40546b; background:#f5f8fc; border-radius:8px; padding:14px; overflow-wrap:anywhere; }
.ai-tag-choices { display:flex; flex-wrap:wrap; gap:10px; }
.ai-tag-choices :deep(.el-checkbox) { margin:0; }
.ai-config-guide { padding:16px 0; }
</style>
