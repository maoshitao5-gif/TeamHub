<template>
  <section class="preorganize-panel" aria-label="AI 预整理">
    <div class="preorganize-heading">
      <div><h3>让 AI 先整理，你来做决定</h3><p>阅读图片、PDF、办公文档与成套资料，准备名称、标签和分类建议。</p></div>
      <div class="preorganize-auto">
        <div><span>自动预整理当前资料库</span><small>{{ config?.policy?.active ? '包括以后新收录的资料' : config?.policy?.enabled ? '已暂停，重新开启以继续' : '默认关闭，手工整理始终可用' }}</small></div>
        <el-switch :model-value="Boolean(config?.policy?.active)" :disabled="busy || !config"
          aria-label="自动预整理当前资料库" data-testid="preorganize-auto-toggle" @change="changePolicy" />
      </div>
    </div>
    <div v-if="config?.policy?.paused_reason" class="preorganize-paused" role="status">
      {{ config.policy.paused_reason }}
      <el-button v-if="config.policy.enabled" link type="primary" :disabled="busy" @click="changePolicy(false)">关闭自动预整理</el-button>
    </div>
    <div class="preorganize-toolbar">
      <div class="preorganize-filters" aria-label="AI 预整理状态筛选">
        <button type="button" :class="{ active: !filter }" :aria-pressed="!filter" data-testid="preorganize-filter-all" @click="emit('update:filter', '')">全部<span>{{ totalCount }}</span></button>
        <button v-for="state in PREORGANIZE_STATES" :key="state.value" type="button" :class="[{ active: filter === state.value }, state.value]"
          :aria-pressed="filter === state.value" :data-testid="`preorganize-filter-${state.value}`" @click="emit('update:filter', state.value)">
          {{ state.label }}<span>{{ counts[state.value] || 0 }}</span>
        </button>
      </div>
      <div class="preorganize-batch-actions">
        <el-button :disabled="!startable.length || busy" data-testid="preorganize-start-selected" @click="start(startable)">AI 预整理所选</el-button>
        <el-button type="primary" :disabled="!reviewable.length || busy" data-testid="preorganize-review-selected" @click="review(reviewable)">确认方案<span v-if="reviewable.length">（{{ reviewable.length }}）</span></el-button>
        <el-button v-if="cancelable.length" :disabled="busy" @click="cancel(cancelable)">取消预整理</el-button>
        <el-button link :disabled="busy" @click="openOperations" data-testid="preorganize-operations">最近整理</el-button>
      </div>
    </div>
    <div v-if="config && !config.configured" class="preorganize-hint">尚未配置 AI 服务，可先手工整理。<el-button link type="primary" @click="router.push('/settings')">前往设置</el-button></div>
    <el-alert v-if="loadError || actionError" :title="actionError || loadError" type="error" :closable="false" class="preorganize-error">
      <el-button link type="primary" @click="refresh">刷新状态</el-button>
      <el-button link type="primary" @click="router.push('/settings')">AI 设置</el-button>
    </el-alert>
    <div v-if="batchFailures.length" class="preorganize-errors" role="status"><p v-for="failure in batchFailures" :key="failure.document_id">{{ failure.name }}：{{ failure.error }}</p></div>
  </section>

  <el-dialog v-model="showConsent" :title="consentMode === 'auto' ? '开启当前资料库的自动预整理' : '开始 AI 预整理'"
    width="560px" class="preorganize-consent" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @closed="clearConsent">
    <div class="consent-service"><span>目标 AI 服务</span><strong>{{ consentConfig?.service?.model }}</strong><small>{{ consentConfig?.service?.base_url }}</small></div>
    <p class="consent-description">{{ consentMode === 'auto' ? '处理当前资料库中尚未生成方案的待整理资料，以及以后新收录的资料。可随时关闭，失败任务不会自动反复重试。' : `本次处理选中的 ${consentDocuments.length} 项资料，不会开启整个资料库的自动处理。` }}</p>
    <ul class="consent-details">
      <li>读取所选资料的已保存版本，可能向该服务发送文字、图像及资料名称。</li>
      <li v-if="consentIncludesFolders">文件夹按整套资料处理：阅读内部文件及相对路径，建议整套资料的名称、标签和分类，保留内部文件名和目录结构。</li>
      <li v-if="consentIncludesArchives">压缩包按整套资料处理：按需读取包内文件及相对路径，保留压缩格式、内部文件名和目录结构。</li>
      <li>图片和扫描件通过图像理解；文档、表格与演示文稿结合文字、表格和必要图像理解。无法识别的部分会在方案中说明。</li>
      <li>已有标签和分类目录名称会作为整理语境发送，帮助复用你的分类习惯。</li>
      <li>可能产生模型费用。只生成方案，完成整理仍由你最后确认。</li>
    </ul>
    <el-alert v-if="consentError" :title="consentError" type="error" :closable="false" />
    <template #footer>
      <el-button :disabled="busy" @click="showConsent = false">取消</el-button>
      <el-button type="primary" :loading="busy" data-testid="preorganize-consent-confirm" @click="confirmConsent">{{ consentMode === 'auto' ? '允许并开启' : '允许并开始' }}</el-button>
    </template>
  </el-dialog>

  <PreorganizeReviewDialog v-model="showReview" :documents="reviewDocuments" :library-token="config?.library_token || ''" :tags="tags" @applied="onApplied" />
  <el-dialog v-model="showOperations" title="最近整理" width="620px" class="preorganize-operations-dialog"
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
    <p class="operations-help">撤销会将资料移回待整理，并恢复此次整理前的名称、标签和位置。已有后续修改时会保留修改并说明原因。</p>
    <el-alert v-if="operationError" :title="operationError" type="error" :closable="false" class="preorganize-error" />
    <p v-if="!operations.length" class="operations-empty">还没有采用 AI 方案的记录</p>
    <div v-for="operation in operations" :key="operation.id" class="operation-row">
      <div><strong>{{ operation.document_name }}</strong><small>{{ operation.undone_at ? '已撤销整理' : '已进入文档库' }}</small></div>
      <el-button v-if="!operation.undone_at" :disabled="busy" :loading="undoing === operation.id" :data-testid="`preorganize-undo-${operation.id}`" @click="undo(operation)">撤销整理</el-button>
      <el-tag v-else type="info" size="small">已撤销</el-tag>
    </div>
    <template #footer><el-button :disabled="busy" @click="showOperations = false">关闭</el-button></template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { getPreorganizeConfig, startPreorganize, setPreorganizePolicy, cancelPreorganize, listPreorganizeOperations, undoPreorganize } from '@/api/preorganize'
import { PREORGANIZE_STATES, isArchiveName } from '@/utils/preorganize'
import PreorganizeReviewDialog from '@/components/PreorganizeReviewDialog.vue'

const props = defineProps({
  selectedDocuments: { type: Array, default: () => [] }, counts: { type: Object, default: () => ({}) },
  filter: { type: String, default: '' }, tags: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:filter', 'refresh'])
const appStore = useAppStore(), router = useRouter()
const config = ref(null), loadError = ref(''), actionError = ref(''), batchFailures = ref([]), busy = ref(false)
const showConsent = ref(false), consentMode = ref('batch'), consentConfig = ref(null), consentDocuments = ref([]), consentError = ref('')
const consentIncludesFolders = computed(() => consentMode.value === 'auto' || consentDocuments.value.some(doc => doc.is_folder))
// 名称只用于提前说明发送范围，实际对象类型与可读格式由后端保存版本决定。
const consentIncludesArchives = computed(() => consentMode.value === 'auto' || consentDocuments.value.some(doc => doc.object_kind === 'archive' || isArchiveName(doc.name, doc.is_folder)))
const showReview = ref(false), reviewDocuments = ref([])
const showOperations = ref(false), operations = ref([]), operationError = ref(''), undoing = ref('')
const totalCount = computed(() => PREORGANIZE_STATES.reduce((sum, state) => sum + (props.counts[state.value] || 0), 0))
const startable = computed(() => props.selectedDocuments.filter(doc => ['waiting', 'failed'].includes(doc.preorganize?.state || 'waiting')))
const reviewable = computed(() => props.selectedDocuments.filter(doc => doc.preorganize?.state === 'ready'))
const cancelable = computed(() => props.selectedDocuments.filter(doc => doc.preorganize?.state === 'processing'))
let generation = 0, readSequence = 0, operationSequence = 0, controller = new AbortController(), operationController, timer, consentRequestId = ''
function clearConsent() { if (!showConsent.value) { consentDocuments.value = []; consentConfig.value = null; consentError.value = ''; consentRequestId = '' } }
function reset() {
  generation++; readSequence++; operationSequence++; controller.abort(); operationController?.abort(); controller = new AbortController(); clearTimeout(timer)
  config.value = null; busy.value = false; loadError.value = ''; actionError.value = ''; batchFailures.value = []
  showConsent.value = false; clearConsent(); showReview.value = false; reviewDocuments.value = []
  showOperations.value = false; operations.value = []; operationError.value = ''; undoing.value = ''
}
async function refresh() {
  const current = generation, sequence = ++readSequence
  clearTimeout(timer)
  try {
    const value = await getPreorganizeConfig(controller.signal)
    if (current !== generation || sequence !== readSequence) return null
    config.value = value; loadError.value = ''
    return value
  } catch (e) { if (current === generation && sequence === readSequence) loadError.value = e.message || 'AI 预整理状态暂时不可用，仍可手工整理'; return null }
  finally { if (current === generation && sequence === readSequence) timer = setTimeout(refresh, 15000) }
}
async function prepareConsent(mode, documents = []) {
  if (busy.value) return
  if (mode === 'batch' && (!documents.length || documents.length > 100)) { actionError.value = '请一次选择 1 至 100 项资料'; return }
  const current = generation
  const snapshot = documents.map(doc => ({ id: doc.id, name: doc.name, is_folder: Boolean(doc.is_folder), object_kind: doc.object_kind || '' }))
  busy.value = true; actionError.value = ''; batchFailures.value = []
  const latest = await refresh()
  if (current !== generation) return
  busy.value = false
  if (!latest) return
  if (!latest.configured) { actionError.value = '请先在设置中配置可用的 AI 服务'; return }
  consentConfig.value = { library_token: latest.library_token, service_revision: latest.service_revision, service: { ...latest.service } }
  consentMode.value = mode; consentDocuments.value = snapshot; consentRequestId = crypto.randomUUID(); consentError.value = ''; showConsent.value = true
}
const start = documents => prepareConsent('batch', documents)
function failures(items, documents) {
  const names = new Map(documents.map(doc => [doc.id, doc.name]))
  batchFailures.value = (items || []).filter(item => item.error).map(item => ({ ...item, name: names.get(item.document_id) || '资料' }))
}
async function confirmConsent() {
  if (busy.value || !showConsent.value || !consentConfig.value) return
  const current = generation, authorized = consentConfig.value
  readSequence++; clearTimeout(timer)
  busy.value = true; consentError.value = ''
  try {
    const common = { library_token: authorized.library_token, service_revision: authorized.service_revision, consent: true }
    if (consentMode.value === 'auto') {
      const result = await setPreorganizePolicy({ ...common, enabled: true }, controller.signal)
      if (current !== generation) return
      config.value = result
      ElMessage.success('已开启自动预整理，方案仍需你确认后入库')
    } else {
      const result = await startPreorganize({ ...common, request_id: consentRequestId, document_ids: consentDocuments.value.map(doc => doc.id) }, controller.signal)
      if (current !== generation) return
      failures(result.items, consentDocuments.value)
      if (!batchFailures.value.length) ElMessage.success('已加入 AI 预整理队列，可继续使用 TeamHub')
    }
    showConsent.value = false; emit('refresh')
  } catch (e) { if (current === generation) consentError.value = e.message || '启动失败，请核对设置后重试' }
  finally { if (current === generation) { busy.value = false; refresh() } }
}
async function changePolicy(enabled) {
  if (enabled) return prepareConsent('auto')
  if (busy.value || !config.value) return
  const current = generation, token = config.value.library_token
  readSequence++; clearTimeout(timer)
  busy.value = true; actionError.value = ''
  try {
    const result = await setPreorganizePolicy({ library_token: token, enabled: false }, controller.signal)
    if (current !== generation) return
    config.value = result; emit('refresh'); ElMessage.success('已关闭自动预整理')
  } catch (e) { if (current === generation) actionError.value = e.message || '关闭失败，请重试' }
  finally { if (current === generation) { busy.value = false; refresh() } }
}
async function cancel(documents) {
  if (busy.value || !config.value || !documents.length) return
  const current = generation
  busy.value = true; actionError.value = ''; batchFailures.value = []
  try {
    const result = await cancelPreorganize({ library_token: config.value.library_token, document_ids: documents.map(doc => doc.id) }, controller.signal)
    if (current !== generation) return
    failures(result.items, documents); emit('refresh')
  } catch (e) { if (current === generation) actionError.value = e.message || '取消失败，请重试' }
  finally { if (current === generation) busy.value = false }
}
async function review(documents) {
  if (busy.value || !documents.length) return
  const current = generation
  const snapshot = documents.map(doc => ({ id: doc.id, name: doc.name, is_folder: doc.is_folder }))
  const latest = config.value || await refresh()
  if (current !== generation || !latest) return
  reviewDocuments.value = snapshot; showReview.value = true
}
async function openOperations() {
  const current = generation, sequence = ++operationSequence
  operationController?.abort(); operationController = new AbortController()
  showOperations.value = true; operationError.value = ''
  try {
    const result = await listPreorganizeOperations(operationController.signal)
    if (current === generation && sequence === operationSequence && showOperations.value) operations.value = result.items || []
  } catch (e) { if (current === generation && sequence === operationSequence && showOperations.value) operationError.value = e.message || '无法加载最近整理记录' }
}
async function undo(operation) {
  if (busy.value || !config.value) return
  const current = generation
  busy.value = true; undoing.value = operation.id; operationError.value = ''
  try {
    await undoPreorganize(operation.id, { library_token: config.value.library_token }, controller.signal)
    if (current !== generation) return
    ElMessage.success('已撤销整理，资料已返回待整理'); emit('refresh'); await openOperations()
  } catch (e) { if (current === generation) operationError.value = e.message || '无法撤销，请检查资料是否已有后续修改' }
  finally { if (current === generation) { busy.value = false; undoing.value = '' } }
}
function onApplied() { emit('refresh'); if (showOperations.value) openOperations() }
watch(() => appStore.libraryPath, () => { reset(); refresh() }, { flush: 'sync' })
watch(showOperations, visible => { if (!visible) { operationSequence++; operationController?.abort() } }, { flush: 'sync' })
onMounted(refresh)
onUnmounted(reset)
defineExpose({ start, review, cancel, refresh })
</script>

<style scoped>
.preorganize-panel { margin-bottom: 18px; border: 1px solid #dce7f2; border-radius: 14px; background: linear-gradient(120deg, #f5f9ff, #fff 65%); overflow: hidden; }
.preorganize-heading { display: flex; align-items: center; justify-content: space-between; gap: 22px; padding: 20px 22px 16px; }
.preorganize-heading h3 { margin: 0; color: #2d4662; font-size: 16px; font-weight: 650; }.preorganize-heading p { margin: 7px 0 0; color: #73849a; font-size: 12px; line-height: 1.7; }
.preorganize-auto { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }.preorganize-auto span { font-size: 13px; color: #4f637a; }.preorganize-auto small { display: block; margin-top: 5px; color: #8693a4; font-size: 11px; }
.preorganize-toolbar { padding: 0 20px 16px; }.preorganize-filters { display: flex; flex-wrap: wrap; gap: 6px; border-bottom: 1px solid #e9eef5; padding-bottom: 14px; }
.preorganize-filters button { border: 1px solid transparent; border-radius: 7px; padding: 8px 10px; background: transparent; cursor: pointer; color: #708197; font: inherit; font-size: 12px; }
.preorganize-filters button:hover { background: #edf4fc; }.preorganize-filters button.active { color: #2468b4; background: #e8f2ff; border-color: #cfe2ff; }.preorganize-filters button.ready.active { color: #2a7759; background: #eaf7ef; border-color: #d3ebdf; }.preorganize-filters button.failed.active { color: #b24a43; background: #fff0ee; border-color: #f6dcd7; }
.preorganize-filters span { margin-left: 7px; opacity: .8; font-variant-numeric: tabular-nums; }
.preorganize-batch-actions { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 14px; }.preorganize-batch-actions :deep(.el-button + .el-button) { margin-left: 0; }
.preorganize-hint,.preorganize-paused { margin: 0 22px 14px; color: #8c713b; font-size: 12px; line-height: 1.7; }.preorganize-error { margin: 0 20px 16px; width: auto; }.preorganize-errors { margin: 0 20px 16px; font-size: 12px; color: #ae4e47; }
.consent-service { padding: 16px 18px; background: #f2f6fc; border-radius: 10px; display: flex; flex-direction: column; gap: 6px; color: #506781; overflow-wrap: anywhere; }.consent-service span,.consent-service small { font-size: 12px; }.consent-service strong { font-size: 16px; color: #2b4461; }
.consent-description,.consent-details,.operations-help { color: #63748a; font-size: 13px; line-height: 1.9; }.consent-description { margin: 18px 0 10px; }.consent-details { padding-left: 20px; margin-bottom: 20px; }
.operations-empty { color: #8b98a7; text-align: center; padding: 24px; }.operation-row { display: flex; gap: 16px; justify-content: space-between; align-items: center; padding: 16px 0; border-bottom: 1px solid #edf1f5; }.operation-row>div { min-width: 0; }.operation-row strong { color: #42596f; font-size: 13px; overflow-wrap: anywhere; }.operation-row small { display: block; color: #91a0ae; margin-top: 6px; }
:global(.preorganize-consent), :global(.preorganize-operations-dialog) { max-width: calc(100vw - 32px); }
@media (max-width: 900px) { .preorganize-heading { flex-direction: column; align-items: flex-start; }.preorganize-auto { width: 100%; justify-content: space-between; } }
</style>
