import request from './request'

export function getIndexRequestHeaders() {
  const headers = { 'X-TeamHub-AI': '1' }
  const capability = window.electron?.aiCapability
  if (capability) headers['X-TeamHub-AI-Capability'] = capability
  return headers
}

const options = (signal, timeout = 30000) => ({ headers: getIndexRequestHeaders(), timeout, _silent: true, signal })

export const getDocumentIndex = (id, signal, { offset = 0, limit = 20 } = {}) =>
  request.get(`/api/ai/indexes/${encodeURIComponent(id)}`, { ...options(signal), params: { offset, limit } })
export const rebuildDocumentIndex = (id, signal) =>
  request.post(`/api/ai/indexes/${encodeURIComponent(id)}/rebuild`, {}, options(signal))
export const prepareIndexEnrichment = (id, signal) =>
  request.post(`/api/ai/indexes/${encodeURIComponent(id)}/prepare`, {}, options(signal))
export const enrichDocumentIndex = (preparationId, signal) =>
  request.post('/api/ai/indexes/enrich', { preparation_id: preparationId, consent: true }, options(signal, 90000))
