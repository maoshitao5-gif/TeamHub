import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import axios from 'axios'

let serial = 0
async function fixture(electron = { apiBaseURL: 'http://127.0.0.1:8004', aiCapability: 'desktop-secret' }) {
  globalThis.window = { electron }
  globalThis.__capabilityAxios = axios
  let source = await fs.readFile(new URL('../src/api/request.js', import.meta.url), 'utf8')
  source = source.replace("import axios from 'axios'", 'const axios = globalThis.__capabilityAxios')
    .replace("import { ElMessage } from 'element-plus'", 'const ElMessage = { error() {} }')
    .replace(/import \{ getCloudSessionScope, notifyCloudSessionExpiry \} from ['"]@\/utils\/cloudSession['"]/, 'const getCloudSessionScope = () => 1; const notifyCloudSessionExpiry = () => false')
    .replace(/import\.meta\.env\.VITE_LOCAL_API_URL/g, 'undefined')
  const { default: request } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#cap-${++serial}`)
  return async (config = {}) => {
    let captured
    await request({ url: '/api/documents', ...config, adapter: async input => {
      captured = input
      return { data: {}, status: 200, headers: {}, config: input }
    } })
    return captured.headers.get('X-TeamHub-AI-Capability')
  }
}

test('all desktop local Axios methods carry the capability, including multipart and downloads', async () => {
  const send = await fixture()
  for (const config of [{}, { method: 'post', data: {} }, { method: 'delete' },
    { method: 'post', data: new FormData() }, { responseType: 'blob' },
    { url: 'http://127.0.0.1:8004/api/document' }]) {
    assert.equal(await send(config), 'desktop-secret')
  }
})

test('capability is stripped from foreign origins, protocol-relative URLs and alternate loopback ports', async () => {
  const send = await fixture()
  for (const config of [
    { url: 'https://example.test/api' }, { url: '//example.test/api' },
    { url: 'http://127.0.0.1:9000/api' }, { url: 'http://localhost:8004/api' },
    { baseURL: 'https://example.test' }, { url: 'data:text/plain,test' },
    { url: 'http://user:pass@127.0.0.1:8004/api' },
  ]) {
    assert.equal(await send({ ...config, headers: { 'x-teamhub-ai-capability': 'desktop-secret' } }), undefined)
  }
})

test('browser mode and a nonloopback Electron endpoint cannot send a desktop capability', async () => {
  for (const electron of [undefined, { apiBaseURL: 'https://example.test', aiCapability: 'desktop-secret' }]) {
    const send = await fixture(electron ?? null)
    assert.equal(await send({ headers: { 'X-TeamHub-AI-Capability': 'desktop-secret' } }), undefined)
  }
})
