<template>
  <div class="partner-page" data-testid="partner-page" v-loading="loading">
    <aside class="conversation-sidebar">
      <div class="sidebar-heading"><span>对话</span><el-button size="small" type="primary" plain @click="newSession" :disabled="loading" data-testid="partner-new-session">＋ 新对话</el-button></div>
      <div class="session-list">
        <div v-for="item in sessions" :key="item.id" class="session-row" :class="{ selected: item.id === activeSession?.id }">
          <button class="session-select" @click="selectSession(item.id)" :title="item.title">{{ item.title || '新对话' }}</button>
          <button class="session-delete" @click="removeSession(item)" :aria-label="`删除对话 ${item.title || '新对话'}`" title="删除对话">×</button>
        </div>
        <p v-if="!sessions.length && !loading" class="quiet sidebar-empty">开始一段新的讨论。</p>
      </div>
      <p class="sidebar-note">对话保存在当前个人资料库。切换对话后，已开始的任务继续运行。</p>
    </aside>

    <section class="partner-main">
      <header class="partner-heading">
        <div><h2>AI 伙伴</h2><p>一起找资料、理解内容、比较分析，形成可保存的成果。</p></div>
        <el-button text @click="router.push('/settings')">AI 设置</el-button>
      </header>
      <el-alert v-if="error" :title="error" type="error" :closable="false" class="partner-alert" />
      <el-alert v-if="!loading && !settings.has_api_key" title="请先在设置中配置 AI 服务和密钥，再开始讨论。" type="info" :closable="false" class="partner-alert" />

      <div class="conversation" aria-live="polite" ref="conversationRef" @scroll="onConversationScroll">
        <div v-if="!messages.length && !isRunning" class="conversation-welcome">
          <div class="partner-emblem">✦</div><h3>从你的问题开始</h3>
          <p>可以直接交流，也可以选择资料，让我结合原文一起思考。</p>
          <div class="example-prompts">
            <button @click="draft = '帮我查找与这个问题相关的资料，并说明你为什么选择它们。'">查找相关资料</button>
            <button @click="draft = '请阅读所选资料，梳理关键内容，并指出需要进一步核实的地方。'">一起阅读资料</button>
            <button @click="draft = '比较所选资料的异同，引用原文，并形成一份分析说明。'">比较并形成成果</button>
          </div>
          <details class="reading-formats" data-testid="partner-reading-formats"><summary>可以一起阅读哪些资料？</summary><p>正文与表格：Word、Excel、PowerPoint、开放文档 ODT / ODS / ODP、CSV / TSV、HTML 和普通文本。</p><p>图像：PDF 原页、常用图片，以及上述办公文档实际引用的内嵌位图。需要当前模型支持图像理解，可在 AI 设置中测试。</p><p>Office 内嵌图片不代表完整页面布局。旧版 DOC / XLS / PPT、CAD、音视频暂未支持；可真实另存为 PDF 后阅读。</p></details>
        </div>
        <template v-for="message in conversationItems" :key="message.id"><article class="chat-message" :class="message.role" :data-testid="`partner-message-${message.role}`">
          <div class="message-role">{{ message.role === 'user' ? '你' : 'AI 伙伴' }}<span v-if="message.incomplete" class="incomplete-label">{{ message.pending ? '正在生成' : '未完成 · 已保留内容' }}</span></div>
          <div v-if="message.role === 'assistant'" :data-testid="message.incomplete ? 'partner-answer-preview' : undefined"><PartnerMarkdown :content="message.content" /></div>
          <div v-else class="message-body">{{ message.content }}</div>
          <div v-if="message.sources?.length" class="message-sources">
            <span class="quiet">资料依据</span>
            <button v-for="source in message.sources" :key="source.id" class="source-chip" @click="openSource(source)">[{{ source.id }}] {{ source.name }} · v{{ source.version_number }}<span v-if="source.label"> · {{ source.label }}</span></button>
          </div>
        </article>
        <section v-for="task in artifactTasksForMessage(message)" :key="task.id" class="artifact-group">
          <div v-for="artifact in task.artifacts" :key="artifact.id" class="artifact-card">
            <div class="artifact-heading"><div><span class="artifact-type">生成成果</span><h4>{{ artifact.name }}</h4></div><el-button v-if="artifact.saved_document_id" size="small" @click="openSavedArtifact(artifact)">查看已收录资料</el-button><el-button v-else size="small" type="primary" plain :loading="savingArtifact === `${task.id}:${artifact.id}`" :disabled="Boolean(savingArtifact)" @click="saveArtifact(task, artifact)" data-testid="partner-save-artifact">确认收录到待整理</el-button></div>
            <details><summary>预览成果</summary><PartnerMarkdown v-if="artifact.media_type === 'text/markdown'" class="artifact-preview" :content="artifact.content" /><pre v-else class="artifact-content">{{ artifact.content }}</pre></details>
          </div>
        </section>
        </template>

        <div v-if="run" class="task-progress" :class="{ 'task-stopped': !isRunning, 'task-completed': run.state === 'completed' }" data-testid="partner-task-progress">
          <div v-if="run.state !== 'completed'" class="task-heading"><span>{{ taskStateLabel(run.state) }}</span><el-button v-if="isRunning" size="small" text :loading="cancelling" :disabled="submissionPending || starting" @click="cancelRun" data-testid="partner-cancel">停止生成</el-button></div>
          <details v-if="run.steps?.length || run.usage" class="task-details" data-testid="partner-task-details"><summary>{{ isRunning ? run.steps?.at(-1)?.message || '正在思考你的问题…' : run.state === 'completed' ? '已完成 · 查看处理过程' : '查看处理过程' }}</summary><ol v-if="run.steps?.length" class="task-steps"><li v-for="step in run.steps" :key="step.id"><span>{{ stepKindLabel(step.kind) }}</span>{{ step.message }}</li></ol><p v-if="run.progress" class="task-usage" data-testid="partner-reading-progress">{{ readingProgressText(run.progress) }}</p><p v-if="run.usage" class="task-usage" data-testid="partner-task-usage">{{ formatUsage(run.usage) }}</p></details>
          <p v-if="isPaused" class="quiet" data-testid="partner-task-pause">{{ run.pause?.message || '本次处理较长，进度和回答已保留。可以继续处理，也可以直接提出新问题。' }}</p>
          <p v-if="run.error" class="task-error">{{ run.error.message || '本次连接未完成，已有内容已保留。' }}</p>
          <div v-if="isPaused" class="paused-actions"><el-button v-if="run.pause?.resumable" size="small" type="primary" plain :loading="starting" :disabled="submissionPending || cancelling || loading || !settings.has_api_key || Boolean(budgetError)" @click="resumeRun" data-testid="partner-continue">继续处理</el-button><el-button size="small" text :loading="cancelling" :disabled="submissionPending || starting" @click="cancelRun" data-testid="partner-end-paused">结束此任务</el-button></div>
          <el-button v-if="canRetryRun && !draft.trim()" size="small" text :disabled="starting || loading" @click="retryRun" data-testid="partner-retry">重新尝试</el-button>
          <p v-if="streamError && isRunning" class="quiet" data-testid="partner-stream-fallback">{{ pollError ? '连接暂时不可用，正在自动重连；不会重复发送问题。' : '正在恢复实时进度，任务仍在后台继续。' }}</p>
          <el-button v-if="pollError && isRunning" size="small" text :disabled="cancelling || starting" @click="refreshRun">立即重连</el-button>
        </div>


      </div>

      <button v-if="!followLatest && conversationItems.length" class="latest-button" @click="followConversation">↓ 回到最新消息</button>
      <div class="composer">
        <div v-if="submissionPending && !starting" class="submission-recovery" role="status" data-testid="partner-submission-recovery"><span>正在核对上次{{ pendingSubmission.kind === 'resume' ? '继续处理' : '发送' }}的结果。可以编辑下一条草稿，确认前不会重复发送。</span><el-button size="small" text @click="retryPendingSubmission" :disabled="cancelling || loading" data-testid="partner-retry-submission">重试本次{{ pendingSubmission.kind === 'resume' ? '继续处理' : '发送' }}</el-button></div>
        <div class="scope-toolbar">
          <span class="scope-label">可用资料</span>
          <el-radio-group v-model="scopeMode" size="small" :disabled="isBlocked || starting || loading">
            <el-radio-button value="selected" label="selected">所选资料</el-radio-button>
            <el-radio-button value="library" label="library">整个个人库</el-radio-button>
          </el-radio-group>
          <el-button v-if="scopeMode === 'selected'" size="small" @click="openDocumentPicker" :disabled="isBlocked || starting || loading" data-testid="partner-scope">选择资料<span v-if="selectedDocuments.length">（{{ selectedDocuments.length }}）</span></el-button>
          <el-switch v-model="thinkingMode" active-value="enabled" inactive-value="disabled" active-text="深度思考" :disabled="isRunning || starting || loading" data-testid="partner-thinking" />
        </div>
        <div v-if="scopeMode === 'selected' && selectedDocuments.length" class="selected-documents"><el-tag v-for="doc in selectedDocuments" :key="doc.id" :closable="!isBlocked && !starting" @close="removeScopeDocument(doc.id)" :disable-transitions="true">{{ doc.name }}</el-tag></div>
        <p class="scope-description">{{ scopeMode === 'library' ? '按需查找个人库资料，不会一次读取整个资料库。' : selectedDocuments.length ? '结合所选资料的已保存版本回答。' : '自由交流，暂不读取资料库。' }}</p>
        <el-input v-model="draft" type="textarea" :autosize="{ minRows: 2, maxRows: 7 }" :maxlength="8000" placeholder="描述你的问题，或希望一起完成的事情…" :disabled="isBlocked || starting || loading" @keydown.enter="handleComposerEnter" data-testid="partner-question" />
        <div class="composer-footer">
          <div class="service-disclosure" data-testid="partner-service"><span v-if="settings.service?.model" class="service-model">{{ settings.service.model }}</span><span v-if="settings.service?.base_url" class="service-destination">{{ settings.service.base_url }}</span><span class="quiet">发送即允许上述服务按当前范围处理问题与相关资料 · Enter 发送，Shift + Enter 换行</span></div>
          <el-button type="primary" :loading="starting" :disabled="submissionPending || !draft.trim() || !settings.has_api_key || !settings.service_revision || isBlocked || loading || Boolean(budgetError)" @click="sendMessage" data-testid="partner-send">发送</el-button>
        </div>
        <details v-if="budgetReady" class="advanced-options"><summary>高级选项</summary><div class="budget-controls">
          <div class="budget-toolbar"><span class="scope-label">任务预算</span><el-radio-group v-model="budgetMode" size="small" :disabled="isRunning || starting || loading" data-testid="partner-budget-mode"><el-radio-button v-for="mode in ['quick', 'standard', 'deep']" :key="mode" :value="mode" :label="mode">{{ budgetPolicy.presets[mode]?.label || ({ quick: '快速', standard: '标准', deep: '深入' })[mode] }}</el-radio-button><el-radio-button value="custom" label="custom">自定义</el-radio-button></el-radio-group></div>
          <div v-if="budgetMode === 'custom'" class="budget-custom"><label v-for="field in budgetFields" :key="field.key"><span>{{ field.label }}</span><el-input-number v-model="customBudget[field.key]" :min="budgetPolicy.bounds[field.key].min" :max="budgetPolicy.bounds[field.key].max" :step="field.step" :precision="0" :disabled="isRunning || starting || loading" controls-position="right" size="small" :data-testid="field.testid" /></label></div>
          <p v-if="budgetError" class="budget-error" data-testid="partner-budget-error">{{ budgetError }}</p><p v-else class="budget-summary">每次最多 {{ budgetLimits?.model_requests }} 次模型请求 · {{ budgetLimits?.tool_calls }} 次工具调用 · {{ budgetLimits?.output_tokens }} 输出 tokens · {{ budgetLimits?.time_seconds }} 秒。通常无需调整；达到总量时保留进度。实际费用由服务商计费。</p>
        </div></details>
      </div>
    </section>

    <el-dialog v-model="pickerVisible" title="选择本轮讨论的资料" width="min(640px, calc(100vw - 32px))">
      <p class="dialog-intro">可选文档库或待整理中的文件，最多 100 份。只选择资料不会调用 AI。</p>
      <el-input v-model="documentKeyword" placeholder="按名称或内容查找资料" clearable @keyup.enter="searchScopeDocuments" data-testid="partner-document-search"><template #append><el-button @click="searchScopeDocuments" :loading="documentSearching">查找</el-button></template></el-input>
      <el-alert v-if="documentError" :title="documentError" type="error" :closable="false" class="picker-alert" />
      <div class="document-options" v-loading="documentSearching"><label v-for="doc in documentOptions" :key="doc.id" class="document-option"><el-checkbox :model-value="selectedDocuments.some(item => item.id === doc.id)" @change="toggleScopeDocument(doc, $event)" :data-testid="`partner-select-document-${doc.id}`" /><span>{{ doc.name }}<small>{{ doc.status === 'pending' ? '待整理' : '文档库' }} · v{{ doc.current_version.version_number }}</small></span></label><p v-if="!documentSearching && !documentOptions.length" class="quiet">未找到可用资料，尝试调整搜索词。</p></div>
      <el-pagination v-if="documentTotal > 30" small layout="prev, pager, next" :total="documentTotal" :page-size="30" v-model:current-page="documentPage" @current-change="searchScopeDocuments" />
      <template #footer><span class="quiet picker-count">已选择 {{ selectedDocuments.length }} 份</span><el-button type="primary" @click="pickerVisible = false">完成</el-button></template>
    </el-dialog>

    <el-dialog v-model="sourceVisible" title="查看资料依据" width="min(600px, calc(100vw - 32px))">
      <template v-if="selectedSource"><h3 class="source-title">[{{ selectedSource.id }}] {{ selectedSource.name }}</h3><p class="quiet">引用已保存版本 v{{ selectedSource.version_number }}<span v-if="selectedSource.label"> · {{ selectedSource.label }}</span></p><pre v-if="selectedSource.excerpt" class="source-excerpt">{{ selectedSource.excerpt }}</pre><p v-else class="quiet">此处记录了资料版本和位置；请到资料详情核对对应历史版本。</p><p class="source-version-note">引用与以上版本绑定。资料详情展示当前状态，可在版本列表核对引用版本。</p></template>
      <template #footer><el-button @click="sourceVisible = false">关闭</el-button><el-button type="primary" @click="openSourceDocument" :disabled="!selectedSource">前往资料详情</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getDocument, searchDocuments } from '@/api/document'
import { getPartnerRequestTask, getPartnerChatConfig, sendPartnerMessage, resumePartnerTask, getPartnerBudgetPolicy, listPartnerSessions, createPartnerSession, getPartnerSession, deletePartnerSession, getPartnerTask, streamPartnerTask, cancelPartnerTask, savePartnerArtifact } from '@/api/partner'
import PartnerMarkdown from '@/components/PartnerMarkdown.vue'
import { pendingPartnerSubmissions } from '@/utils/partnerSubmissionRegistry'

const appStore = useAppStore(), route = useRoute(), router = useRouter()
const sessions = ref([]), activeSession = ref(null), messages = ref([]), tasks = ref([]), run = ref(null)
const settings = ref({ has_api_key: false }), loading = ref(false), error = ref(''), draft = ref('')
const scopeMode = ref('selected'), selectedDocuments = ref([]), thinkingMode = ref('disabled'), streamError = ref(false)
const starting = ref(false), followLatest = ref(true), pendingSubmission = ref(null)
const submissionPending = computed(() => Boolean(pendingSubmission.value))
const cancelling = ref(false), pollError = ref(false), savingArtifact = ref(''), conversationRef = ref(null)
const pickerVisible = ref(false), documentKeyword = ref(''), documentOptions = ref([]), documentSearching = ref(false), documentError = ref(''), documentPage = ref(1), documentTotal = ref(0)
const sourceVisible = ref(false), selectedSource = ref(null)
const isRunning = computed(() => Boolean(run.value && ['queued', 'running'].includes(run.value.state)))
const isPaused = computed(() => run.value?.state === 'paused')
const partialAnswer = computed(() => run.value?.state !== 'completed' ? taskPreview(run.value) : '')
const usageMayBeIncomplete = computed(() => ['failed', 'cancelled', 'interrupted'].includes(run.value?.state) || run.value?.usage?.token_usage_complete === false)
const isBlocked = computed(() => isRunning.value)
const canRetryRun = computed(() => ['failed', 'interrupted'].includes(run.value?.state))
const conversationItems = computed(() => {
  const byTask = new Map(tasks.value.map(task => [task.id, task]))
  const answered = new Set(messages.value.filter(message => message.role === 'assistant').map(message => message.task_id))
  return messages.value.flatMap(message => {
    const task = byTask.get(message.task_id), content = taskPreview(task)
    if (message.role !== 'user' || !content || task.state === 'completed' || answered.has(task.id)) return [message]
    return [message, { id: `${task.id}-partial`, task_id: task.id, role: 'assistant', content, sources: task.sources || [], incomplete: true, pending: ['queued', 'running'].includes(task.state) }]
  })
})
const artifactTasks = computed(() => tasks.value.filter(task => !['queued', 'running'].includes(task.state) && task.artifacts?.length))
const budgetFields = [
  { key: 'model_requests', label: '模型请求次数', testid: 'partner-budget-model-requests', step: 1 },
  { key: 'tool_calls', label: '工具调用次数', testid: 'partner-budget-tool-calls', step: 1 },
  { key: 'output_tokens', label: '输出 token 数', testid: 'partner-budget-output-tokens', step: 1000 },
  { key: 'time_seconds', label: '执行时长（秒）', testid: 'partner-budget-time-seconds', step: 60 },
]
const budgetPolicy = ref(null), budgetMode = ref('standard'), customBudget = ref({}), budgetReady = ref(false)
const budgetError = computed(() => validateBudget())
const budgetLimits = computed(() => budgetMode.value === 'custom' ? customBudget.value : isPresetMode(budgetMode.value) ? budgetPolicy.value.presets[budgetMode.value] : null)
const budgetPreferenceKey = 'teamhub.partner.budget.v1'
let generation = 0, controller = new AbortController(), streamController, timer, searchGeneration = 0, taskRevision = 0, reconnectAttempt = 0
let submissionTimer, submissionAttempt = 0, reconciliationSequence = 0
// 未知提交按库和会话保留，编辑草稿、切换会话都不能替换其幂等身份。
const pendingSubmissions = pendingPartnerSubmissions
const saveRequestIds = new Map()

function current(value) { return value === generation }
function stopSubmissionChecks() { clearTimeout(submissionTimer); submissionTimer = undefined; reconciliationSequence++ }
function clearPendingRequests() {
  stopSubmissionChecks()
  if (pendingSubmission.value) pendingSubmissions.delete(pendingSubmission.value.key)
  pendingSubmission.value = null; submissionAttempt = 0
}
function detachPendingSubmission() { stopSubmissionChecks(); pendingSubmission.value = null; submissionAttempt = 0 }
function submissionKey(sessionId) { return JSON.stringify([appStore.libraryPath, sessionId]) }
function rememberSubmission(kind, body, taskId = null) {
  const sessionId = activeSession.value.id
  if (body.document_ids) Object.freeze(body.document_ids)
  if (body.budget) Object.freeze(body.budget)
  const submission = Object.freeze({ kind, sessionId, taskId, body: Object.freeze(body), draft: draft.value, key: submissionKey(sessionId) })
  pendingSubmissions.set(submission.key, submission); pendingSubmission.value = submission
  return submission
}
function stopPolling() { clearTimeout(timer); timer = undefined }
function stopUpdates() { stopPolling(); streamController?.abort(); streamController = undefined }
function resetContext() {
  generation++; controller.abort(); controller = new AbortController(); stopUpdates(); searchGeneration++; taskRevision++
  detachPendingSubmission(); followLatest.value = true; reconnectAttempt = 0; thinkingMode.value = 'disabled'; messages.value = []; tasks.value = []; run.value = null; activeSession.value = null; draft.value = ''; scopeMode.value = 'selected'; selectedDocuments.value = []
  error.value = ''; starting.value = false; cancelling.value = false; savingArtifact.value = ''; pollError.value = false; streamError.value = false
  pickerVisible.value = false; documentOptions.value = []; documentError.value = ''; documentSearching.value = false; sourceVisible.value = false; selectedSource.value = null; saveRequestIds.clear()
  return generation
}
function isPresetMode(mode, policy = budgetPolicy.value) {
  return ['quick', 'standard', 'deep'].includes(mode) && Object.hasOwn(policy?.presets || {}, mode)
}
function validateBudget() {
  if (!budgetReady.value || !budgetPolicy.value) return '正在获取任务预算策略。'
  if (budgetMode.value !== 'custom') return isPresetMode(budgetMode.value) ? '' : '请选择有效的任务预算。'
  for (const { key, label } of budgetFields) {
    const value = customBudget.value[key], bound = budgetPolicy.value.bounds?.[key]
    if (!bound || !Number.isInteger(value) || value < bound.min || value > bound.max) return `${label}须为 ${bound?.min ?? '允许范围'} 至 ${bound?.max ?? '上限'} 内的整数。`
  }
  return ''
}
function budgetPayload() {
  return budgetMode.value === 'custom' ? { mode: 'custom', ...Object.fromEntries(budgetFields.map(({ key }) => [key, customBudget.value[key]])) } : { mode: budgetMode.value }
}
function loadBudgetPolicy(policy, defaults) {
  if (!isPresetMode(policy?.default_mode, policy) || !budgetFields.every(({ key }) => Number.isInteger(policy.bounds?.[key]?.min) && Number.isInteger(policy.bounds?.[key]?.max))) throw new Error('任务预算策略不完整，请重新打开 AI 伙伴。')
  budgetReady.value = false; budgetPolicy.value = policy; budgetMode.value = policy.default_mode; customBudget.value = { ...policy.presets[policy.default_mode] }
  if (defaults?.mode === 'custom' && budgetFields.every(({ key }) => Number.isInteger(defaults[key]) && defaults[key] >= policy.bounds[key].min && defaults[key] <= policy.bounds[key].max)) { budgetMode.value = 'custom'; customBudget.value = Object.fromEntries(budgetFields.map(({ key }) => [key, defaults[key]])) }
  try {
    const stored = JSON.parse(localStorage.getItem(budgetPreferenceKey) || 'null')
    const keys = stored && typeof stored === 'object' && !Array.isArray(stored) ? Object.keys(stored) : []
    if (stored?.mode === 'custom' && keys.length === 5 && keys.every(key => key === 'mode' || budgetFields.some(field => field.key === key)) && budgetFields.every(({ key }) => Number.isInteger(stored[key]) && stored[key] >= policy.bounds[key].min && stored[key] <= policy.bounds[key].max)) {
      budgetMode.value = 'custom'; customBudget.value = Object.fromEntries(budgetFields.map(({ key }) => [key, stored[key]]))
    } else if (keys.length === 1 && isPresetMode(stored?.mode, policy)) budgetMode.value = stored.mode
    else if (stored !== null) localStorage.removeItem(budgetPreferenceKey)
  } catch { /* 浏览器禁用存储或旧偏好损坏时，使用服务端默认值。 */ }
  budgetReady.value = true
}
function persistBudget() {
  if (budgetError.value) return
  try { localStorage.setItem(budgetPreferenceKey, JSON.stringify(budgetPayload())) } catch { /* 偏好保存失败不影响本轮任务。 */ }
}
function onConversationScroll() {
  const element = conversationRef.value
  if (element) followLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight < 100
}
function scrollConversation(force = false) {
  if (!force && !followLatest.value) return
  const value = generation
  nextTick(() => { const element = conversationRef.value; if (current(value) && element && (force || followLatest.value)) element.scrollTop = element.scrollHeight })
}
function followConversation() { followLatest.value = true; scrollConversation(true) }
function artifactTasksForMessage(message) {
  const task = artifactTasks.value.find(item => item.id === message.task_id)
  if (!task) return []
  const hasAnswer = Boolean(taskPreview(task)) || messages.value.some(item => item.task_id === task.id && item.role === 'assistant')
  return message.role === 'assistant' || !hasAnswer ? [task] : []
}
function taskPreview(task) { if (task?.preview_discarded) return ''; return typeof task?.answer_preview === 'string' && task.answer_preview ? task.answer_preview : typeof task?.answer === 'string' ? task.answer : '' }
async function applySession(detail) {
  const value = generation
  activeSession.value = detail.session; messages.value = detail.messages; tasks.value = detail.tasks
  run.value = detail.tasks.find(task => task.id === detail.session.active_task_id) || detail.tasks.find(task => ['queued', 'running', 'paused'].includes(task.state)) || detail.tasks[0] || null
  const options = detail.chat_options || {}
  scopeMode.value = options.scope_mode === 'library' ? 'library' : 'selected'
  thinkingMode.value = options.thinking_mode === 'enabled' ? 'enabled' : 'disabled'
  if (scopeMode.value === 'selected' && Array.isArray(options.document_ids) && options.document_ids.length) {
    const results = await Promise.allSettled(options.document_ids.slice(0, 100).map(id => getDocument(id)))
    if (!current(value)) return
    selectedDocuments.value = results.filter(item => item.status === 'fulfilled' && isUsableDocument(item.value)).map(item => item.value)
    if (selectedDocuments.value.length !== results.length) error.value = '部分原选资料已不可用，本次讨论会使用其余资料。'
  }
  // 老会话可能只有任务快照，没有独立的未完成消息。
  for (const task of detail.tasks) if (task.message && !messages.value.some(message => message.task_id === task.id && message.role === 'user')) messages.value.push({ id: `${task.id}-user`, role: 'user', content: task.message, task_id: task.id, sources: [] })
  pendingSubmission.value = pendingSubmissions.get(submissionKey(detail.session.id)) || null
  if (submissionPending.value) scheduleSubmissionCheck(pendingSubmission.value, value)
  else if (isRunning.value) startUpdates(run.value.id, value)
  scrollConversation(true)
}
async function initialize(contextDocumentId = null) {
  const value = resetContext(); sessions.value = []; settings.value = { has_api_key: false }; loading.value = true
  budgetReady.value = false
  try {
    const [configuration, listed, policy] = await Promise.all([getPartnerChatConfig(controller.signal), listPartnerSessions(controller.signal), getPartnerBudgetPolicy(controller.signal)])
    if (!current(value)) return
    loadBudgetPolicy(policy, configuration.defaults?.budget)
    settings.value = configuration; sessions.value = listed.items
    if (contextDocumentId) {
      const created = await createPartnerSession(undefined, controller.signal)
      if (!current(value)) return
      activeSession.value = created; sessions.value = [created, ...sessions.value]
      await addContextDocument(contextDocumentId, value)
    } else if (listed.items.length) {
      const detail = await getPartnerSession(listed.items[0].id, controller.signal)
      if (current(value)) await applySession(detail)
    } else {
      const created = await createPartnerSession(undefined, controller.signal)
      if (current(value)) { activeSession.value = created; sessions.value = [created] }
    }
  } catch (e) { if (current(value)) error.value = e.message || '无法读取 AI 对话，请重试。' }
  finally { if (current(value)) loading.value = false }
}
async function selectSession(id) {
  if (id === activeSession.value?.id) return
  const value = resetContext(); loading.value = true
  try { const detail = await getPartnerSession(id, controller.signal); if (current(value)) await applySession(detail) }
  catch (e) { if (current(value)) error.value = e.message || '无法读取这段对话。' }
  finally { if (current(value)) loading.value = false }
}
async function newSession() {
  const value = resetContext(); loading.value = true
  try {
    const created = await createPartnerSession(undefined, controller.signal)
    if (!current(value)) return null
    activeSession.value = created; sessions.value = [created, ...sessions.value]
    return { generation: value, sessionId: created.id }
  }
  catch (e) { if (current(value)) error.value = e.message || '新建对话失败。' }
  finally { if (current(value)) loading.value = false }
  return null
}
async function removeSession(item) {
  const value = generation
  try { await ElMessageBox.confirm('删除这段对话及其记录？已收录的成果会保留在个人库中。', '删除对话', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }) } catch { return }
  if (!current(value)) return
  try {
    await deletePartnerSession(item.id, controller.signal)
    if (!current(value)) return
    sessions.value = sessions.value.filter(session => session.id !== item.id)
    if (activeSession.value?.id === item.id) { if (sessions.value.length) await selectSession(sessions.value[0].id); else await newSession() }
  } catch (e) { if (current(value)) error.value = e.message || '删除对话失败，请先停止正在运行的任务。' }
}
async function addContextDocument(id, value = generation) {
  try {
    const doc = await getDocument(id)
    if (!current(value)) return
    if (!isUsableDocument(doc)) { error.value = '这份资料暂无可讨论的已保存版本，请选择其他资料。'; return }
    if (!selectedDocuments.value.some(item => item.id === id)) selectedDocuments.value.push(doc)
  } catch (e) { if (current(value)) error.value = e.message || '无法载入所选资料。' }
}
function isUsableDocument(doc) { return !doc.is_folder && ['pending', 'organized'].includes(doc.status) && Boolean(doc.current_version?.id) }
function errorCode(error) { return error?.response?.data?.detail?.error?.code || error?.response?.data?.error?.code || error?.code }
function serviceChanged(error) {
  return ['AI_SERVICE_CHANGED', 'AI_CONFIG_CHANGED', 'AI_CONFIGURATION_CHANGED', 'PARTNER_SERVICE_CHANGED'].includes(errorCode(error)) || /服务.*(变化|变更|更新)/.test(error?.message || '')
}
function submissionIsCurrent(submission, value) { return current(value) && pendingSubmission.value === submission && activeSession.value?.id === submission.sessionId }
function scheduleSubmissionCheck(submission, value) {
  clearTimeout(submissionTimer)
  if (!submissionIsCurrent(submission, value)) return
  submissionTimer = setTimeout(() => reconcileSubmission(submission, value), Math.min(1000 * 2 ** Math.min(submissionAttempt, 4), 15000))
}
function acceptSubmission(task, submission, value) {
  if (!submissionIsCurrent(submission, value)) return
  if (task.session_id !== submission.sessionId || !task.id || (submission.kind === 'resume' && task.id !== submission.taskId)) throw new Error('返回的对话状态不一致，正在核对原请求。')
  taskRevision++; clearPendingRequests(); error.value = ''
  // 对账期间用户可能已编写下一个问题，只清除仍与本次提交相同的草稿。
  if (submission.kind === 'send' && draft.value === submission.draft) draft.value = ''
  applyTask(task)
  if (isRunning.value) startUpdates(task.id, value)
  void refreshSessionTitles(value, submission.sessionId)
}
async function reconcileSubmission(submission = pendingSubmission.value, value = generation) {
  if (!submission || !submissionIsCurrent(submission, value) || starting.value) return
  clearTimeout(submissionTimer); submissionTimer = undefined
  const sequence = ++reconciliationSequence
  try {
    const task = submission.kind === 'send'
      ? await getPartnerRequestTask(submission.sessionId, submission.body.request_id, controller.signal)
      : await getPartnerTask(submission.taskId, controller.signal)
    if (!submissionIsCurrent(submission, value) || sequence !== reconciliationSequence) return
    if (submission.kind === 'send' && task.request_id !== submission.body.request_id) throw new Error('请求编号不一致，继续核对原请求。')
    if (submission.kind === 'resume' && task.state === 'paused' && task.last_request_id !== submission.body.request_id) {
      // paused 可能是原 POST 尚未到达，不能据此丢弃续行身份。
      submissionAttempt++; scheduleSubmissionCheck(submission, value); return
    }
    acceptSubmission(task, submission, value)
  } catch (cause) {
    if (!submissionIsCurrent(submission, value) || sequence !== reconciliationSequence) return
    const status = cause?.response?.status
    if ([401, 403, 410].includes(status)) { error.value = cause.message || '暂时无法核对发送状态，请恢复访问后重试本次发送。'; return }
    // 404 只说明本次查询时尚未登记；可能早于仍在处理的 POST。
    submissionAttempt++; scheduleSubmissionCheck(submission, value)
  }
}
async function followExistingTask(submission, value) {
  const task = submission.kind === 'resume'
    ? await getPartnerTask(submission.taskId, controller.signal)
    : (await getPartnerSession(submission.sessionId, controller.signal)).tasks.find(item => ['queued', 'running', 'paused'].includes(item.state))
  if (!current(value) || activeSession.value?.id !== submission.sessionId) return
  if (task) { taskRevision++; applyTask(task); if (isRunning.value) startUpdates(task.id, value) }
}
async function handleSubmissionError(cause, submission, value) {
  if (!submissionIsCurrent(submission, value)) return
  const status = cause?.response?.status, changed = serviceChanged(cause)
  const definite = changed || (status >= 400 && status < 500 && status !== 408)
  if (!definite) {
    error.value = ''
    scheduleSubmissionCheck(submission, value)
    return
  }
  clearPendingRequests(); error.value = cause.message || '本次请求未被接受，请调整后重试。'
  if (['PARTNER_SESSION_BUSY', 'PARTNER_NOT_RESUMABLE'].includes(errorCode(cause))) {
    try { await followExistingTask(submission, value) }
    catch { if (current(value)) error.value = '当前任务状态已变化，暂未获取最新进度，请重新打开这段对话。' }
  }
  if (changed) {
    try {
      const configuration = await getPartnerChatConfig(controller.signal)
      if (!current(value)) return
      settings.value = configuration
      error.value = 'AI 服务配置已更新，请核对输入框下方的服务后再次发送。'
    } catch { if (current(value)) error.value = 'AI 服务配置已变化，暂时无法读取新配置。请重新打开 AI 伙伴后发送。' }
  }
}
async function submitRequest(submission, value) {
  starting.value = true; error.value = ''; stopSubmissionChecks(); followConversation()
  try {
    const task = submission.kind === 'send'
      ? await sendPartnerMessage(submission.sessionId, submission.body, controller.signal)
      : await resumePartnerTask(submission.taskId, submission.body, controller.signal)
    acceptSubmission(task, submission, value)
  } catch (cause) { await handleSubmissionError(cause, submission, value) }
  finally { if (current(value)) starting.value = false }
}
async function retryPendingSubmission() {
  const submission = pendingSubmission.value
  if (!submission || starting.value || cancelling.value || loading.value) return
  await submitRequest(submission, generation)
}
async function handleComposerEnter(event) {
  if (event.isComposing || event.keyCode === 229 || event.shiftKey) return
  event.preventDefault()
  await sendMessage()
}
async function sendMessage() {
  if (submissionPending.value || starting.value || cancelling.value || isRunning.value || loading.value || budgetError.value || !draft.value.trim() || !settings.value.has_api_key || !settings.value.service_revision || !activeSession.value) return
  const submission = rememberSubmission('send', { message: draft.value.trim(), scope_mode: scopeMode.value, document_ids: scopeMode.value === 'selected' ? selectedDocuments.value.map(doc => doc.id) : [], thinking_mode: thinkingMode.value, budget: budgetPayload(), request_id: crypto.randomUUID(), expected_service_revision: settings.value.service_revision, consent: true })
  await submitRequest(submission, generation)
}
async function resumeRun() {
  if (submissionPending.value || !isPaused.value || !run.value.pause?.resumable || starting.value || cancelling.value || loading.value || budgetError.value || !settings.value.has_api_key || !settings.value.service_revision) return
  const submission = rememberSubmission('resume', { request_id: crypto.randomUUID(), expected_service_revision: settings.value.service_revision, consent: true, budget: budgetPayload() }, run.value.id)
  await submitRequest(submission, generation)
}
async function retryRun() { if (canRetryRun.value && !submissionPending.value && !starting.value && !draft.value.trim()) { draft.value = run.value.message; await sendMessage() } }
async function refreshSessionTitles(value, sessionId) {
  try {
    const listed = await listPartnerSessions(controller.signal)
    if (!current(value) || activeSession.value?.id !== sessionId) return
    sessions.value = listed.items
    const updated = listed.items.find(item => item.id === sessionId)
    if (updated) activeSession.value = { ...activeSession.value, title: updated.title, updated_at: updated.updated_at }
  } catch {
    // 标题稍后随会话读取恢复；不让附属信息刷新打断正在进行的回答。
  }
}
function applyTask(task) {
  const previous = tasks.value.find(item => item.id === task.id)
  if (task.state !== 'completed' && !task.preview_discarded && !taskPreview(task) && taskPreview(previous)) task = { ...task, answer_preview: taskPreview(previous) }
  run.value = task
  const index = tasks.value.findIndex(item => item.id === task.id)
  if (index < 0) tasks.value.push(task); else tasks.value.splice(index, 1, task)
  if (!messages.value.some(message => message.task_id === task.id && message.role === 'user')) messages.value.push({ id: `${task.id}-user`, role: 'user', content: task.message, sources: [], task_id: task.id })
  if (task.state === 'completed' && task.answer && !messages.value.some(message => message.task_id === task.id && message.role === 'assistant')) messages.value.push({ id: `${task.id}-assistant`, role: 'assistant', content: task.answer, sources: task.sources || [], task_id: task.id })
  if (!['queued', 'running'].includes(task.state)) stopUpdates()
  scrollConversation()
}
function schedulePoll(id, value) {
  stopPolling()
  const delay = Math.min(1000 * 2 ** Math.min(reconnectAttempt, 4), 15000)
  timer = setTimeout(() => pollTask(id, value), delay)
}
function startUpdates(id, value) {
  stopUpdates(); streamError.value = false
  const connection = new AbortController(), revision = taskRevision
  streamController = connection
  const valid = () => current(value) && run.value?.id === id && revision === taskRevision && !connection.signal.aborted
  void streamPartnerTask(id, task => { if (valid()) { reconnectAttempt = 0; pollError.value = false; applyTask(task) } }, connection.signal)
    .then(() => { if (valid() && isRunning.value) { streamError.value = true; schedulePoll(id, value) } })
    .catch(() => { if (valid() && isRunning.value) { streamError.value = true; schedulePoll(id, value) } })
}
async function pollTask(id, value) {
  const revision = taskRevision
  try {
    const task = await getPartnerTask(id, controller.signal)
    if (!current(value) || run.value?.id !== id || revision !== taskRevision) return
    reconnectAttempt = 0; pollError.value = false; applyTask(task)
    if (isRunning.value) startUpdates(id, value)
  } catch (cause) {
    if (!current(value) || run.value?.id !== id || revision !== taskRevision) return
    pollError.value = true; streamError.value = true
    const status = cause?.response?.status
    if ([401, 403, 404, 410].includes(status)) { stopUpdates(); error.value = cause.message || '当前任务暂不可访问，请重新打开对话。'; return }
    reconnectAttempt++; schedulePoll(id, value)
  }
}
function refreshRun() { if (run.value && isRunning.value && !cancelling.value && !starting.value) { error.value = ''; stopUpdates(); taskRevision++; reconnectAttempt = 0; pollTask(run.value.id, generation) } }
async function cancelRun() {
  if (submissionPending.value || (!isRunning.value && !isPaused.value) || cancelling.value || starting.value) return
  const value = generation, id = run.value.id; cancelling.value = true; error.value = ''; stopUpdates(); taskRevision++; clearPendingRequests()
  try { const task = await cancelPartnerTask(id, controller.signal); if (current(value) && run.value?.id === id) { applyTask(task); if (isRunning.value) startUpdates(id, value) } }
  catch (cause) { if (current(value)) { error.value = cause.message || '停止任务暂未确认，正在重新获取状态。'; pollError.value = true; streamError.value = true; schedulePoll(id, value) } }
  finally { if (current(value)) cancelling.value = false }
}
async function saveArtifact(task, artifact) {
  if (savingArtifact.value || artifact.saved_document_id || ['queued', 'running'].includes(task.state)) return
  const value = generation, key = `${task.id}:${artifact.id}`
  try { await ElMessageBox.confirm(`将“${artifact.name}”作为新资料收录到待整理？`, '收录生成成果', { confirmButtonText: '确认收录', cancelButtonText: '取消' }) } catch { return }
  if (!current(value)) return
  savingArtifact.value = key; error.value = ''
  if (!saveRequestIds.has(key)) saveRequestIds.set(key, crypto.randomUUID())
  try {
    const result = await savePartnerArtifact(task.id, artifact.id, saveRequestIds.get(key), controller.signal)
    if (!current(value)) return
    for (const item of tasks.value) if (item.id === task.id) for (const output of item.artifacts) if (output.id === artifact.id) output.saved_document_id = result.document_id
    if (run.value?.id === task.id) for (const output of run.value.artifacts) if (output.id === artifact.id) output.saved_document_id = result.document_id
    if (!result.duplicate && typeof appStore.pendingCount === 'number') appStore.pendingCount++
    ElMessage.success(result.duplicate ? '相同内容已存在于个人库，可前往资料查看。' : '成果已收录到待整理。')
  } catch (e) { if (current(value)) error.value = e.message || '成果收录失败，可以重试。' }
  finally { if (current(value)) savingArtifact.value = '' }
}
function openSavedArtifact(artifact) { router.push({ path: '/library', query: { highlight: artifact.saved_document_id } }) }
function openSource(source) { selectedSource.value = source; sourceVisible.value = true }
function openSourceDocument() { if (selectedSource.value) router.push({ path: '/library', query: { highlight: selectedSource.value.document_id } }) }
function taskStateLabel(state) { return ({ queued: '任务排队中', running: '伙伴正在工作', paused: '任务已暂停，进度已保留', completed: '本轮任务完成', failed: '本轮任务未完成', cancelled: '本轮任务已停止', interrupted: '任务因服务中断而停止' })[state] || '任务状态' }
function formatUsage(usage) {
  if (!usage || !Object.keys(usage).length) return '本段用量未完整取得，已发请求可能计费。'
  const input = Number.isFinite(usage.input_tokens) ? `${usage.input_tokens} 输入 tokens` : '输入 tokens 未取得'
  const recorded = `${usage.model_requests || 0} 次模型请求 · ${usage.tool_calls || 0} 次工具调用 · ${input} · ${usage.output_tokens || 0} 输出 tokens · ${Math.ceil(usage.time_seconds || 0)} 秒`
  return usageMayBeIncomplete.value ? `${recorded}（本段用量未完整取得，已发请求可能计费）` : recorded
}
function readingProgressText(progress) { return `已读 ${progress.read_units || 0} 个原文单元 · ${progress.visual_pages || 0} 个图像页${progress.embedded_images ? ` · ${progress.embedded_images} 张内嵌图` : ''} · 已保留 ${progress.text_characters || 0} 字原文证据` }
function stepKindLabel(kind) { return ({ search: '查找', read: '阅读', vision: '看图', visual: '看图', warning: '提示', draft: '成果', status: '进度' })[kind] || '进度' }
function removeScopeDocument(id) { if (!isBlocked.value && !starting.value) selectedDocuments.value = selectedDocuments.value.filter(doc => doc.id !== id) }
function toggleScopeDocument(doc, selected) {
  if (selected) { if (selectedDocuments.value.length >= 100) { documentError.value = '每轮最多选择 100 份资料。'; return }; if (!selectedDocuments.value.some(item => item.id === doc.id)) selectedDocuments.value.push(doc) }
  else removeScopeDocument(doc.id)
}
function openDocumentPicker() { pickerVisible.value = true; documentKeyword.value = ''; documentPage.value = 1; searchScopeDocuments() }
async function searchScopeDocuments() {
  const value = generation, searchValue = ++searchGeneration; documentSearching.value = true; documentError.value = ''
  try {
    const result = await searchDocuments({ keyword: documentKeyword.value || undefined, is_folder: false, page: documentPage.value, page_size: 30 })
    if (!current(value) || searchValue !== searchGeneration) return
    documentOptions.value = result.documents.filter(isUsableDocument); documentTotal.value = result.total
  } catch (e) { if (current(value) && searchValue === searchGeneration) documentError.value = e.message || '查找资料失败，请重试。' }
  finally { if (current(value) && searchValue === searchGeneration) documentSearching.value = false }
}
watch(() => JSON.stringify(budgetPayload()), persistBudget)
watch(documentKeyword, () => { documentPage.value = 1 })
watch(() => appStore.libraryPath, () => {
  if (route.query.doc_id) {
    const query = { ...route.query }; delete query.doc_id
    router.replace({ path: route.path, query })
  }
  initialize()
})
watch(() => route.query.doc_id, async (id, oldId) => {
  if (!id || id === oldId) return
  const context = await newSession()
  if (context && current(context.generation) && activeSession.value?.id === context.sessionId && route.query.doc_id === id) await addContextDocument(String(id), context.generation)
})
onMounted(() => initialize(route.query.doc_id ? String(route.query.doc_id) : null))
onUnmounted(() => { generation++; controller.abort(); stopUpdates(); detachPendingSubmission() })
</script>

<style scoped>
.partner-page { display:grid; grid-template-columns:210px minmax(0,1fr); min-height:660px; height:calc(100vh - 150px); max-height:1100px; background:#fff; border:1px solid #e8edf3; border-radius:18px; overflow:hidden; box-shadow:0 4px 22px #263c5a05; }
.conversation-sidebar { background:#f9fafc; border-right:1px solid #e9edf2; padding:20px 12px; display:flex; flex-direction:column; min-width:0; }
.sidebar-heading { display:flex; align-items:center; justify-content:space-between; padding:0 2px 20px; font-size:14px; font-weight:600; color:#4a586c; gap:8px; }
.session-list { flex:1; overflow:auto; }.session-row { display:flex; align-items:center; border-radius:9px; margin-bottom:5px; }.session-row.selected { background:#eaf2ff; }.session-select { flex:1; min-width:0; text-align:left; padding:12px 10px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; background:none; border:none; cursor:pointer; color:#536174; font:inherit; font-size:13px; }.selected .session-select { color:#2778dc; font-weight:600; }.session-delete { border:none; background:none; color:#9aa6b5; cursor:pointer; font-size:18px; padding:8px; }.session-delete:hover { color:#d75656; }.sidebar-note { margin:18px 4px 0; color:#909bab; font-size:11px; line-height:1.8; }.sidebar-empty { padding:0 8px; }
.partner-main { position:relative; min-width:0; display:flex; flex-direction:column; overflow:hidden; }.partner-heading { display:flex; align-items:center; justify-content:space-between; padding:23px 28px 18px; border-bottom:1px solid #eff2f6; gap:16px; }.partner-heading h2 { font-size:20px; color:#26364b; margin:0 0 6px; }.partner-heading p { margin:0; color:#8591a2; font-size:12px; line-height:1.6; }.partner-alert { margin:12px 24px 0; width:auto; }
.conversation { flex:1; overflow:auto; padding:24px 28px; min-height:200px;  }.conversation-welcome { max-width:560px; margin:36px auto; text-align:center; }.partner-emblem { margin:0 auto 16px; display:grid; place-items:center; background:linear-gradient(135deg,#eaf3ff,#f1edff); color:#6188e9; width:58px; height:58px; border-radius:18px; font-size:30px; }.conversation-welcome h3 { margin:0 0 12px; color:#34465e; font-size:22px; }.conversation-welcome p { color:#8a96a6; line-height:1.8; font-size:13px; }.example-prompts { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; margin-top:26px; }.example-prompts button { background:white; border:1px solid #e3eaf3; color:#62768f; border-radius:9px; padding:10px 12px; font-size:12px; cursor:pointer; }.example-prompts button:hover { border-color:#9abffc; color:#3987e8; }
.chat-message { margin:0 0 25px; }.chat-message.user { padding:16px 18px; background:#f2f6fc; border-radius:12px; margin-left:40px; }.message-role { color:#4f78a8; font-size:12px; font-weight:600; margin-bottom:9px; }.message-body { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.85; font-size:14px; color:#35455a; }.message-sources { display:flex; flex-wrap:wrap; gap:7px; margin-top:14px; align-items:center; }.source-chip { border:1px solid #dfe8f3; background:#fafcff; color:#5075a5; font-size:11px; padding:5px 8px; border-radius:6px; cursor:pointer; text-align:left; line-height:1.6; }
.task-progress { margin:15px 0 24px; padding:14px 17px; background:#f6f9fe; border:1px solid #e9eff9; border-radius:10px; }.task-heading { display:flex; justify-content:space-between; align-items:center; color:#5a7294; font-size:12px; }.task-steps { margin:10px 0 0; padding:0; list-style:none; font-size:12px; color:#7c899a; line-height:1.8; max-height:170px; overflow:auto; }.task-steps li { margin:5px 0; overflow-wrap:anywhere; }.task-steps li span { color:#7e9ac0; margin-right:9px; }.task-stopped { background:#fbfcfe; }.task-error { color:#c06458; font-size:12px; line-height:1.7; }
.artifact-card { border:1px solid #dfe8f3; border-radius:11px; padding:16px; margin-bottom:14px; }.artifact-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; }.artifact-type { color:#8a98aa; font-size:10px; }.artifact-heading h4 { margin:5px 0; color:#405874; font-size:14px; overflow-wrap:anywhere; }.artifact-card details { margin-top:12px; }.artifact-card summary { cursor:pointer; color:#72849b; font-size:12px; }.artifact-content,.source-excerpt { white-space:pre-wrap; overflow-wrap:anywhere; font-family:inherit; line-height:1.8; background:#f8fafc; border-radius:8px; padding:16px; color:#4c5a6c; max-height:380px; overflow:auto; font-size:13px; }
.composer { max-height:48%; overflow-y:auto; flex-shrink:0; padding:16px 24px 20px; border-top:1px solid #edf1f6; background:#fff; }.scope-toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }.scope-label { font-size:12px; color:#697c94; }.scope-description { font-size:11px; color:#91a0b2; margin:10px 0 12px; line-height:1.7; }.selected-documents { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; max-height:80px; overflow:auto; }.selected-documents :deep(.el-tag) { max-width:100%; }.selected-documents :deep(.el-tag__content) { overflow:hidden; text-overflow:ellipsis; }.composer :deep(.el-textarea__inner) { box-shadow:0 0 0 1px #e0e7f1 inset; padding:13px 15px; border-radius:11px; font-family:inherit; font-size:14px; line-height:1.7; }.composer-footer { display:flex; justify-content:space-between; align-items:center; margin-top:12px; gap:12px; }.quiet { color:#8b97a7; font-size:12px; line-height:1.7; }.composer-footer .quiet { font-size:10px; }
.dialog-intro { color:#7a8798; font-size:12px; line-height:1.8; margin:0 0 16px; }.picker-alert { margin-top:12px; }.document-options { min-height:180px; max-height:340px; overflow:auto; margin:16px 0; }.document-option { display:flex; align-items:center; gap:12px; padding:10px 3px; border-bottom:1px solid #f1f4f8; font-size:13px; color:#53657d; cursor:pointer; }.document-option span { min-width:0; overflow-wrap:anywhere; }.document-option small { display:block; color:#99a5b4; font-size:11px; margin-top:5px; }.picker-count { margin-right:15px; }.source-title { margin:0 0 8px; font-size:17px; color:#41556e; overflow-wrap:anywhere; }.source-version-note { color:#9aa6b5; font-size:11px; line-height:1.8; }


.task-details { margin-top:10px; }.task-details summary { color:#7c899a; font-size:12px; line-height:1.8; cursor:pointer; overflow-wrap:anywhere; }.task-details summary span { color:#6d8cb3; white-space:nowrap; }.task-details .task-steps { padding:8px 0 0 12px; border-left:2px solid #e3ebf6; }
.artifact-preview { margin-top:15px; max-height:380px; overflow:auto; }
.reading-formats { margin:22px auto 0; text-align:left; padding:13px 16px; border:1px solid #e5ebf3; border-radius:10px; background:#fbfcfe; }.reading-formats summary { color:#667f9d; font-size:12px; cursor:pointer; }.reading-formats p { margin:9px 0 0; font-size:12px; }
.budget-controls { margin:0 0 12px; padding:10px 12px; border-radius:9px; background:#f8fafc; }.budget-toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }.budget-toolbar .quiet { font-size:10px; }.budget-summary { color:#8996a8; font-size:10px; line-height:1.7; margin:8px 0 0; }.budget-custom { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:9px 16px; margin-top:12px; }.budget-custom label { display:flex; gap:8px; align-items:center; justify-content:space-between; color:#6e7f96; font-size:11px; }.budget-custom :deep(.el-input-number) { width:125px; }.budget-error { color:#c06458; font-size:11px; }.task-usage { color:#8996aa; font-size:10px; line-height:1.8; margin-bottom:0; }.task-pause>p { color:#8b744d; font-size:12px; line-height:1.8; }.task-pause>.quiet { color:#929cac; font-size:11px; }

@media(max-width:1100px) { .partner-page { grid-template-columns:170px minmax(0,1fr); }.partner-heading { padding:20px; }.conversation { padding:20px; }.composer { max-height:48%; overflow-y:auto; flex-shrink:0; padding:15px 20px; }.sidebar-heading { flex-wrap:wrap; } }
@media(max-width:760px) { .partner-page { display:flex; flex-direction:column; min-height:730px; height:auto; max-height:none; }.conversation-sidebar { border-right:0; border-bottom:1px solid #e9edf2; padding:12px; }.sidebar-heading { padding-bottom:10px; }.session-list { display:flex; max-height:65px; overflow:auto; gap:5px; }.session-row { min-width:130px; max-width:220px; }.sidebar-note { display:none; }.partner-main { min-height:660px; }.conversation { max-height:480px; }.composer-footer .quiet { max-width:170px; }.artifact-heading { align-items:flex-start; flex-direction:column; } }
.service-disclosure { display:flex; flex-wrap:wrap; gap:3px 9px; min-width:0; align-items:center; }.service-model { font-size:11px; font-weight:600; color:#657c99; }.service-destination { font-size:10px; color:#8a98aa; overflow-wrap:anywhere; }.service-disclosure>.quiet { flex-basis:100%; }.advanced-options { margin-top:9px; }.advanced-options>summary { font-size:10px; color:#8c9aac; cursor:pointer; }.advanced-options .budget-controls { margin:9px 0 0; }.incomplete-label { margin-left:9px; color:#8b97a7; font-weight:400; }.latest-button { align-self:center; flex-shrink:0; margin:-6px 0 8px; z-index:1; border:1px solid #dae5f5; border-radius:18px; padding:7px 14px; font-size:12px; color:#4d78ac; background:#fff; box-shadow:0 2px 12px #243c5b12; cursor:pointer; }.paused-actions { display:flex; gap:9px; margin-top:10px; }.task-stopped { padding:9px 12px; background:transparent; border-color:#edf1f5; }.scope-toolbar :deep(.el-switch) { margin-left:auto; }.scope-toolbar :deep(.el-switch__label) { font-size:11px; }
.submission-recovery { display:flex; flex-wrap:wrap; align-items:center; gap:6px; padding:9px 12px; margin-bottom:12px; border-radius:8px; background:#f1f6fd; color:#69809c; font-size:12px; line-height:1.6; }
.task-progress.task-completed { padding:0; margin:0 0 14px; background:transparent; border:0; }.task-completed .task-details { margin-top:0; }.task-completed .task-details summary { font-size:11px; color:#96a2b2; }
</style>
