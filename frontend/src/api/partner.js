import request from './request'
import { readPartnerEvents } from '../utils/partnerStream.js'

const options = signal => {
  const headers = { 'X-TeamHub-AI': '1' }
  const capability = window.electron?.aiCapability
  if (capability) headers['X-TeamHub-AI-Capability'] = capability
  return { headers, timeout: 30000, _silent: true, signal }
}
const sessionURL = id => `/api/ai/partner/sessions/${encodeURIComponent(id)}`
const taskURL = id => `/api/ai/partner/tasks/${encodeURIComponent(id)}`

export const getPartnerChatConfig = signal => request.get('/api/ai/partner/chat-config', options(signal))
export const sendPartnerMessage = (id, data, signal) => request.post(`${sessionURL(id)}/send`, data, options(signal))
export const resumePartnerTask = (id, data, signal) => request.post(`${taskURL(id)}/resume`, data, options(signal))
export const getPartnerRequestTask = (id, requestId, signal) => request.get(`${sessionURL(id)}/requests/${encodeURIComponent(requestId)}`, options(signal))
export const getPartnerBudgetPolicy = signal => request.get('/api/ai/partner/budget-policy', options(signal))
export const listPartnerSessions = signal => request.get('/api/ai/partner/sessions', options(signal))
export const createPartnerSession = (title, signal) => request.post('/api/ai/partner/sessions', title ? { title } : {}, options(signal))
export const getPartnerSession = (id, signal) => request.get(sessionURL(id), options(signal))
export const deletePartnerSession = (id, signal) => request.delete(sessionURL(id), options(signal))
export const preparePartnerTurn = (id, data, signal) => request.post(`${sessionURL(id)}/prepare`, data, options(signal))
export const startPartnerTurn = (id, preparationId, requestId, signal) => request.post(`${sessionURL(id)}/turns`, { preparation_id: preparationId, consent: true, request_id: requestId }, options(signal))
export const getPartnerTask = (id, signal) => request.get(taskURL(id), options(signal))
export async function streamPartnerTask(id, onTask, signal) {
  const connection = new AbortController()
  let idleTimer
  const abort = () => connection.abort()
  const active = () => { clearTimeout(idleTimer); idleTimer = setTimeout(abort, 45000) }
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) connection.abort()
  try {
    active()
    const base = request.defaults.baseURL.replace(/\/$/, '')
    const response = await fetch(`${base}${taskURL(id)}/events`, { method: 'GET', headers: { ...options(signal).headers, Accept: 'text/event-stream' }, signal: connection.signal, credentials: 'omit', redirect: 'error', cache: 'no-store' })
    if (!response.ok) throw new Error(`实时进度连接失败（${response.status}），将尝试读取任务状态。`)
    await readPartnerEvents(response, task => {
      if (task.id !== id) throw new Error('实时进度与当前任务不一致。')
      onTask(task)
    }, connection.signal, active)
  } finally {
    clearTimeout(idleTimer)
    signal?.removeEventListener('abort', abort)
    connection.abort()
  }
}
export const cancelPartnerTask = (id, signal) => request.post(`${taskURL(id)}/cancel`, {}, options(signal))
export const preparePartnerContinuation = (id, data, signal) => request.post(`${taskURL(id)}/prepare-continue`, data, options(signal))
export const continuePartnerTask = (id, preparationId, requestId, signal) => request.post(`${taskURL(id)}/continue`, { preparation_id: preparationId, consent: true, request_id: requestId }, options(signal))
export const savePartnerArtifact = (taskId, artifactId, requestId, signal) => request.post(`${taskURL(taskId)}/artifacts/${encodeURIComponent(artifactId)}/save`, { consent: true, request_id: requestId }, options(signal))
