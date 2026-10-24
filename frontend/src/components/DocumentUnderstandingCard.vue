<template>
  <section class="understanding-card" data-testid="document-understanding-card" aria-label="AI 资料卡">
    <div class="card-heading">
      <h4>AI 资料卡</h4>
      <el-tag size="small" :type="stateType">{{ stateLabel }}</el-tag>
    </div>
    <p class="card-help"><span v-if="objectKindLabel" data-testid="card-object-kind">{{ objectKindLabel }} · </span>基于已保存版本{{ snapshot?.version_number ? ` v${snapshot.version_number}` : '' }}，帮助了解、查找资料和定位原文。</p>
    <p v-if="document?.is_folder" class="card-help">将整个文件夹作为一套资料，阅读内部文件并生成总览与内容导航；保留内部文件名和目录结构。</p>
    <p v-else-if="isArchive" class="card-help">将整个压缩包作为一套资料，按需阅读包内文件；保留压缩格式、内部文件名和目录结构。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="card-notice" />
    <p v-if="snapshot?.job?.error" class="card-warning" role="status">{{ snapshot.job.error }}</p>
    <p v-if="snapshot?.job?.cancelled" class="card-help">本次生成已取消，已有资料卡和人工补充保留。</p>
    <p v-if="currentCard" class="card-summary">{{ currentCard.content?.summary }}</p>
    <p v-else-if="snapshot?.state === 'stale'" class="card-help">资料内容或理解规则已更新，旧卡片仅供回顾；生成后会基于当前版本重新理解。</p>
    <p v-else-if="snapshot && !processing" class="card-help">AI 预整理会同时生成资料卡，也可在这里生成。</p>
    <p v-if="snapshot?.state === 'partial'" class="card-warning">仅理解了部分内容，请留意下方覆盖范围和缺口。</p>
    <details v-if="currentCard" class="card-details" data-testid="card-details">
      <summary>查看内容导航、关键事实与依据</summary>
      <div v-if="currentCard.content?.document_type || currentCard.content?.topics?.length" class="card-section">
        <p v-if="currentCard.content.document_type"><strong>资料类型</strong> {{ currentCard.content.document_type }}</p>
        <p v-if="currentCard.content.topics?.length"><strong>主要主题</strong> {{ currentCard.content.topics.join(' · ') }}</p>
      </div>
      <div v-if="currentCard.content?.sections?.length" class="card-section">
        <h5>内容导航</h5>
        <article v-for="(section, i) in currentCard.content.sections" :key="i">
          <strong>{{ section.title }}</strong><p>{{ section.summary }}</p><small>{{ sourceLabels(section.source_ids) }}</small>
        </article>
      </div>
      <div v-if="currentCard.content?.facts?.length" class="card-section">
        <h5>关键对象与事实</h5>
        <dl><div v-for="(fact, i) in currentCard.content.facts" :key="i"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}<small>{{ sourceLabels(fact.source_ids) }}</small></dd></div></dl>
      </div>
      <div v-if="currentCard.content?.search_terms?.length" class="card-section">
        <h5>检索线索</h5><p class="card-help">AI 提炼的查找用语，不会自动加入人工标签。</p>
        <div class="card-terms"><el-tag v-for="term in currentCard.content.search_terms" :key="term" size="small" type="info">{{ term }}</el-tag></div>
      </div>
      <div v-if="currentCard.content?.uses?.length" class="card-section"><h5>可能的用途 · AI 建议</h5><ul><li v-for="(use, i) in currentCard.content.uses" :key="i">{{ use }}</li></ul></div>
      <div class="card-section">
        <h5>理解范围</h5><p class="card-help">{{ coverageText }}</p>
        <ul v-if="cardGaps.length" class="card-warning"><li v-for="(gap, i) in cardGaps" :key="i">{{ gap }}</li></ul>
        <p class="card-help">摘要可能遗漏细节，涉及参数、条款或判断时仍需核对原文。</p>
      </div>
      <details v-if="currentCard.sources?.length" class="card-section card-sources">
        <summary>查看原文依据（{{ currentCard.sources.length }} 处）</summary>
        <article v-for="(source, i) in currentCard.sources" :key="source.id || i"><strong>{{ source.label || locationLabel(source.location) }}</strong><p>{{ source.excerpt || source.text || source.quote || '' }}</p></article>
      </details>
      <p class="card-help">卡片修订 {{ currentCard.revision || 1 }}<span v-if="currentCard.model"> · {{ currentCard.model }}</span><span v-if="currentCard.generated_at"> · {{ currentCard.generated_at }}</span></p>
    </details>
    <details v-if="previousCard && !currentCard" class="card-details card-previous" data-testid="previous-card">
      <summary>{{ previousCardLabel }}</summary>
      <p class="card-warning">{{ previousCardNotice }}</p><p>{{ previousCard.content?.summary }}</p>
    </details>
    <template v-if="!compact">
      <div class="card-actions">
        <el-button size="small" :loading="loading" :disabled="busy" @click="refresh">刷新</el-button>
        <el-button v-if="processing" size="small" :loading="busy" @click="cancel" data-testid="card-cancel">取消生成</el-button>
      </div>
      <div v-if="config && !config.configured" class="card-generation"><p class="card-help">配置 AI 服务后即可生成资料卡。</p><el-button size="small" @click="openSettings">前往设置</el-button></div>
      <div v-else-if="config?.configured" class="card-generation">
        <p class="card-help">点击下方按钮，将本资料已保存版本的文字与必要图像发送至 {{ config.service?.base_url }} · {{ config.service?.model }}。{{ document?.is_folder || isArchive ? '包含内部文件的内容和相对路径。' : '' }}调用可能产生费用。</p>
        <el-checkbox v-model="autoUpdate" :disabled="busy || processing" class="card-checkbox">本次生成后，此资料保存新版本时自动更新卡片</el-checkbox>
        <p class="card-help">随本次生成保存此设置，默认关闭。开启后仅持续分析本资料，服务或模型变更后会暂停。</p>
        <p v-if="snapshot?.policy?.enabled" class="card-help">{{ snapshot.policy.active ? '本资料已开启自动更新。' : '自动更新已暂停。' }} {{ snapshot.policy.paused_reason || '' }} <el-button text size="small" :disabled="busy" @click="disableAutoUpdate">关闭自动更新</el-button></p>
        <el-button type="primary" plain size="small" :loading="busy" :disabled="!snapshot || loading || processing || busy" @click="generate" data-testid="card-generate">{{ generationLabel }}</el-button>
      </div>
      <details v-if="snapshot" class="card-details card-annotation" :open="Boolean(snapshot.annotation?.text || annotationText || annotationConflict)">
        <summary>我的补充与修正</summary>
        <p class="card-help">用户提供的背景与修正，独立保存，AI 重新生成不会覆盖。</p>
        <p v-if="snapshot.annotation?.needs_review" class="card-warning">这份补充关联旧版本，请对照当前内容核对后保存。</p>
        <el-input v-model="annotationText" type="textarea" :rows="3" :maxlength="4000" show-word-limit :disabled="annotationSaving" placeholder="补充用途、背景，或纠正 AI 的理解…" aria-label="我的补充与修正" data-testid="card-annotation-input" />
        <el-alert v-if="annotationError" :title="annotationError" type="warning" :closable="false" class="card-notice" />
        <div v-if="annotationConflict" class="annotation-conflict">
          <p v-if="annotationVersion !== snapshot.version_id" class="card-warning">资料已更新到 v{{ snapshot.version_number }}。请对照新版本核对这份草稿，确认后才会将它关联到该版本。</p>
          <p><strong>当前已保存的补充</strong></p><p>{{ snapshot.annotation?.text || '（暂无）' }}</p>
          <el-button size="small" :disabled="loading" @click="refresh">刷新已保存内容</el-button>
          <el-button size="small" :disabled="!canRebaseAnnotation" @click="rebaseAnnotation">{{ annotationRebaseLabel }}</el-button>
        </div>
        <el-button size="small" :loading="annotationSaving" :disabled="busy || annotationConflict || !annotationDirty && !snapshot.annotation?.needs_review" @click="saveAnnotation" data-testid="card-save-annotation">保存补充</el-button>
        <span v-if="annotationSaved" class="card-help" role="status"> 已保存</span>
      </details>
    </template>
    <p v-else-if="snapshot?.annotation?.text" class="card-help"><strong>用户补充{{ snapshot.annotation.needs_review ? ' · 跨版待核对' : '' }}</strong> {{ snapshot.annotation.text }}</p>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { getDocumentCardsConfig, getDocumentCard, generateDocumentCard, cancelDocumentCard, setDocumentCardPolicy, saveDocumentCardAnnotation } from '@/api/documentCards'

const props = defineProps({ visible: Boolean, document: Object, compact: Boolean })
const appStore = useAppStore(), router = useRouter()
const snapshot = ref(null), config = ref(null), loading = ref(false), error = ref(''), busy = ref(false), autoUpdate = ref(false)
const annotationText = ref(''), annotationBase = ref(''), annotationRevision = ref(0), annotationConflict = ref(false)
const annotationVersion = ref(null)
const annotationError = ref(''), annotationSaving = ref(false), annotationSaved = ref(false)
let generation = 0, pollTimer, readController, actionController, annotationController, policyInitialized = false
const processing = computed(() => ['queued', 'running'].includes(snapshot.value?.job?.state))
const objectKindLabel = computed(() => ({ text: '文本文档', word: 'Word 文档', spreadsheet: '电子表格', presentation: '演示文稿', pdf: 'PDF', image: '图片', folder: '文件夹', archive: '压缩包' })[snapshot.value?.object_kind] || '')
const isArchive = computed(() => snapshot.value?.object_kind === 'archive')
const currentCard = computed(() => ['ready', 'partial'].includes(snapshot.value?.state) ? snapshot.value.card : null)
const previousCard = computed(() => snapshot.value?.previous_card || (snapshot.value?.state === 'stale' ? snapshot.value.card : null))
const previousCardSameVersion = computed(() => previousCard.value?.version_id === snapshot.value?.version_id)
const previousCardLabel = computed(() => `${previousCardSameVersion.value ? '查看待更新资料卡' : '查看旧版资料卡'}${previousCard.value?.version_number != null ? ` · v${previousCard.value.version_number}` : ''}`)
const previousCardNotice = computed(() => previousCardSameVersion.value ? '资料版本未变化，但解析或理解规则已更新；以下为先前生成的理解，仅供回顾。' : '以下理解来自旧版本，不代表当前资料内容。')
const cardGaps = computed(() => [...new Set([...(currentCard.value?.content?.gaps || []), ...(currentCard.value?.coverage?.gaps || [])].map(gap => typeof gap === 'string' ? gap : gap.message || gap.reason || '部分内容尚未识别'))])
const annotationDirty = computed(() => annotationText.value !== annotationBase.value)
const canRebaseAnnotation = computed(() => annotationConflict.value && !loading.value && Boolean(snapshot.value?.annotation) && (snapshot.value.annotation.revision !== annotationRevision.value || snapshot.value.version_id !== annotationVersion.value))
const annotationRebaseLabel = computed(() => snapshot.value?.version_id !== annotationVersion.value ? `已核对 v${snapshot.value?.version_number || ''}，保留我的编辑` : '已核对，保留我的编辑')
const stateLabel = computed(() => processing.value ? '正在理解' : snapshot.value?.job?.state === 'failed' ? '生成未完成' : ({ ready: '已建立', partial: '部分理解', stale: '待更新', missing: '待生成' })[snapshot.value?.state] || (loading.value ? '正在读取' : '待读取'))
const stateType = computed(() => snapshot.value?.job?.state === 'failed' ? 'warning' : ({ ready: 'success', partial: 'warning', stale: 'warning' })[snapshot.value?.state] || 'info')
const generationLabel = computed(() => snapshot.value?.state === 'partial' ? '补全资料卡' : snapshot.value?.state === 'ready' ? '重新生成资料卡' : '生成资料卡')
const coverageText = computed(() => {
  const coverage = currentCard.value?.coverage || {}
  const read = coverage.read_units ?? coverage.processed_units ?? coverage.processed
  const total = coverage.total_units ?? coverage.total
  const parts = []
  if (Number.isFinite(coverage.read_members) && Number.isFinite(coverage.total_members)) {
    parts.push(`已阅读成员 ${coverage.read_members} / ${coverage.total_members}`)
    const unread = coverage.unread_members ?? Math.max(0, coverage.total_members - coverage.read_members)
    if (Number.isFinite(unread) && unread > 0) parts.push(`${unread} 个成员尚未阅读`)
  }
  if (Number.isFinite(read) && Number.isFinite(total)) parts.push(`原文单元 ${read} / ${total}`)
  if (Number.isFinite(coverage.total_visual_pages) && coverage.total_visual_pages > 0) parts.push(`视觉页面 ${coverage.visual_pages || 0} / ${coverage.total_visual_pages}`)
  if (Number.isFinite(coverage.total_embedded_images) && coverage.total_embedded_images > 0) parts.push(`内嵌图片 ${coverage.embedded_images || 0} / ${coverage.total_embedded_images}`)
  return parts.length ? `本次阅读：${parts.join('，')}。请结合原文依据查看具体位置。` : '请结合原文依据和未识别部分判断覆盖范围。'
})
const version = () => props.document?.current_version?.id || props.document?.current_version_id || null
const token = () => ({ generation, id: props.document?.id, version: version(), library: appStore.libraryPath })
const current = scope => props.visible && scope.generation === generation && scope.id === props.document?.id && scope.version === version() && scope.library === appStore.libraryPath
const message = (cause, fallback) => cause.response?.data?.error?.message || cause.message || fallback
const serviceKey = value => JSON.stringify([value?.library_token, value?.service_revision, value?.configured, value?.service?.base_url, value?.service?.model])

function reset(preserveAnnotation = false) {
  generation++; clearTimeout(pollTimer); readController?.abort(); actionController?.abort(); annotationController?.abort()
  readController = actionController = annotationController = null
  snapshot.value = config.value = null; loading.value = busy.value = false; error.value = ''; autoUpdate.value = false; policyInitialized = false
  if (!preserveAnnotation) {
    annotationText.value = annotationBase.value = annotationError.value = ''; annotationRevision.value = 0; annotationVersion.value = null
    annotationConflict.value = false
  }
  annotationSaving.value = annotationSaved.value = false
}
function acceptDetail(value, scope) {
  if (value.document_id !== scope.id || !value.version_id) throw new Error('资料卡与当前资料不一致，请刷新资料详情')
  // 首次读取须匹配入口版本；同资料后续刷新以服务端版本为准，草稿仍绑定原版本。
  if (scope.version && value.version_id !== scope.version && !snapshot.value && !annotationVersion.value) throw new Error('资料卡与当前资料版本不一致，请刷新资料详情')
  if ((annotationDirty.value || annotationConflict.value || annotationSaving.value) && annotationVersion.value && annotationVersion.value !== value.version_id) {
    annotationConflict.value = true
    annotationError.value = '资料版本已变化，你的草稿已保留。请对照新版本核对后，再保存这份补充。'
  }
  snapshot.value = value
  if (!policyInitialized) { autoUpdate.value = Boolean(value.policy?.enabled && value.policy?.active); policyInitialized = true }
  if (!annotationDirty.value && !annotationConflict.value && !annotationSaving.value) {
    annotationText.value = annotationBase.value = value.annotation?.text || ''
    annotationRevision.value = value.annotation?.revision || 0
    annotationVersion.value = value.version_id
  }
}
async function refresh() {
  if (!props.visible || !props.document?.id) return
  clearTimeout(pollTimer); readController?.abort()
  const scope = token(), controller = readController = new AbortController()
  loading.value = true; error.value = ''
  try {
    const [settings, detail] = await Promise.all([getDocumentCardsConfig(controller.signal), getDocumentCard(scope.id, controller.signal)])
    if (!current(scope) || controller !== readController) return
    acceptDetail(detail, scope); config.value = settings
    if (processing.value) pollTimer = setTimeout(refresh, 1800)
  } catch (cause) {
    if (current(scope) && controller === readController) { error.value = message(cause, '资料卡读取失败，请重试'); if (processing.value) pollTimer = setTimeout(refresh, 3000) }
  } finally { if (current(scope) && controller === readController) loading.value = false }
}
async function generate() {
  if (props.compact || !props.visible || !snapshot.value || !config.value?.configured || busy.value || processing.value || annotationSaving.value) return
  const scope = token(), displayed = config.value, controller = actionController = new AbortController()
  busy.value = true; error.value = ''
  try {
    const latest = await getDocumentCardsConfig(controller.signal)
    if (!current(scope)) return
    config.value = latest
    if (serviceKey(latest) !== serviceKey(displayed)) { error.value = 'AI 服务配置已变化，请核对上方服务后再次点击生成。'; return }
    await generateDocumentCard(scope.id, { library_token: displayed.library_token, service_revision: displayed.service_revision, consent: true, request_id: crypto.randomUUID(), auto_update: autoUpdate.value }, controller.signal)
    if (current(scope)) await refresh()
  } catch (cause) {
    if (!current(scope)) return
    error.value = message(cause, '资料卡生成未启动，请重试')
    if (cause.response?.status === 409) {
      try { const latest = await getDocumentCardsConfig(controller.signal); if (current(scope)) config.value = latest } catch { /* 原失败信息保留，不自动重发资料。 */ }
    }
  } finally { if (current(scope)) busy.value = false }
}
async function performAction(action) {
  if (props.compact || !props.visible || !config.value || !snapshot.value || busy.value) return
  const scope = token(), controller = actionController = new AbortController()
  busy.value = true; error.value = ''
  try { await action(scope.id, config.value.library_token, controller.signal); if (current(scope)) await refresh() }
  catch (cause) { if (current(scope)) error.value = message(cause, '操作未完成，请重试') }
  finally { if (current(scope)) busy.value = false }
}
async function cancel() { await performAction((id, library_token, signal) => cancelDocumentCard(id, { library_token }, signal)) }
async function disableAutoUpdate() {
  await performAction((id, library_token, signal) => setDocumentCardPolicy(id, { library_token, enabled: false, consent: false }, signal))
  if (!snapshot.value?.policy?.enabled) autoUpdate.value = false
}
async function saveAnnotation() {
  if (props.compact || !props.visible || !config.value || !snapshot.value || annotationSaving.value || annotationConflict.value || busy.value) return
  if (annotationVersion.value !== snapshot.value.version_id) {
    annotationConflict.value = true; annotationError.value = '资料版本已变化，请核对新版本后再保存这份补充。'; return
  }
  if (annotationText.value.length > 4000) { annotationError.value = '人工补充最多 4000 字，请精简后保存。'; return }
  const scope = token(), controller = annotationController = new AbortController(), text = annotationText.value
  annotationSaving.value = true; annotationError.value = ''; annotationSaved.value = false
  try {
    await saveDocumentCardAnnotation(scope.id, { library_token: config.value.library_token, version_id: annotationVersion.value, expected_revision: annotationRevision.value, text }, controller.signal)
    if (!current(scope)) return
    annotationBase.value = text; annotationSaving.value = false; annotationSaved.value = true
    await refresh()
  } catch (cause) {
    if (!current(scope)) return
    annotationConflict.value = cause.response?.status === 409
    annotationError.value = annotationConflict.value ? '补充或资料版本已变化，你的输入已保留。请刷新已保存内容，核对后再保存。' : message(cause, '补充保存失败，你的输入已保留')
  } finally { if (current(scope)) annotationSaving.value = false }
}
function rebaseAnnotation() {
  if (!canRebaseAnnotation.value) return
  annotationRevision.value = snapshot.value.annotation.revision; annotationBase.value = snapshot.value.annotation.text || ''
  annotationVersion.value = snapshot.value.version_id
  annotationConflict.value = false; annotationError.value = ''; annotationSaved.value = false
}
function locationLabel(location) {
  if (!location || typeof location !== 'object') return String(location || '资料原文')
  const names = { member_path: '文件', page: '页', sheet: '工作表', row: '行', cell: '单元格', paragraph: '段', slide: '幻灯片', line: '行' }
  return Object.entries(location).map(([key, value]) => `${names[key] || key} ${value}`).join(' · ')
}
function sourceLabels(ids) {
  const sources = new Map((currentCard.value?.sources || []).map(source => [source.id, source.label || locationLabel(source.location)]))
  return (ids || []).map(id => sources.get(id)).filter(Boolean).join(' · ')
}
function openSettings() { router.push('/settings') }
watch(() => [props.visible, props.document?.id, version(), appStore.libraryPath], (now, before) => {
  const sameDocument = now[0] && before?.[0] && now[1] === before[1] && now[3] === before[3]
  const preserveDraft = sameDocument && (annotationDirty.value || annotationConflict.value || annotationSaving.value)
  reset(preserveDraft)
  if (preserveDraft && annotationVersion.value !== version()) {
    annotationConflict.value = true
    annotationError.value = '资料版本已变化，你的草稿已保留。请对照新版本核对后，再保存这份补充。'
  }
  if (props.visible && props.document?.id) refresh()
}, { immediate: true, flush: 'sync' })
watch(annotationText, () => { annotationSaved.value = false })
onUnmounted(reset)
</script>

<style scoped>
.understanding-card { margin: 16px 0; padding: 18px; border: 1px solid #dce7ed; border-radius: 12px; background: #f7fbfc; color: #34465b; overflow-wrap: anywhere; }
.card-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
h4 { margin: 0; font-size: 15px; } h5 { margin: 0 0 8px; font-size: 13px; }
.card-help, small { color: #687b8e; font-size: 12px; line-height: 1.8; } .card-help { margin: 8px 0; }
.card-summary { font-size: 14px; line-height: 1.85; white-space: pre-wrap; margin: 12px 0; }
.card-details { padding-top: 12px; margin-top: 12px; border-top: 1px solid #e1eaef; font-size: 13px; line-height: 1.8; }
summary { cursor: pointer; color: #2d6073; font-weight: 500; } .card-section { margin-top: 14px; }
.card-section p, .card-previous p { white-space: pre-wrap; margin: 5px 0; } .card-section ul { padding-left: 20px; margin: 5px 0; }
.card-section article { margin-top: 10px; } .card-section dl { margin: 0; } .card-section dl > div { margin: 8px 0; } dt { font-weight: 600; } dd { margin-left: 0; white-space: pre-wrap; } small { display: block; }
.card-warning { font-size: 12px; color: #926226; line-height: 1.8; }
.card-actions, .card-terms { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.card-actions :deep(.el-button + .el-button) { margin-left: 0; } .card-notice { margin: 12px 0; }
.card-generation { margin-top: 12px; padding-top: 4px; } .card-checkbox { height: auto; align-items: flex-start; }
.card-checkbox :deep(.el-checkbox__label) { white-space: normal; line-height: 1.7; font-size: 12px; } .card-checkbox :deep(.el-checkbox__input) { margin-top: 3px; }
.card-sources article, .annotation-conflict { padding: 10px; border-radius: 6px; background: #fff; }
.card-annotation :deep(.el-textarea) { margin-bottom: 10px; } .annotation-conflict { margin-bottom: 10px; white-space: pre-wrap; }
</style>
