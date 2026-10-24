import request from './request'

const base = '/api/ai/cards'
const options = signal => {
  const headers = { 'X-TeamHub-AI': '1' }
  if (window.electron?.aiCapability) headers['X-TeamHub-AI-Capability'] = window.electron.aiCapability
  return { headers, signal, timeout: 30000, _silent: true }
}
const path = id => `${base}/${encodeURIComponent(id)}`

export const getDocumentCardsConfig = signal => request.get(`${base}/config`, options(signal))
export const getDocumentCard = (id, signal) => request.get(path(id), options(signal))
export const generateDocumentCard = (id, data, signal) => request.post(`${path(id)}/generate`, data, options(signal))
export const cancelDocumentCard = (id, data, signal) => request.post(`${path(id)}/cancel`, data, options(signal))
export const setDocumentCardPolicy = (id, data, signal) => request.put(`${path(id)}/policy`, data, options(signal))
export const saveDocumentCardAnnotation = (id, data, signal) => request.put(`${path(id)}/annotation`, data, options(signal))
