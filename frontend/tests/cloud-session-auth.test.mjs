import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { createPinia, setActivePinia } from '../node_modules/pinia/dist/pinia.mjs'
import { reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'
import { getCloudSessionScope, notifyCloudSessionExpiry } from '../src/utils/cloudSession.js'

globalThis.CustomEvent ??= class extends Event { constructor(type, options) { super(type); this.detail = options?.detail } }
let serial = 0
async function fixture() {
  globalThis.window = new EventTarget()
  globalThis.localStorage = { getItem: () => null, setItem: () => {} }
  const app = reactive({ settings: { cloud_api_url: 'http://127.0.0.1:9100' }, libraryPath: '/temporary/alice', syncStatus: null, pendingCount: 7 })
  let logoutCalls = 0, statusCalls = 0
  const api = {
    cloudLogin: async () => ({ user: { id: 'alice' }, server_id: 'cloud', teams: [], workspaces: [{ id: 'lib-a', name: '库 A' }] }),
    cloudLogout: async () => { logoutCalls++; return {} },
    selectSharedLibrary: async () => ({}), listTeams: async () => [],
    listCloudWorkspaces: async () => ({ teams: [], workspaces: [{ id: 'lib-a', name: '库 A' }] }),
    getSyncStatus: async () => { statusCalls++; return { logged_in: false } },
  }
  const hook = `__expiry_auth_fixture_${++serial}`; globalThis[hook] = { app, api }
  let source = await fs.readFile(new URL('../src/stores/auth.js', import.meta.url), 'utf8')
  source = source.replace(/import \{ defineStore \} from 'pinia'/, `import { defineStore } from '${new URL('../node_modules/pinia/dist/pinia.mjs', import.meta.url)}'`)
    .replace(/from 'vue'/g, `from '${new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)}'`)
    .replace(/import \{ useAppStore \} from '\.\/app'/, `const useAppStore = () => globalThis.${hook}.app`)
    .replace(/import \{([^}]*?)\} from '@\/api\/sync'/, (_, names) => names.split(',').map(name => name.trim()).filter(Boolean).map(name => `const ${name} = (...args) => globalThis.${hook}.api.${name}(...args)`).join('\n'))
    .replace(/from ['"]@\/utils\/([^'"]+)['"]/g, (_, name) => `from '${new URL(`../src/utils/${name}.js`, import.meta.url)}'`)
  setActivePinia(createPinia())
  const { useAuthStore } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#expiry-${serial}`)
  return { auth: useAuthStore(), app, api, get logoutCalls() { return logoutCalls }, get statusCalls() { return statusCalls } }
}
const expired = { status: 401, data: { detail: { error: { code: 'CLOUD_SESSION_EXPIRED' } } } }

test('terminal expiry clears cloud state without calling logout or changing personal data', async () => {
  const f = await fixture(); await f.auth.login('alice', 'password')
  notifyCloudSessionExpiry(expired, getCloudSessionScope(), window)
  assert.equal(f.auth.isLoggedIn, false); assert.equal(f.auth.currentWorkspace, null)
  assert.deepEqual(f.auth.allWorkspaces, []); assert.equal(f.auth.user, null)
  assert.equal(f.auth.sessionError, '云服务登录已失效，请重新登录')
  assert.equal(f.logoutCalls, 0); assert.equal(f.app.libraryPath, '/temporary/alice'); assert.equal(f.app.pendingCount, 7)
  assert.equal(await f.auth.restoreSession(), false); assert.equal(f.statusCalls, 0)
})
test('failed re-login keeps expiry message; successful re-login restores cloud use', async () => {
  const f = await fixture(); await f.auth.login('alice', 'password')
  notifyCloudSessionExpiry(expired, getCloudSessionScope(), window)
  const login = f.api.cloudLogin; f.api.cloudLogin = async () => { throw Error('密码错误') }
  await assert.rejects(f.auth.login('alice', 'wrong'))
  assert.equal(f.auth.sessionError, '云服务登录已失效，请重新登录')
  f.api.cloudLogin = login; await f.auth.login('alice', 'password')
  assert.equal(f.auth.sessionError, ''); assert.equal(f.auth.currentWorkspace.id, 'lib-a')
})
test('old terminal responses cannot log out a newer login, new server or personal library', async () => {
  const f = await fixture(); await f.auth.login('alice', 'password')
  const oldLogin = getCloudSessionScope(); await f.auth.login('alice', 'password')
  assert.equal(notifyCloudSessionExpiry(expired, oldLogin, window), false); assert.equal(f.auth.isLoggedIn, true)
  const oldServer = getCloudSessionScope(); f.app.settings.cloud_api_url = 'http://127.0.0.1:9200'
  await f.auth.login('alice', 'password')
  assert.equal(notifyCloudSessionExpiry(expired, oldServer, window), false); assert.equal(f.auth.isLoggedIn, true)
  const oldLibrary = getCloudSessionScope(); f.app.libraryPath = '/temporary/bob'
  await f.auth.login('alice', 'password')
  assert.equal(notifyCloudSessionExpiry(expired, oldLibrary, window), false); assert.equal(f.auth.isLoggedIn, true)
})
test('unscoped legacy expiry events are ignored', async () => {
  const f = await fixture(); await f.auth.login('alice', 'password')
  window.dispatchEvent(new Event('cloud:session-expired'))
  assert.equal(f.auth.isLoggedIn, true)
})
