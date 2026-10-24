import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { createPinia, setActivePinia } from '../node_modules/pinia/dist/pinia.mjs'
import { reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'
const deferred = () => { let resolve, reject; const promise = new Promise((ok, no) => { resolve = ok; reject = no }); return { promise, resolve, reject } }
let serial = 0
async function fixture({ libraries = [{ id: 'a', name: '资料A', team_id: 'team', team_name: '组织' }], user = { id: 'alice' }, preference } = {}) {
  const app = reactive({ settings: { cloud_api_url: 'http://127.0.0.1:9100' }, libraryPath: '/temp/alice', syncStatus: null })
  const values = new Map(), calls = []
  globalThis.window = new EventTarget()
  globalThis.localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, val) => values.set(key, val) }
  globalThis.CustomEvent ??= class extends Event { constructor(type, options) { super(type); this.detail = options?.detail } }
  if (preference) values.set('teamhub:shared-library:server-a:alice', preference)
  const api = {
    cloudLogin: async () => ({ user, server_id: 'server-a', teams: [{ id: 'team', name: '组织' }], workspaces: libraries }), cloudLogout: async () => ({}),
    getSyncStatus: async () => ({ logged_in: true, user, server_id: 'server-a', workspace_id: null }),
    listTeams: async () => [{ id: 'team', name: '组织' }], listTeamWorkspaces: async () => libraries,
    listCloudWorkspaces: async () => ({ workspaces: libraries, teams: [{ id: 'team', name: '组织' }] }),
    bindWorkspace: async (id, name) => { calls.push(id); return { workspace_id: id, workspace_name: name } },
    selectSharedLibrary: async (id, name) => { calls.push(id); return { workspace_id: id, workspace_name: name } },
  }
  const hook = `__auth_fixture_${++serial}`; globalThis[hook] = { app, api }
  let source = await fs.readFile(new URL('../src/stores/auth.js', import.meta.url), 'utf8')
  source = source.replace(/import \{ defineStore \} from 'pinia'/, `import { defineStore } from '${new URL('../node_modules/pinia/dist/pinia.mjs', import.meta.url)}'`)
    .replace(/from 'vue'/g, `from '${new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)}'`)
    .replace(/import \{ useAppStore \} from '\.\/app'/, `const useAppStore = () => globalThis.${hook}.app`)
    .replace(/import \{([^}]*?)\} from '@\/api\/sync'/, (_, names) => names.split(',').map(name => name.trim()).filter(Boolean).map(name => `const ${name} = (...args) => globalThis.${hook}.api.${name}(...args)`).join('\n'))
    .replace(/from ['"]@\/utils\/([^'"]+)['"]/g, (_, name) => `from '${new URL(`../src/utils/${name}.js`, import.meta.url)}'`)
  setActivePinia(createPinia())
  const { useAuthStore } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${serial}`)
  return { auth: useAuthStore(), app, api, calls, values }
}
test('first multi library login waits for an explicit click', async () => { const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }] }); await f.auth.login('alice', 'pw'); assert.equal(f.auth.currentWorkspace, null); assert.equal(f.auth.allWorkspaces.length, 2); assert.ok(!f.calls.includes('a')) })
test('single library login selects it automatically', async () => { const f = await fixture(); await f.auth.login('alice', 'pw'); assert.equal(f.auth.currentWorkspace.id, 'a'); assert.equal(f.app.syncStatus.workspace_id, 'a') })
test('scoped preference survives logout, but another account does not inherit it', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }], preference: 'b' })
  await f.auth.login('alice', 'pw'); assert.equal(f.auth.currentWorkspace.id, 'b')
  await f.auth.logout(); await f.auth.login('alice', 'pw'); assert.equal(f.auth.currentWorkspace.id, 'b')
  await f.auth.logout()
  f.api.cloudLogin = async () => ({ user: { id: 'bob' }, server_id: 'server-a', teams: [], workspaces: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }] })
  await f.auth.login('bob', 'pw'); assert.equal(f.auth.currentWorkspace, null)
})
test('save failure keeps previous current library and reports an error', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }], preference: 'a' })
  await f.auth.login('alice', 'pw')
  f.api.selectSharedLibrary = async () => { throw Error('保存失败') }
  assert.equal(await f.auth.switchWorkspace({ id: 'b' }), false)
  assert.equal(f.auth.currentWorkspace.id, 'a'); assert.equal(f.app.syncStatus.workspace_id, 'a'); assert.match(f.auth.workspaceError, /保存失败/)
})
test('library and server changes invalidate old current selection immediately', async () => {
  const f = await fixture(); await f.auth.login('alice', 'pw'); f.app.libraryPath = '/temp/bob'
  assert.equal(f.auth.currentWorkspace, null); assert.equal(f.auth.isLoggedIn, false); assert.equal(f.app.syncStatus, null)
  await f.auth.login('alice', 'pw'); f.app.settings.cloud_api_url = 'http://127.0.0.1:9200'; assert.equal(f.auth.currentWorkspace, null)
})
test('rapid selection requests are serialized and late first choice never changes UI', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }, { id: 'c', name: 'C' }], preference: 'a' })
  await f.auth.login('alice', 'pw')
  const first = deferred(), second = deferred(), requested = []
  f.api.selectSharedLibrary = async id => { requested.push(id); return id === 'b' ? first.promise : second.promise }
  const b = f.auth.switchWorkspace({ id: 'b' }); await Promise.resolve(); await Promise.resolve()
  const c = f.auth.switchWorkspace({ id: 'c' })
  assert.deepEqual(requested, ['b']); assert.equal(f.auth.currentWorkspace.id, 'a'); assert.equal(f.auth.workspaceLoading, true)
  first.resolve({}); assert.equal(await b, false)
  await Promise.resolve(); await Promise.resolve(); assert.deepEqual(requested, ['b', 'c']); assert.equal(f.auth.currentWorkspace.id, 'a')
  second.resolve({}); assert.equal(await c, true); assert.equal(f.auth.currentWorkspace.id, 'c'); assert.equal(f.auth.workspaceLoading, false)
})
test('old login response cannot revive a session after logout', async () => {
  const f = await fixture(), pending = deferred()
  f.api.cloudLogin = () => pending.promise
  const login = f.auth.login('alice', 'pw'); await f.auth.logout()
  pending.resolve({ user: { id: 'alice' }, server_id: 'server-a', teams: [], workspaces: [{ id: 'a', name: 'A' }] })
  assert.equal(await login, false); assert.equal(f.auth.isLoggedIn, false); assert.equal(f.auth.currentWorkspace, null)
})
test('revoked choice asks again with many libraries, and enters a sole remaining library', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }, { id: 'c', name: 'C' }], preference: 'a' })
  await f.auth.login('alice', 'pw')
  f.api.listCloudWorkspaces = async () => ({ teams: [], workspaces: [{ id: 'b', name: 'B' }, { id: 'c', name: 'C' }] })
  await f.auth.refreshWorkspaces(); assert.equal(f.auth.currentWorkspace, null)
  f.api.listCloudWorkspaces = async () => ({ teams: [], workspaces: [{ id: 'c', name: 'C' }] })
  await f.auth.refreshWorkspaces(); assert.equal(f.auth.currentWorkspace.id, 'c')
})
test('management refresh discovers libraries without selecting one', async () => {
  const f = await fixture({ libraries: [] }); await f.auth.login('alice', 'pw')
  f.api.listCloudWorkspaces = async () => ({ teams: [], workspaces: [{ id: 'new', name: 'New' }] })
  await f.auth.refreshWorkspaces({ autoSelect: false }); assert.equal(f.auth.allWorkspaces.length, 1); assert.equal(f.auth.currentWorkspace, null)
})
test('failed selection during login keeps authenticated account and allows retry', async () => {
  const f = await fixture(); f.api.selectSharedLibrary = async () => { throw Error('暂时无法保存') }
  assert.equal(await f.auth.login('alice', 'pw'), true); assert.equal(f.auth.isLoggedIn, true); assert.match(f.auth.workspaceError, /无法保存/)
  f.api.selectSharedLibrary = async () => ({})
  assert.equal(await f.auth.switchWorkspace({ id: 'a' }), true); assert.equal(f.auth.currentWorkspace.id, 'a')
})
test('restored legacy selection is checked against actual accessible libraries', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }] })
  f.api.getSyncStatus = async () => ({ user: { id: 'alice' }, logged_in: true, server_id: 'server-a', workspace_id: 'b' })
  await f.auth.restoreSession(); assert.equal(f.auth.currentWorkspace.id, 'b'); assert.equal(f.app.syncStatus.workspace_id, 'b')
})

test('sync status cache cannot overwrite a newer selected library', async () => {
  const pending = deferred(), appApi = { getSyncStatus: () => pending.promise }
  globalThis.__app_status_api = appApi
  let source = await fs.readFile(new URL('../src/stores/app.js', import.meta.url), 'utf8')
  source = source.replace(/import \{ defineStore \} from 'pinia'/, `import { defineStore } from '${new URL('../node_modules/pinia/dist/pinia.mjs', import.meta.url)}'`)
    .replace(/from 'vue'/g, `from '${new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)}'`)
    .replace(/import request from '@\/api\/request'/, 'const request = {}')
    .replace(/import \{ getSyncStatus \} from '@\/api\/sync'/, 'const getSyncStatus = () => globalThis.__app_status_api.getSyncStatus()')
  setActivePinia(createPinia())
  const { useAppStore } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#cache-${++serial}`)
  const app = useAppStore(); app.libraryPath = '/temp/alice'; app.syncStatus = { workspace_id: 'a' }
  const request = app.fetchSyncStatus(); app.syncStatus = { workspace_id: 'b' }
  pending.resolve({ workspace_id: 'a' }); await request
  assert.equal(app.syncStatus.workspace_id, 'b')
})

test('successful superseded selection then latest failure displays the actual persisted library', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }, { id: 'c', name: 'C' }], preference: 'a' })
  await f.auth.login('alice', 'pw')
  const delayed = deferred(); let persisted = 'a'
  f.api.selectSharedLibrary = async id => {
    if (id === 'b') { await delayed.promise; persisted = 'b'; return {} }
    throw Error('C 无权访问')
  }
  const b = f.auth.switchWorkspace({ id: 'b' }); await Promise.resolve(); await Promise.resolve()
  const c = f.auth.switchWorkspace({ id: 'c' })
  delayed.resolve({}); assert.equal(await b, false); assert.equal(await c, false)
  assert.equal(f.auth.currentWorkspace.id, persisted); assert.equal(f.app.syncStatus.workspace_id, persisted)
  assert.equal(f.values.get('teamhub:shared-library:server-a:alice'), persisted)
  assert.match(f.auth.workspaceError, /C 无权访问/); assert.equal(f.auth.workspaceLoading, false)
})

test('failed logout preserves authenticated state and reports failure', async () => {
  const f = await fixture(); await f.auth.login('alice', 'pw')
  const pending = deferred(); f.api.cloudLogout = () => pending.promise
  const logout = f.auth.logout()
  assert.equal(f.auth.isLoggedIn, true); assert.equal(f.auth.currentWorkspace.id, 'a')
  pending.reject(Error('退出保存失败'))
  assert.equal(await logout, false); assert.equal(f.auth.isLoggedIn, true); assert.equal(f.auth.currentWorkspace.id, 'a')
  assert.match(f.auth.workspaceError, /退出保存失败/); assert.equal(f.auth.workspaceLoading, false)
})

test('confirmed logout clears current session and returns true', async () => {
  const f = await fixture(); await f.auth.login('alice', 'pw')
  assert.equal(await f.auth.logout(), true); assert.equal(f.auth.isLoggedIn, false); assert.equal(f.auth.currentWorkspace, null)
})

test('late logout success cannot clear a subsequently authenticated account', async () => {
  const f = await fixture(); await f.auth.login('alice', 'pw')
  const pending = deferred(), started = deferred(); f.api.cloudLogout = () => { started.resolve(); return pending.promise }
  const logout = f.auth.logout(); await started.promise
  f.api.cloudLogin = async () => ({ user: { id: 'bob' }, server_id: 'server-a', teams: [], workspaces: [{ id: 'bob-library', name: 'Bob' }] })
  await f.auth.login('bob', 'pw')
  pending.resolve({}); assert.equal(await logout, false)
  assert.equal(f.auth.isLoggedIn, true); assert.equal(f.auth.user.id, 'bob'); assert.equal(f.auth.currentWorkspace.id, 'bob-library')
})

test('late logout failure cannot put an error onto a subsequent account', async () => {
  const f = await fixture(); await f.auth.login('alice', 'pw')
  const pending = deferred(), started = deferred(); f.api.cloudLogout = () => { started.resolve(); return pending.promise }
  const logout = f.auth.logout(); await started.promise
  f.api.cloudLogin = async () => ({ user: { id: 'bob' }, server_id: 'server-a', teams: [], workspaces: [{ id: 'bob-library', name: 'Bob' }] })
  await f.auth.login('bob', 'pw'); pending.reject(Error('旧账号退出失败'))
  assert.equal(await logout, false); assert.equal(f.auth.user.id, 'bob'); assert.equal(f.auth.workspaceError, '')
})

test('logout waits for an in-flight persisted choice and failed credential removal retains actual library', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }, { id: 'c', name: 'C' }], preference: 'a' })
  await f.auth.login('alice', 'pw')
  const ack = deferred(); let persisted = 'a', logoutCalls = 0
  f.api.selectSharedLibrary = async id => { await ack.promise; persisted = id; return {} }
  const b = f.auth.switchWorkspace({ id: 'b' }); await Promise.resolve(); await Promise.resolve()
  f.api.cloudLogout = async () => { logoutCalls++; throw Error('退出保存失败') }
  const logout = f.auth.logout()
  await Promise.resolve(); assert.equal(logoutCalls, 0); assert.equal(f.auth.workspaceLoading, true)
  assert.equal(await f.auth.switchWorkspace({ id: 'c' }), false)
  ack.resolve({}); assert.equal(await b, false); assert.equal(await logout, false)
  assert.equal(logoutCalls, 1); assert.equal(f.auth.currentWorkspace.id, persisted); assert.equal(persisted, 'b')
  assert.equal(f.app.syncStatus.workspace_id, persisted); assert.equal(f.auth.isLoggedIn, true); assert.equal(f.auth.workspaceLoading, false)
})

test('successful logout confirms removal only after in-flight choice has settled', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }], preference: 'a' })
  await f.auth.login('alice', 'pw')
  const ack = deferred(), calls = []
  f.api.selectSharedLibrary = async () => { await ack.promise; calls.push('choice-saved'); return {} }
  const choice = f.auth.switchWorkspace({ id: 'b' }); await Promise.resolve(); await Promise.resolve()
  f.api.cloudLogout = async () => { calls.push('credentials-removed'); return {} }
  const logout = f.auth.logout(); await Promise.resolve()
  assert.deepEqual(calls, []); assert.equal(f.auth.isLoggedIn, true)
  ack.resolve({}); await choice; assert.equal(await logout, true)
  assert.deepEqual(calls, ['choice-saved', 'credentials-removed']); assert.equal(f.auth.isLoggedIn, false)
})

test('new login supersedes a queued logout before any credential removal request', async () => {
  const f = await fixture({ libraries: [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }], preference: 'a' })
  await f.auth.login('alice', 'pw')
  const ack = deferred(); let logoutCalls = 0
  f.api.selectSharedLibrary = async id => { if (id === 'b') await ack.promise; return {} }
  const selection = f.auth.switchWorkspace({ id: 'b' }); await Promise.resolve(); await Promise.resolve()
  f.api.cloudLogout = async () => { logoutCalls++; return {} }
  const logout = f.auth.logout()
  f.api.cloudLogin = async () => ({ user: { id: 'bob' }, server_id: 'server-a', teams: [], workspaces: [{ id: 'bob-library', name: 'Bob' }] })
  const login = f.auth.login('bob', 'pw')
  ack.resolve({}); await selection; await login; assert.equal(await logout, false)
  assert.equal(logoutCalls, 0); assert.equal(f.auth.user.id, 'bob'); assert.equal(f.auth.currentWorkspace.id, 'bob-library')
})
