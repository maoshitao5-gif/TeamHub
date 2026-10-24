import request from './request'

const base = '/api/ai/preorganize'
const options = signal => {
  const headers = { 'X-TeamHub-AI': '1' }
  if (window.electron?.aiCapability) headers['X-TeamHub-AI-Capability'] = window.electron.aiCapability
  return { headers, signal, timeout: 30000, _silent: true }
}

export const getPreorganizeConfig = signal => request.get(`${base}/config`, options(signal))
export const listPreorganizeDocuments = (params, signal) => request.get(`${base}/documents`, { ...options(signal), params })
export const getPreorganizeDocument = (id, signal) => request.get(`${base}/documents/${encodeURIComponent(id)}`, options(signal))
export const startPreorganize = (data, signal) => request.post(`${base}/start`, data, options(signal))
export const setPreorganizePolicy = (data, signal) => request.put(`${base}/policy`, data, options(signal))
export const cancelPreorganize = (data, signal) => request.post(`${base}/cancel`, data, options(signal))
export const applyPreorganize = (data, signal) => request.post(`${base}/apply`, data, options(signal))
export const listPreorganizeOperations = signal => request.get(`${base}/operations`, options(signal))
export const undoPreorganize = (id, data, signal) => request.post(`${base}/operations/${encodeURIComponent(id)}/undo`, data, options(signal))
