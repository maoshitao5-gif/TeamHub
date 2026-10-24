<template>
  <section class="document-index-card" data-testid="document-index-card" aria-label="资料内容索引">
    <div class="index-heading">
      <h4>资料内容索引</h4>
      <el-tag size="small" :type="stateType">{{ stateLabel }}</el-tag>
    </div>
    <p class="index-help">索引基于当前已保存版本{{ index?.version_number ? ` v${index.version_number}` : '' }}，不包含工作副本中未保存的修改。</p>
    <p class="index-help">本地索引保存可搜索的原文，在后台提取，不影响整理。AI 对资料的理解请查看“AI 资料卡”。</p>
    <p v-if="index?.state === 'failed' && !index?.unit_count" class="index-help">图片、压缩包等资料可通过 AI 资料卡理解内容；未提取到本地原文，不代表 AI 无法整理。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="index-notice" />
    <p v-if="index?.overview" class="index-overview">{{ index.overview }}</p>
    <p v-if="index?.coverage" class="index-help">
      已处理 {{ index.coverage.processed }} / {{ index.coverage.total }} {{ coverageUnit }}
      <span v-if="index.coverage.failed">，{{ index.coverage.failed }} {{ coverageUnit }}未提取</span>
      · {{ index.unit_count || 0 }} 条可搜索原文
    </p>
    <p v-if="index?.state === 'partial'" class="index-gap">部分内容尚不可搜索，请结合原文件核对。</p>
    <p v-if="publicationProgress" class="index-help" aria-live="polite">{{ publicationProgress }}</p>
    <ul v-if="index?.notes?.length" class="index-notes"><li v-for="(note, position) in index.notes" :key="position">{{ note }}</li></ul>
    <ul v-if="index?.gaps?.length" class="index-notes">
      <li v-for="(gap, position) in index.gaps.slice(0, 8)" :key="position">{{ locationLabel(gap.location) }}：{{ gapReason(gap.reason) }}</li>
      <li v-if="index.gaps.length > 8">另有 {{ index.gaps.length - 8 }} 条缺口记录，请结合原文件查看。</li>
    </ul>
    <div v-if="index?.ai" class="index-ai-summary">
      <h5>已有 AI 检索线索</h5>
      <p class="index-help">本次 AI 参考了 {{ index.ai.processed_units }} / {{ index.unit_count }} 条已提取原文单元。</p>
      <p>{{ index.ai.overview }}</p>
      <div v-if="clueTerms.length" class="index-clues"><el-tag v-for="term in clueTerms" :key="term" size="small" type="info">{{ term }}</el-tag></div>
      <p class="index-help">仅用于查找资料，可能有误；人工标签和原文件保持不变。</p>
    </div>
    <div class="index-actions">
      <el-button size="small" :loading="loading" :disabled="rebuilding" @click="refresh">刷新状态</el-button>
      <el-button size="small" :loading="rebuilding" :disabled="loading || processing || enriching" @click="rebuild">{{ index?.state === 'failed' ? '重试本地索引' : '重建本地索引' }}</el-button>
      <el-button v-if="index?.units?.length" text size="small" @click="showUnits = !showUnits">{{ showUnits ? '收起原文' : '查看原文单元' }}</el-button>
    </div>
    <div v-if="showUnits && index?.units?.length" class="index-units">
      <p class="index-help">显示前 {{ index.units.length }} / {{ index.unit_count }} 条原文单元</p>
      <article v-for="unit in index.units" :key="unit.id" class="index-unit">
        <strong>{{ unit.label || locationLabel(unit.location) }}</strong>
        <p v-if="unit.context" class="index-help">{{ unit.context }}</p>
        <pre>{{ unit.text }}</pre>
      </article>
    </div>
    <p v-if="document?.status !== 'organized'" class="index-help">原文索引与 AI 资料卡可以在待整理阶段建立，无需等待完成整理。</p>
    <details v-else class="index-ai-action">
      <summary>原文摘录补充工具</summary>
      <p class="index-help">保留原有的摘录检索线索功能。需要内容概览、导航和人工补充时，请使用 AI 资料卡。</p>
      <el-button v-if="!showAI" size="small" plain :disabled="!canEnrich" @click="openAI" data-testid="index-ai-open">补充摘录检索线索</el-button>
      <template v-else>
        <div class="index-heading"><h5>AI 补充检索线索</h5><el-button text size="small" @click="closeAI">{{ enriching ? '取消等待并收起' : '收起' }}</el-button></div>
        <p v-if="preparing" class="index-help">正在准备本地摘录…</p>
        <el-alert v-if="aiError" :title="aiError" type="error" :closable="false" class="index-notice" />
        <div v-if="notConfigured"><p class="index-help">请先在设置中配置 AI 服务和 API 密钥。</p><el-button size="small" @click="openSettings">前往设置</el-button></div>
        <template v-if="prepared">
          <p class="index-help">将发送《{{ prepared.document_name }}》及以下原文摘录和位置标记。服务：{{ service.base_url }} · 模型：{{ service.model }}</p>
          <el-input :model-value="prepared.text" type="textarea" readonly :rows="7" aria-label="将发送给 AI 的索引摘录" data-testid="index-ai-source" />
          <p v-if="prepared.truncated" class="index-gap">本次仅发送部分摘录，AI 线索不代表完整资料。</p>
          <ul v-if="prepared.notes?.length" class="index-notes"><li v-for="(note, position) in prepared.notes" :key="position">{{ note }}</li></ul>
          <el-checkbox v-model="consent" :disabled="enriching" class="index-consent" data-testid="index-ai-consent">允许将以上摘录、位置标记和资料名称发送给所示 AI 服务</el-checkbox>
          <p class="index-help">调用可能产生费用。补充结果仅用于检索，不修改人工标签和原文件。</p>
          <el-button type="primary" size="small" :loading="enriching" :disabled="!consent || enriching" @click="enrich" data-testid="index-ai-enrich">生成检索线索</el-button>
        </template>
        <el-alert v-if="aiDone" title="AI 检索线索已保存" type="success" :closable="false" />
        <el-button v-if="!prepared && !preparing && !notConfigured && !aiDone" size="small" :disabled="!canEnrich" @click="prepare">重新读取摘录</el-button>
      </template>
    </details>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { getAISettings } from '@/api/ai'
import { getDocumentIndex, rebuildDocumentIndex, prepareIndexEnrichment, enrichDocumentIndex } from '@/api/indexing'

const props = defineProps({ visible: Boolean, document: Object })
const appStore = useAppStore(), router = useRouter()
const index = ref(null), loading = ref(false), rebuilding = ref(false), error = ref(''), showUnits = ref(false)
const showAI = ref(false), prepared = ref(null), service = ref(null), consent = ref(false)
const preparing = ref(false), enriching = ref(false), aiError = ref(''), notConfigured = ref(false), aiDone = ref(false)
let generation = 0, aiGeneration = 0, pollTimer, statusController, aiController
const processing = computed(() => ['queued', 'processing'].includes(index.value?.state))
const publicationProgress = computed(() => {
  const progress = index.value?.processing_progress
  if (progress?.stage !== 'publish') return ''
  return `已建立 ${progress.cursor} / ${progress.total_units} 条可搜索原文，尚未全部发布；处理中断后将继续未完成部分。`
})
const canEnrich = computed(() => props.document?.status === 'organized' && ['ready', 'partial'].includes(index.value?.state) && index.value?.unit_count > 0 && !rebuilding.value)
const stateLabel = computed(() => ({ queued: '等待提取', processing: '正在提取', ready: '已建立', partial: '部分可搜索', failed: '未提取原文' })[index.value?.state] || (loading.value ? '正在读取' : '待建立'))
const stateType = computed(() => ({ ready: 'success', partial: 'warning', failed: 'danger' })[index.value?.state] || 'info')
const coverageUnit = computed(() => ({ pages: '页', page: '页', lines: '行', rows: '行', row: '行', blocks: '段落块', cells: '单元格', paragraphs: '段', paragraph: '段', files: '项', file: '项', units: '项' })[index.value?.coverage?.unit] || (/^[\u4e00-\u9fff]+$/.test(index.value?.coverage?.unit || '') ? index.value.coverage.unit : '项'))
const clueTerms = computed(() => [...new Set((index.value?.ai?.clues || []).flatMap(clue => clue.terms || []))].slice(0, 16))
const documentVersion = () => props.document?.current_version?.id || props.document?.current_version_id || null
const documentContext = () => {
  const doc = props.document || {}
  return JSON.stringify([doc.status, doc.name, doc.description, doc.storage_path, doc.storage_area,
    doc.original_path, doc.storage_mode, (doc.tags || []).map(tag => [tag.id, tag.name])])
}
function token() { return { generation, id: props.document?.id, library: appStore.libraryPath, version: documentVersion(), context: documentContext() } }
function current(value) { return props.visible && value?.generation === generation && value.id === props.document?.id && value.library === appStore.libraryPath && value.version === documentVersion() && value.context === documentContext() }
function currentAI(value) { return showAI.value && current(value) && value.aiGeneration === aiGeneration }
function clearAI() {
  aiGeneration++; aiController?.abort(); aiController = null
  prepared.value = null; service.value = null; consent.value = false; preparing.value = false; enriching.value = false
  aiError.value = ''; notConfigured.value = false; aiDone.value = false
}
function closeAI() { clearAI(); showAI.value = false }
function clear() {
  generation++; clearTimeout(pollTimer); statusController?.abort(); statusController = null
  closeAI(); index.value = null; error.value = ''; loading.value = false; rebuilding.value = false; showUnits.value = false
}
async function refresh() {
  if (!props.visible || !props.document?.id || rebuilding.value) return
  clearTimeout(pollTimer); statusController?.abort()
  const scope = token(), controller = statusController = new AbortController()
  loading.value = true; error.value = ''
  try {
    const value = await getDocumentIndex(scope.id, controller.signal)
    if (!current(scope) || controller !== statusController) return
    if (value.document_id !== scope.id) throw new Error('索引与当前资料不一致，请刷新状态')
    if (index.value && (value.version_id !== index.value.version_id || value.content_hash !== index.value.content_hash)) closeAI()
    index.value = value
    if (['queued', 'processing'].includes(value.state)) pollTimer = setTimeout(refresh, 1500)
  } catch (cause) {
    if (current(scope) && controller === statusController) error.value = cause.message || '索引状态读取失败，请刷新状态'
  } finally { if (current(scope) && controller === statusController) loading.value = false }
}
async function rebuild() {
  if (!props.visible || !props.document?.id || rebuilding.value || processing.value || enriching.value) return
  clearTimeout(pollTimer); statusController?.abort(); closeAI()
  const scope = token(), controller = statusController = new AbortController()
  rebuilding.value = true; error.value = ''
  try {
    await rebuildDocumentIndex(scope.id, controller.signal)
    if (!current(scope) || controller !== statusController) return
    rebuilding.value = false
    await refresh()
  } catch (cause) {
    if (current(scope) && controller === statusController) error.value = cause.message || '无法重建本地索引，请重试'
  } finally { if (current(scope)) rebuilding.value = false }
}
async function openAI() {
  if (!canEnrich.value) return
  showAI.value = true
  await prepare()
}
async function prepare() {
  clearAI()
  if (!props.visible || !props.document?.id || !showAI.value || !canEnrich.value) return
  const scope = { ...token(), aiGeneration }, controller = aiController = new AbortController()
  preparing.value = true
  try {
    const settings = await getAISettings(controller.signal)
    if (!currentAI(scope)) return
    if (!settings.has_api_key) { notConfigured.value = true; return }
    const value = await prepareIndexEnrichment(scope.id, controller.signal)
    if (!currentAI(scope)) return
    if (value.document_id !== scope.id || !value.preparation_id || typeof value.text !== 'string' || !value.version_id || value.version_id !== index.value?.version_id || !value.content_hash || value.content_hash !== index.value?.content_hash) throw new Error('摘录与当前索引不一致，请刷新状态后重新读取')
    if (!value.service?.base_url || typeof value.service.base_url !== 'string' || !value.service.model || typeof value.service.model !== 'string') throw new Error('缺少本次绑定的 AI 服务，请重新读取摘录')
    service.value = { base_url: value.service.base_url, model: value.service.model }; prepared.value = value
  } catch (cause) { if (currentAI(scope)) aiError.value = cause.message || '摘录读取失败，请重新读取' }
  finally { if (currentAI(scope)) preparing.value = false }
}
async function enrich() {
  if (!consent.value || !prepared.value || enriching.value || !props.visible || !showAI.value) return
  const scope = { ...token(), aiGeneration }, preview = prepared.value
  enriching.value = true; aiError.value = ''
  try {
    const value = await enrichDocumentIndex(preview.preparation_id, aiController.signal)
    if (!currentAI(scope) || prepared.value !== preview) return
    if (value.preparation_id !== preview.preparation_id || value.document_id !== scope.id || value.version_id !== preview.version_id || value.content_hash !== preview.content_hash) throw new Error('AI 结果与本次摘录不一致，请重新读取')
    prepared.value = null; consent.value = false; aiDone.value = true
    await refresh()
  } catch (cause) {
    if (!currentAI(scope)) return
    prepared.value = null; consent.value = false; service.value = null
    aiError.value = `${cause.message || 'AI 补充失败'}；请重新读取摘录并确认发送。`
  } finally { if (currentAI(scope)) enriching.value = false }
}
function openSettings() { closeAI(); router.push('/settings') }
function locationLabel(location) {
  if (!location || typeof location !== 'object') return '资料内容'
  if (location.scope === 'file') return '整份资料'
  const names = { page: '页', paragraph: '段', row: '行', sheet: '工作表', path: '文件', member: '文件', slide: '幻灯片', line: '行' }
  return Object.entries(location).map(([key, value]) => names[key] ? `${names[key]} ${value}` : String(value)).join(' · ') || '资料内容'
}
function gapReason(reason) {
  if (typeof reason === 'string' && /[\u4e00-\u9fff]/.test(reason)) return reason
  return ({ no_text: '未识别出文字', empty_text: '没有可提取文字', image_only: '图像未提取为本地可搜索文字，可用 AI 资料卡理解', visual_not_parsed: '图片或图形尚未提取为本地可搜索文字', ocr_required: '扫描内容尚未提取为本地可搜索文字', unsupported_format: '此格式暂不生成本地原文索引，可尝试 AI 资料卡', folder_not_parsed: '文件夹内容尚未逐项提取为本地可搜索文字', folder_not_supported: '文件夹内容尚未逐项提取为本地可搜索文字', encrypted: '加密内容无法提取', encrypted_pdf: '加密 PDF 无法提取', invalid_document: '文件内容损坏或不符合格式', extraction_failed: '此部分提取失败', text_limit: '此部分超出文字提取限额', page_limit: '此部分超出页数限额', unit_limit: '此部分超出内容单元限额', truncated: '此部分超出本次提取范围' })[reason] || '此部分尚未提取为可搜索文字'
}
watch(() => [props.visible, props.document?.id, appStore.libraryPath, documentVersion(), documentContext()], () => {
  clear()
  if (props.visible && props.document?.id) refresh()
}, { immediate: true, flush: 'sync' })
onUnmounted(clear)
</script>

<style scoped>
.document-index-card { padding:16px; margin:16px 0; border:1px solid #e2e9f1; border-radius:10px; background:#fafcfe; }
.index-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; }
h4, h5 { margin:0; color:#34465b; font-size:14px; }
.index-help { color:#697586; font-size:12px; line-height:1.7; margin:8px 0; overflow-wrap:anywhere; }
.index-overview, .index-ai-summary p { font-size:13px; line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }
.index-notice { margin:10px 0; }
.index-gap, .index-notes { color:#8a6425; font-size:12px; line-height:1.7; overflow-wrap:anywhere; }
.index-notes { padding-left:18px; }
.index-actions, .index-clues { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }
.index-actions :deep(.el-button + .el-button) { margin-left:0; }
.index-ai-summary, .index-ai-action { border-top:1px solid #e2e9f1; margin-top:12px; padding-top:12px; }
.index-ai-action > summary { cursor:pointer; color:#697586; font-size:12px; }
.index-units { max-height:300px; overflow:auto; margin-top:12px; }
.index-unit { padding:10px; background:#fff; border-radius:6px; margin-bottom:8px; font-size:12px; }
.index-unit pre { white-space:pre-wrap; overflow-wrap:anywhere; font-family:inherit; line-height:1.7; }
.index-consent { height:auto; align-items:flex-start; margin-top:12px; }
.index-consent :deep(.el-checkbox__label) { white-space:normal; line-height:1.6; }
.index-consent :deep(.el-checkbox__input) { margin-top:4px; }
</style>
