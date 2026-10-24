import request from './request'

const options = (signal, timeout = 30000) => {
  const headers = { 'X-TeamHub-AI': '1' }
  const capability = window.electron?.aiCapability
  if (capability) headers['X-TeamHub-AI-Capability'] = capability
  return { headers, timeout, _silent: true, signal }
}

export const getAISettings = signal => request.get('/api/ai/settings', options(signal))
export const updateAISettings = (data, signal) => request.put('/api/ai/settings', data, options(signal))
export const testAIConnection = signal => request.post('/api/ai/test', {}, options(signal, 90000))
export const testAIVision = signal => request.post('/api/ai/test', { mode: 'vision' }, options(signal, 90000))
export const prepareAIDocument = (id, signal) => request.post(`/api/ai/documents/${encodeURIComponent(id)}/prepare`, {}, options(signal))
export const analyzeAIDocument = (preparationId, signal) => request.post('/api/ai/analyze', { preparation_id: preparationId, consent: true }, options(signal, 90000))
