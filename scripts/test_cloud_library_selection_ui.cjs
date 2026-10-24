#!/usr/bin/env node
// Production Vue components + isolated API fixtures. No user service or library is accessed.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn } = require('node:child_process')
const repo = path.resolve(__dirname, '..')
const evidence = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-library-selection-ui-'))
const harnessName = `.library-selection-${crypto.randomUUID()}.html`
const harnessPath = path.join(repo, 'frontend', harnessName)
const modulePath = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(modulePath) ? modulePath : 'playwright'))
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))
const userA = { id: 'account-a', email: 'a@example.invalid', display_name: '甲成员' }
const userB = { id: 'account-b', email: 'b@example.invalid', display_name: '乙成员' }
const teams = [{ id: 'team-a', name: '运维团队', slug: 'team-a', storage_used: 0, storage_quota: 1000000 }, { id: 'team-b', name: '检修团队', slug: 'team-b', storage_used: 0, storage_quota: 1000000 }]
const wsA = { id: 'ws-a', name: '技术资料', team_id: 'team-a', team_name: teams[0].name, role: 'member' }
const wsB = { id: 'ws-b', name: '检修资料', team_id: 'team-b', team_name: teams[1].name, role: 'member' }
const wsC = { id: 'ws-c', name: '专项资料', team_id: 'team-b', team_name: teams[1].name, role: 'member' }
const localDoc = { id: 'local-doc', name: '本地设备说明.txt', status: 'organized', storage_path: '本地设备说明.txt', storage_mode: 'move', tags: [], file_count: 1, total_size: 20, updated_at: '2026-10-14T00:00:00Z', current_version: { id: 'local-v1', version_number: 1, content_hash: 'a'.repeat(64) } }
let browser, service, currentPage, origin
const results = [], errors = []
const startedAt = Date.now()
function pass(name) { results.push({ name, status: 'passed' }); console.log('PASS ' + name) }
function saveResults(status, error) {
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  const seconds = (Date.now() - startedAt) / 1000
  fs.writeFileSync(path.join(evidence, 'results.json'), JSON.stringify({ status, results, errors, seconds, ...(error ? { error: error.stack } : {}) }, null, 2))
  fs.writeFileSync(path.join(evidence, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?><testsuite name="Shared library selection" tests="${results.length + (error ? 1 : 0)}" failures="${error ? 1 : 0}" time="${seconds}">${results.map(result => `<testcase name="${escape(result.name)}"/>`).join('')}${error ? `<testcase name="未完成资料库交互验收"><failure message="${escape(error.message)}">${escape(error.stack)}</failure></testcase>` : ''}</testsuite>`)
  fs.writeFileSync(path.join(evidence, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>共享资料库交互验收</title><h1>共享资料库交互验收：${escape(status)}</h1><p>${seconds.toFixed(1)} 秒，${results.length} 组通过，${errors.length} 个浏览器异常</p><ul>${results.map(result => `<li>${escape(result.name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p><a href="web-first-choice.png">网页首次选择</a> · <a href="desktop-first-choice.png">桌面首次选择</a> · <a href="desktop-cloud-settings.png">云服务设置</a> · <a href="desktop-publish-target.png">发布目的地</a> · <a href="desktop-update-original-library.png">更新原位置</a></p></html>`)
}
const uiSettled = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
async function capture(page, filename, fullPage = false) {
  await page.evaluate(async () => { await Promise.all(document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().iterations)).map(animation => animation.finished.catch(() => {}))) })
  await uiSettled(page); await page.screenshot({ path: path.join(evidence, filename), fullPage })
}
async function releaseSearch(fixture, id) {
  const received = fixture.page.waitForResponse(response => {
    const url = new URL(response.url()); return url.pathname.endsWith('/documents/search') && url.pathname.includes(`/${id}/`) || url.pathname === '/api/sync/docs/search' && url.searchParams.get('workspace_id') === id
  })
  fixture.state.heldSearch = null; fixture.state.releaseSearch(); await received; await uiSettled(fixture.page)
}
async function unusedPort() { return new Promise((resolve, reject) => { const server = net.createServer().once('error', reject); server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)) }) }) }

fs.writeFileSync(harnessPath, `<!doctype html><html><body><div id="app"></div><script type="module">
import {createApp} from 'vue'; import ElementPlus from 'element-plus'; import 'element-plus/dist/index.css';
if(new URLSearchParams(location.search).get('mode')==='desktop') {
 const [{createPinia},{default:App},{default:router},{useAuthStore},{useAppStore}]=await Promise.all([import('pinia'),import('/src/App.vue'),import('/src/router/index.js'),import('/src/stores/auth.js'),import('/src/stores/app.js')]);
 const pinia=createPinia(),auth=useAuthStore(pinia),app=useAppStore(pinia);
 window.libraryTest={navigate:p=>router.push(p),refresh:()=>auth.refreshWorkspaces(),restore:()=>auth.restoreSession(),login:user=>auth.login(user.email,'synthetic'),logout:()=>auth.logout(),switchLibrary:id=>auth.switchWorkspace(auth.allWorkspaces.find(w=>w.id===id)),changeServer:url=>{app.settings.cloud_api_url=url},session:()=>({user:auth.user,serverId:auth.serverId,current:auth.currentWorkspace,available:auth.allWorkspaces,loading:auth.workspaceLoading,error:auth.workspaceError,team:auth.currentTeam})};
 createApp(App).use(pinia).use(router).use(ElementPlus).mount('#app');
} else {
 const [{default:CloudApp},{webSession}]=await Promise.all([import('/src/cloud/App.vue'),import('/src/cloud/api.js')]); await import('/src/cloud/style.css');
 window.libraryTest={changeAccount:user=>{webSession.user=user},changeServer:id=>{webSession.serverId=id},session:()=>({user:webSession.user,serverId:webSession.serverId})};
 createApp(CloudApp).use(ElementPlus).mount('#app');
}
</script></body></html>`)

async function webFixture({ available = [wsA, wsB], user = userA, saved = null } = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
  const page = await context.newPage(); currentPage = page; page.setDefaultTimeout(7000)
  const calls = [], state = { available, user, denied: null, showDocs: false, heldSearch: null, releaseSearch: null, searchStarted: null }
  page.on('pageerror', error => errors.push(error.message))
  if (saved) await page.addInitScript(saved)
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), endpoint = url.pathname
    if (!endpoint.startsWith('/api/')) { assert.equal(url.origin, origin, 'Preview/selection must not request external services'); return route.continue() }
    const call = { endpoint, method: request.method(), body: null }; try { call.body = request.postDataJSON() } catch {} calls.push(call)
    let value, status = 200
    if (endpoint === '/api/server-info') value = { protocol_major: 1, server_id: 'server-test' }
    else if (endpoint === '/api/web-auth/refresh' || endpoint === '/api/web-auth/login') value = { access_token: 'synthetic-token', user: state.user }
    else if (endpoint === '/api/web-auth/logout') value = null
    else if (endpoint === '/api/teams') value = teams
    else if (/^\/api\/teams\/[^/]+\/workspaces$/.test(endpoint)) value = state.available.filter(space => space.team_id === endpoint.split('/')[3])
    else if (endpoint.endsWith('/collaboration-context')) {
      const space = state.available.find(space => space.id === endpoint.split('/')[3])
      if (!space || state.denied === space.id) { status = 403; value = { error: { message: '你已无法访问此资料库', code: 'FORBIDDEN' } } }
      else value = { user_id: state.user.id, workspace_id: space.id, workspace_name: space.name, team_id: space.team_id, team_name: space.team_name, can_tag: true }
    }
    else if (/\/documents\/search$/.test(endpoint)) {
      const id = endpoint.split('/')[3]
      if (id === state.heldSearch) { state.searchStarted?.(); await new Promise(resolve => { state.releaseSearch = resolve }) }
      value = { items: state.showDocs ? [{ id: `doc-${id}`, name: `${id === wsB.id ? '检修' : '运维'}网页资料.txt`, doc_type: 'file', total_size: 20, tags: [], current_version_id: `v-${id}`, capabilities: {} }] : [], total: state.showDocs ? 1 : 0 }
    }
    else if (/\/deliveries(?:\/unread-count)?$/.test(endpoint)) value = endpoint.endsWith('/unread-count') ? { count: 0 } : { items: [], total: 0 }
    else if (/\/(operations|tags)$/.test(endpoint)) value = []
    else throw new Error('Unmocked API; refusing access: ' + endpoint)
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
  })
  await page.goto(`${origin}/${harnessName}`)
  await page.waitForFunction(() => window.libraryTest?.session().user)
  return { page, state, calls, context, close: async name => { await context.tracing.stop({ path: path.join(evidence, `${name}-trace.zip`) }); await context.close() } }
}

async function desktopFixture({ available = [wsA, wsB], user = userA, selected = '', linked = false, route = '/shared' } = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1024 } }); await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
  await context.addInitScript(() => { window.electron = { apiBaseURL: location.origin } })
  const page = await context.newPage(); currentPage = page; page.setDefaultTimeout(8000); page.on('pageerror', error => errors.push(error.message))
  const calls = [], state = { available, user, selected, linked, failSwitch: '', failLogout: false, failedWorkspaces: false, heldLogout: false, releaseLogout: null, logoutStarted: null, heldSwitch: null, releaseSwitch: null, switchStarted: null, heldSearch: null, releaseSearch: null, searchStarted: null, heldMembers: null, releaseMembers: null, membersStarted: null, removedMembers: '', failedMembers: '', denied: '', denyOriginal: false, publicationMode: 'success', teams: available.length ? teams : [] }
  const statusPayload = () => ({ logged_in: !!state.user, user: state.user, workspace_id: state.selected, workspace_name: state.available.find(ws => ws.id === state.selected)?.name || '', server_id: 'server-test' })
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), endpoint = url.pathname
    if (!endpoint.startsWith('/api/')) { assert.equal(url.origin, origin, 'Desktop fixture must stay on its isolated origin'); return route.continue() }
    const call = { endpoint, method: request.method(), body: null, query: Object.fromEntries(url.searchParams) }; try { call.body = request.postDataJSON() } catch {} calls.push(call)
    let value, status = 200
    if (endpoint === '/api/settings/config') value = { library_path: '/synthetic/library', cloud_api_url: 'http://synthetic.invalid', library_show_tree_view: false, library_show_flat_view: true, enable_floating_window: false, max_file_size: 1073741824, items_per_page: 15 }
    else if (endpoint === '/api/ai/settings' && request.method() === 'GET') value = { provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', has_api_key: false, key_storage: 'memory', can_persist_key: false }
    else if (endpoint === '/api/settings/library') value = { path: '/synthetic/library', initialized: true, pending_count: 0, total_documents: 1, total_size: 20 }
    else if (endpoint === '/api/settings/pending-path') value = { path: '/synthetic/pending', configured_path: '/synthetic/pending', migration_recovery: null }
    else if (endpoint === '/api/documents/pending/count') value = { count: 0 }
    else if (endpoint === '/api/sync/status') value = statusPayload()
    else if (endpoint === '/api/sync/login') { state.user = call.body.email === userB.email ? userB : userA; state.selected = ''; value = { user: state.user, teams, workspaces: state.available, server_id: 'server-test' } }
    else if (endpoint === '/api/sync/logout') { if (state.failLogout) { status = 503; value = { detail: '退出暂时未完成' } } else { state.user = null; state.selected = ''; value = {} } if (state.heldLogout) { state.logoutStarted?.(); await new Promise(resolve => { state.releaseLogout = resolve }) } }
    else if (endpoint === '/api/sync/teams') value = state.teams
    else if (endpoint === '/api/sync/workspaces') { if (state.failedWorkspaces) { status = 503; value = { detail: '资料库列表暂不可读取' } } else value = { workspaces: state.available, teams: state.teams } }
    else if (/^\/api\/sync\/teams\/[^/]+\/workspaces$/.test(endpoint)) value = state.available.filter(ws => ws.team_id === endpoint.split('/')[4])
    else if (endpoint.endsWith('/members') && !endpoint.includes('/deliveries')) value = [{ id: state.user?.id, user_id: state.user?.id, display_name: state.user?.display_name, email: state.user?.email, role: 'member' }]
    else if (endpoint === '/api/sync/workspace') {
      if (call.body.workspace_id === state.heldSwitch) { state.switchStarted?.(); await new Promise(resolve => { state.releaseSwitch = resolve }) }
      if (state.failSwitch === call.body.workspace_id && state.failSwitch) { status = 403; value = { detail: '你已无法访问此资料库' } }
      else { state.selected = call.body.workspace_id; value = {} }
    }
    else if (endpoint === '/api/sharing/context') {
      const space = state.available.find(ws => ws.id === (url.searchParams.get('workspace_id') || state.selected))
      if (!space || space.id === state.denied) { status = 403; value = { detail: '你已无法访问此资料库' } }
      else value = { workspace_id: space.id, workspace_name: space.name, server_id: 'server-test', server_url: 'http://synthetic.invalid', user_id: state.user.id, can_tag: true, can_manage: false }
    }
    else if (endpoint === '/api/documents/search') value = { documents: [localDoc], total: 1 }
    else if (endpoint === '/api/documents/local-doc') value = localDoc
    else if (endpoint === '/api/sharing/links/local-doc/target') { if (state.denyOriginal) { status = 403; value = { detail: '原资料无权限' } } else value = { workspace_id: wsA.id, workspace_name: wsA.name, team_id: wsA.team_id, team_name: wsA.team_name, shared_document_id: 'cloud-original', shared_document_name: '云端原设备说明.txt', can_contribute: true } }
    else if (endpoint === '/api/sharing/links/local-doc') value = { linked: state.linked }
    else if (endpoint.endsWith('/delivery-members') || endpoint === '/api/deliveries/members') {
      const id = url.searchParams.get('workspace_id') || endpoint.split('/')[3]
      if (id === state.heldMembers) { state.membersStarted?.(); await new Promise(resolve => { state.releaseMembers = resolve }) }
      if (state.failedMembers === id) { status = 503; value = { detail: '成员列表暂不可读取' } }
      else value = state.removedMembers === id ? [] : [{ id: id === wsB.id ? 'member-b' : 'member-a', display_name: id === wsB.id ? '检修库成员' : '运维库成员' }]
    }
    else if (endpoint === '/api/sync/docs/search') {
      const id = url.searchParams.get('workspace_id') || state.selected
      if (id === state.heldSearch) { state.searchStarted?.(); await new Promise(resolve => { state.releaseSearch = resolve }) }
      value = { items: [{ id: `cloud-${id}`, name: `${id === wsB.id ? '检修' : '运维'}库资料.txt`, tags: [], is_folder: false, total_size: 20, current_version_id: `v-${id}` }], total: 1 }
    }
    else if (endpoint === '/api/sharing/publish' || endpoint === '/api/sharing/submit-version') {
      if (state.publicationMode === 'uncertain') { status = 503; value = { error: { code: 'SERVICE_UNAVAILABLE', message: '连接暂时中断，请重试本次请求' } } }
      else if (state.publicationMode === 'duplicate') { status = 409; value = { error: { code: 'DUPLICATE_CONTENT', message: '云端已存在相同文件，未重复新增资料', details: { workspace_id: call.body.workspace_id, duplicate_document: { document_id: 'cloud-existing', name: '已存在说明.txt' } } } } }
      else value = { status: 'succeeded', code: endpoint.endsWith('/submit-version') ? 'CONTENT_UNCHANGED' : undefined, document_id: 'cloud-new' }
    }
    else if (endpoint === '/api/sharing/pull') value = { status: 'duplicate', duplicate: true, code: 'DUPLICATE_CONTENT', message: '本地已存在相同文件，未重复收录', document_id: localDoc.id, name: localDoc.name, document_status: 'organized', duplicate_document: localDoc }
    else if (endpoint === '/api/sync/cloud-docs') value = { documents: state.publicationMode === 'duplicate' ? [{ id: 'cloud-existing', name: '已存在说明.txt', tags: [], is_folder: false, total_size: 20, current_version_id: 'cloud-v1' }] : [] }
    else if (/\/versions$/.test(endpoint)) value = [{ id: 'cloud-v1', version_number: 1, is_current: true, original_filename: '说明.txt', file_size: 20 }]
    else if (endpoint.endsWith('/preview')) value = { kind: 'text', filename: '说明.txt', version_id: 'cloud-v1', version_number: 1, text: '已授权预览内容', truncated: false }
    else if (endpoint === '/api/deliveries' || endpoint.endsWith('/deliveries')) value = { items: [], total: 0 }
    else if (endpoint.endsWith('/unread-count')) value = { count: 0 }
    else if (endpoint === '/api/settings/storage/migration') value = { pending: false }
    else if (endpoint.startsWith('/api/tags') || endpoint.includes('/favorites') || endpoint.includes('/directories') || endpoint.includes('/missing') || endpoint.includes('/tree') || endpoint.includes('/operations') || endpoint.startsWith('/api/sharing/tags/') || endpoint === '/api/sharing/suggestions') value = []
    else throw new Error('Unmocked desktop API; refusing access: ' + endpoint)
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value), headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' } })
  })
  await page.goto(`${origin}/${harnessName}?mode=desktop#${route}`)
  await page.waitForFunction(() => window.libraryTest)
  await page.getByRole('button', { name: '打开文件库', exact: true }).click()
  await page.waitForFunction(() => window.libraryTest.session().user)
  return { page, state, calls, context, close: async name => { state.releaseSwitch?.(); state.releaseSearch?.(); state.releaseMembers?.(); state.releaseLogout?.(); await context.tracing.stop({ path: path.join(evidence, `${name}-trace.zip`) }); await context.close() } }
}

async function main() {
  const port = await unusedPort(); origin = `http://127.0.0.1:${port}`
  const log = fs.openSync(path.join(evidence, 'vite.log'), 'w')
  service = spawn(process.execPath, [path.join(repo, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: path.join(repo, 'frontend'), stdio: ['ignore', log, log] }); fs.closeSync(log)
  for (let attempt = 0; attempt < 200; attempt++) { try { if ((await fetch(origin)).ok) break } catch {} if (attempt === 199) throw new Error('Vite did not start'); await wait(50) }
  browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' })
  if (process.env.TEAMHUB_LIBRARY_TEST_SCOPE !== 'desktop') await webTests()
  if (process.env.TEAMHUB_LIBRARY_TEST_SCOPE !== 'web') await desktopTests()
  assert.deepEqual(errors, [])
}

async function webTests() {
  const first = await webFixture()
  await first.page.getByTestId('shared-library-entry-ws-a').waitFor(); await first.page.getByTestId('shared-library-entry-ws-b').waitFor()
  assert.equal(first.calls.filter(call => call.endpoint.endsWith('/documents/search')).length, 0, 'First multi-library login must not browse an arbitrary library')
  await capture(first.page, 'web-first-choice.png'); pass('网页首次多资料库：直接选入口，未随机进入第一个库')
  await first.page.getByTestId('shared-library-entry-ws-b').click(); await first.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  assert(first.calls.some(call => call.endpoint === '/api/workspaces/ws-b/collaboration-context'), 'Entry verifies selected library access')
  const saved = await first.page.evaluate(() => ({ ...localStorage }))
  assert(Object.values(saved).some(value => value === wsB.id), 'Store only selected library ID')
  assert(!Object.values(saved).some(value => value.includes('synthetic-token') || value.includes(wsB.name) || value.includes(userA.email)), 'Preference contains no token, account data or document details')
  await first.page.reload(); await first.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  assert.equal(await first.page.getByTestId('shared-library-entry-ws-a').count(), 0)
  assert((await first.page.getByTestId('cloud-library-picker').innerText()).includes(wsB.name)); pass('网页一次点击进入并记住同服务同账号上次选择')
  first.state.user = userB; const before = first.calls.length; await first.page.evaluate(user => window.libraryTest.changeAccount(user), userB)
  await first.page.getByTestId('shared-library-entry-ws-a').waitFor(); assert.equal(await first.page.getByRole('heading', { name: '组织资料', exact: true }).count(), 0)
  assert.equal(first.calls.slice(before).filter(call => call.endpoint.endsWith('/documents/search')).length, 0); pass('网页换账号不继承此前账号的资料库偏好或内容')
  await first.page.getByTestId('shared-library-entry-ws-a').click(); await first.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  await first.page.evaluate(() => window.libraryTest.changeServer('server-other')); await first.page.getByTestId('shared-library-entry-ws-a').waitFor(); pass('网页换服务身份不继承其它服务的资料库偏好')
  await first.close('web-multi-preference')

  const single = await webFixture({ available: [wsA] }); await single.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  assert.equal(await single.page.getByTestId('shared-library-entry-ws-a').count(), 0); await capture(single.page, 'web-single-auto.png'); await single.close('web-single-auto'); pass('网页单资料库自动进入，无额外选择步骤')
  const absent = await webFixture({ available: [] }); await absent.page.getByText('暂无可访问的资料库', { exact: false }).first().waitFor()
  assert.equal(absent.calls.filter(call => call.endpoint.endsWith('/documents/search')).length, 0); await capture(absent.page, 'web-no-library.png'); await absent.close('web-no-library'); pass('网页无资料库明确联系管理员，不展示旧内容')
  const revoked = await webFixture({ available: [wsA, wsC] })
  // Copy the old account/server's prior B preference, then re-evaluate after reload.
  await revoked.page.evaluate(storage => { for (const [key, value] of Object.entries(storage)) localStorage.setItem(key, value) }, saved); await revoked.page.reload()
  await revoked.page.getByTestId('shared-library-entry-ws-a').waitFor(); await revoked.page.getByTestId('shared-library-entry-ws-c').waitFor()
  assert.equal(revoked.calls.filter(call => call.endpoint.endsWith('/documents/search')).length, 0); await revoked.close('web-preference-revoked'); pass('网页失去上次资料库权限时重新选入口，不猜测替代库')
  const denied = await webFixture(); denied.state.denied = wsB.id; await denied.page.getByTestId('shared-library-entry-ws-b').click(); await denied.page.getByText('你已无法访问此资料库', { exact: false }).waitFor()
  assert.equal(denied.calls.filter(call => call.endpoint.endsWith('/documents/search')).length, 0); await denied.close('web-denied-entry'); pass('网页进入资料库权限校验失败：保留选择入口，不读取资料')
  const late = await webFixture(); late.state.showDocs = true; late.state.heldSearch = wsA.id; const started = new Promise(resolve => { late.state.searchStarted = resolve }); await late.page.getByTestId('shared-library-entry-ws-a').click(); await started
  await late.page.getByTestId('shared-library-selector').click(); await late.page.getByRole('option', { name: '检修团队 / 检修资料', exact: true }).click(); await late.page.getByRole('button', { name: '检修网页资料.txt', exact: true }).waitFor(); await releaseSearch(late, wsA.id)
  await uiSettled(late.page); await late.page.getByRole('button', { name: '检修网页资料.txt', exact: true }).waitFor(); assert.equal(await late.page.getByRole('button', { name: '运维网页资料.txt', exact: true }).count(), 0); await late.close('web-late-library'); pass('网页切库后迟到的旧库资料不覆盖新库内容')
}

async function desktopTests() {
  const multi = await desktopFixture()
  await multi.page.getByTestId('shared-library-entry-ws-a').waitFor(); await multi.page.getByTestId('shared-library-entry-ws-b').waitFor()
  assert.equal(multi.calls.filter(call => call.endpoint === '/api/sync/docs/search').length, 0); assert.equal(await multi.page.getByRole('button', { name: '绑定', exact: true }).count(), 0)
  await capture(multi.page, 'desktop-first-choice.png'); pass('桌面首次多资料库一眼可选，未随机进入资料库')
  await multi.page.getByTestId('shared-library-entry-ws-b').click(); await multi.page.getByText('检修库资料.txt', { exact: true }).waitFor(); assert.equal(multi.state.selected, wsB.id)
  await multi.page.reload(); await multi.page.getByRole('button', { name: '打开文件库', exact: true }).click(); await multi.page.getByText('检修库资料.txt', { exact: true }).waitFor(); pass('桌面一次点击选择自动保存，重新打开沿用上次资料库')
  await multi.page.evaluate(() => window.libraryTest.navigate('/settings')); await multi.page.getByText('云服务', { exact: true }).first().waitFor()
  assert.equal(await multi.page.getByRole('button', { name: '绑定', exact: true }).count(), 0); assert.equal(await multi.page.getByText('当前绑定', { exact: true }).count(), 0); assert.equal(await multi.page.getByText('已登录，未绑定空间', { exact: true }).count(), 0)
  await capture(multi.page, 'desktop-cloud-settings.png', true); pass('设置仅云服务账号与地址，没有绑定工作空间动作')
  await multi.page.evaluate(() => window.libraryTest.navigate('/team')); await multi.page.getByText('运维团队', { exact: true }).first().click(); await multi.page.getByText('运维团队 — 成员', { exact: true }).waitFor()
  assert.equal(await multi.page.evaluate(() => window.libraryTest.session().current?.id), wsB.id); assert.equal(multi.state.selected, wsB.id); pass('组织管理切换团队不改变日常查看资料库')
  await multi.close('desktop-multi-settings-team')

  const single = await desktopFixture({ available: [wsA] }); await single.page.getByText('运维库资料.txt', { exact: true }).waitFor(); assert.equal(single.state.selected, wsA.id); await single.close('desktop-single-auto'); pass('桌面单资料库自动进入并完成上下文配置')
  const none = await desktopFixture({ available: [] }); await none.page.getByText('暂无可访问的资料库', { exact: false }).first().waitFor(); assert(none.page.url().endsWith('#/shared'), 'No-team login stays on cloud library instead of onboarding'); assert.equal(none.calls.filter(call => call.endpoint === '/api/sync/docs/search').length, 0); await none.close('desktop-none'); pass('桌面无资料库在当前位置说明，无额外入职引导')

  const revoke = await desktopFixture({ selected: wsA.id }); await revoke.page.getByText('运维库资料.txt', { exact: true }).waitFor(); revoke.state.available = []; await revoke.page.evaluate(() => window.libraryTest.refresh()); await revoke.page.getByText('暂无可访问的资料库', { exact: false }).first().waitFor(); assert.equal(await revoke.page.getByText('运维库资料.txt', { exact: true }).count(), 0); assert.equal(await revoke.page.evaluate(() => window.libraryTest.session().current), null); assert.equal(revoke.state.selected, ''); await revoke.close('desktop-revoked'); pass('桌面权限全部失去后清空库选择、持久化上下文和旧资料')

  const rapid = await desktopFixture({ selected: wsA.id }); await rapid.page.getByText('运维库资料.txt', { exact: true }).waitFor()
  rapid.state.heldSwitch = wsB.id; const started = new Promise(resolve => { rapid.state.switchStarted = resolve }); await rapid.page.evaluate(() => { window.libraryTest.switchLibrary('ws-b') }); await started; await rapid.page.evaluate(() => { window.libraryTest.switchLibrary('ws-a') }); rapid.state.releaseSwitch(); rapid.state.heldSwitch = null
  await rapid.page.waitForFunction(() => !window.libraryTest.session().loading && window.libraryTest.session().current?.id === 'ws-a'); assert.equal(rapid.state.selected, wsA.id); assert.equal(await rapid.page.getByText('检修库资料.txt', { exact: true }).count(), 0); pass('桌面快速连续切换最终只保留最后一次选择')
  rapid.state.failSwitch = wsB.id; await rapid.page.evaluate(() => window.libraryTest.switchLibrary('ws-b')); await rapid.page.waitForFunction(() => window.libraryTest.session().error); assert.equal(await rapid.page.evaluate(() => window.libraryTest.session().current?.id), wsA.id); assert.equal(rapid.state.selected, wsA.id); assert.equal(await rapid.page.getByText('检修库资料.txt', { exact: true }).count(), 0); pass('桌面切换失败不会把界面或发布上下文误指到目标库'); await rapid.close('desktop-rapid-switch')

  const publish = await desktopFixture({ route: '/library' }); await publish.page.locator('.doc-card .el-dropdown button').click(); await publish.page.getByText('↑ 发布 / 更新共享资料', { exact: true }).click()
  const dialog = publish.page.locator('.el-dialog:visible').last(); await dialog.getByTestId('publication-library-picker').click(); await publish.page.getByRole('option', { name: '检修团队 / 检修资料', exact: true }).click(); await dialog.getByText('指定成员', { exact: true }).click(); await dialog.getByRole('combobox').last().click(); await publish.page.getByRole('option', { name: '检修库成员', exact: true }).click()
  assert(publish.calls.some(call => call.endpoint === '/api/deliveries/members' && call.query.workspace_id === wsB.id)); await capture(publish.page, 'desktop-publish-target.png'); await dialog.getByRole('button', { name: '确认共享', exact: true }).click(); await publish.page.getByText('共享成功', { exact: true }).waitFor()
  const write = publish.calls.findLast(call => call.endpoint === '/api/sharing/publish'); assert.equal(write.body.workspace_id, wsB.id); assert.equal(write.body.access[0].user_id, 'member-b'); assert.equal(await publish.page.evaluate(() => window.libraryTest.session().current), null); await publish.close('desktop-direct-publish'); pass('桌面未选浏览库时可直接选发布目的地，成员及写入均取目标库')

  const linked = await desktopFixture({ selected: wsB.id, linked: true, route: '/library' }); await linked.page.locator('.doc-card .el-dropdown button').click(); await linked.page.getByText('↑ 发布 / 更新共享资料', { exact: true }).click(); const update = linked.page.locator('.el-dialog:visible').last()
  await update.getByTestId('linked-publication-target').waitFor(); assert((await update.getByTestId('linked-publication-target').innerText()).includes(wsA.name)); assert((await update.getByTestId('linked-publication-target').innerText()).includes('云端原设备说明.txt')); await capture(linked.page, 'desktop-update-original-library.png'); await update.getByRole('button', { name: '确认共享', exact: true }).click(); await linked.page.getByText('内容未变，保留云端当前版本', { exact: true }).waitFor()
  const original = linked.calls.findLast(call => call.endpoint === '/api/sharing/submit-version'); assert(original); assert.equal(original.body.doc_id, localDoc.id); assert.equal(original.body.expected_workspace_id, wsA.id); assert.equal(original.body.expected_document_id, 'cloud-original'); assert.equal(original.body.workspace_id, undefined, 'Original update uses stored ShareLink, never browser target'); assert.equal(linked.calls.filter(call => call.endpoint === '/api/sharing/publish').length, 0); await linked.close('desktop-original-fixed'); pass('浏览B资料库时更新原A资料：明确A与资料名，原关联请求保持不变')

  const retry = await desktopFixture({ selected: wsB.id, route: '/library' }); retry.state.publicationMode = 'uncertain'; await openPublication(retry.page)
  let retryDialog = retry.page.locator('.el-dialog:visible').last(); await retryDialog.getByRole('button', { name: '确认共享', exact: true }).click(); await retryDialog.getByText('连接暂时中断，请重试本次请求', { exact: true }).waitFor(); const frozen = retry.calls.findLast(call => call.endpoint === '/api/sharing/publish').body
  await retryDialog.getByRole('button', { name: '关闭', exact: true }).click(); await retry.page.evaluate(() => window.libraryTest.switchLibrary('ws-a')); await openPublication(retry.page); retryDialog = retry.page.locator('.el-dialog:visible').last()
  assert((await retryDialog.getByTestId('publication-library-picker').innerText()).includes(wsB.name)); assert.equal(await retryDialog.getByRole('combobox', { name: '发布到', exact: true }).isDisabled(), true)
  retry.state.publicationMode = 'success'; await retryDialog.getByRole('button', { name: '重试本次共享', exact: true }).click(); await retry.page.getByText('共享成功', { exact: true }).waitFor(); assert.deepEqual(retry.calls.findLast(call => call.endpoint === '/api/sharing/publish').body, frozen); await retry.close('desktop-frozen-retry'); pass('发布失败后切换浏览库再重试，仍锁定原目标与相同请求身份')

  const members = await desktopFixture({ route: '/library' }); await openPublication(members.page); let memberDialog = members.page.locator('.el-dialog:visible').last(); members.state.heldMembers = wsA.id; const memberStarted = new Promise(resolve => { members.state.membersStarted = resolve })
  await memberDialog.getByTestId('publication-library-picker').click(); await members.page.getByRole('option', { name: '运维团队 / 技术资料', exact: true }).click(); await memberStarted
  await memberDialog.getByTestId('publication-library-picker').click(); await members.page.getByRole('option', { name: '检修团队 / 检修资料', exact: true }).click(); await memberDialog.getByText('指定成员', { exact: true }).click(); await memberDialog.getByRole('combobox').last().click(); await members.page.getByRole('option', { name: '检修库成员', exact: true }).waitFor(); const receivedMembers = members.page.waitForResponse(response => new URL(response.url()).pathname === '/api/deliveries/members' && new URL(response.url()).searchParams.get('workspace_id') === wsA.id); members.state.heldMembers = null; members.state.releaseMembers(); await receivedMembers; await uiSettled(members.page)
  assert.equal(await members.page.getByRole('option', { name: '运维库成员', exact: true }).count(), 0); await members.page.getByRole('option', { name: '检修库成员', exact: true }).click(); await memberDialog.getByRole('button', { name: '确认共享', exact: true }).click(); await members.page.getByText('共享成功', { exact: true }).waitFor(); assert.equal(members.calls.findLast(call => call.endpoint === '/api/sharing/publish').body.access[0].user_id, 'member-b'); await members.close('desktop-late-members'); pass('发布目的地切换后迟到的旧库成员不显示、不混入访问范围')

  const late = await desktopFixture({ selected: wsA.id }); await late.page.getByText('运维库资料.txt', { exact: true }).waitFor(); late.state.heldSearch = wsB.id; const searchStarted = new Promise(resolve => { late.state.searchStarted = resolve }); await late.page.evaluate(() => window.libraryTest.switchLibrary('ws-b')); await searchStarted
  await late.page.evaluate(() => window.libraryTest.switchLibrary('ws-a')); await late.page.getByText('运维库资料.txt', { exact: true }).waitFor(); await releaseSearch(late, wsB.id); assert.equal(await late.page.getByText('检修库资料.txt', { exact: true }).count(), 0); await late.close('desktop-late-library'); pass('桌面切库后迟到搜索结果不显示旧库资料')

  const identity = await desktopFixture({ selected: wsA.id }); await identity.page.getByText('运维库资料.txt', { exact: true }).waitFor(); identity.state.heldSearch = wsB.id; const identityStarted = new Promise(resolve => { identity.state.searchStarted = resolve }); await identity.page.evaluate(() => window.libraryTest.switchLibrary('ws-b')); await identityStarted
  await identity.page.evaluate(user => window.libraryTest.login(user), userB); await releaseSearch(identity, wsB.id); await identity.page.getByTestId('shared-library-entry-ws-a').waitFor(); assert.equal(await identity.page.getByText('检修库资料.txt', { exact: true }).count(), 0); assert.equal(await identity.page.evaluate(() => window.libraryTest.session().current), null); await identity.close('desktop-late-account'); pass('桌面更换账号不沿用旧库偏好，旧账号迟到资料不可见')

  const server = await desktopFixture({ selected: wsA.id }); await server.page.getByText('运维库资料.txt', { exact: true }).waitFor(); server.state.heldSearch = wsB.id; const serverStarted = new Promise(resolve => { server.state.searchStarted = resolve }); await server.page.evaluate(() => window.libraryTest.switchLibrary('ws-b')); await serverStarted
  await server.page.evaluate(() => window.libraryTest.changeServer('http://new-server.invalid')); await releaseSearch(server, wsB.id); await server.page.getByText('请先登录云服务', { exact: false }).first().waitFor(); assert.equal(await server.page.getByText('检修库资料.txt', { exact: true }).count(), 0); await server.close('desktop-late-server'); pass('桌面更换云服务立即清除会话与旧库迟到资料')

  const regression = await desktopFixture({ selected: wsA.id }); await regression.page.getByText('运维库资料.txt', { exact: true }).waitFor(); await regression.page.getByTestId('preview-current-cloud-ws-a').click(); await regression.page.getByTestId('preview-text').waitFor(); assert.equal(await regression.page.getByTestId('preview-text').innerText(), '已授权预览内容'); await regression.page.getByTestId('cloud-file-preview').getByRole('button', { name: '关闭', exact: true }).click()
  await regression.page.getByRole('button', { name: '取回到个人库', exact: true }).click(); await regression.page.locator('.el-dialog:visible').last().getByRole('button', { name: '确认取回', exact: true }).click(); await regression.page.getByTestId('transfer-duplicate').waitFor(); assert.equal(await regression.page.getByText('已取回到个人库', { exact: true }).count(), 0); await regression.close('desktop-preview-dedup'); pass('库选择优化后预览与取回实体查重仍可用')
  for (const selected of [wsA.id, '']) {
    const duplicate = await desktopFixture({ selected, route: '/library' }); duplicate.state.publicationMode = 'duplicate'; await openPublication(duplicate.page); const duplicateDialog = duplicate.page.locator('.el-dialog:visible').last(); await duplicateDialog.getByTestId('publication-library-picker').click(); await duplicate.page.getByRole('option', { name: '检修团队 / 检修资料', exact: true }).click(); await duplicateDialog.getByRole('button', { name: '确认共享', exact: true }).click(); await duplicate.page.getByTestId('transfer-duplicate').waitFor(); await duplicate.page.getByTestId('duplicate-open-existing').click(); await duplicate.page.waitForURL('**#/shared?highlight=cloud-existing&workspace=ws-b'); await duplicate.page.locator('.el-drawer:visible').getByRole('heading', { name: '已存在说明.txt', exact: true }).waitFor()
    assert.equal(await duplicate.page.evaluate(() => window.libraryTest.session().current?.id), wsB.id); assert.equal(duplicate.state.selected, wsB.id); assert(duplicate.calls.some(call => call.endpoint === '/api/sync/cloud-docs' && call.query.workspace_id === wsB.id)); await duplicate.close(`desktop-duplicate-open-${selected || 'none'}`); pass(selected ? '浏览A发布B查重后查看已有资料：自动进入B并打开正确实体' : '尚未选浏览库发布B查重后查看已有资料：直接进入B并打开正确实体')
  }
  const deniedOriginal = await desktopFixture({ selected: wsB.id, linked: true, route: '/library' }); deniedOriginal.state.denyOriginal = true; await openPublication(deniedOriginal.page); const deniedDialog = deniedOriginal.page.locator('.el-dialog:visible').last(); await deniedDialog.getByText('无法确认原云端资料的访问权限或账号、服务身份，不能更新原资料。可选择另建资料。', { exact: true }).waitFor(); assert.equal(await deniedDialog.getByRole('radio', { name: '更新原资料', exact: true }).isDisabled(), true); assert.equal(await deniedDialog.getByTestId('linked-publication-target').count(), 0); assert.equal(deniedOriginal.calls.filter(call => call.endpoint === '/api/sharing/submit-version').length, 0); await deniedOriginal.close('desktop-original-denied'); pass('原关联资料失权后禁用更新，不展示缓存原资料名称')
  const queuedFailure = await desktopFixture({ selected: wsA.id, available: [wsA, wsB, wsC] }); await queuedFailure.page.getByText('运维库资料.txt', { exact: true }).waitFor(); queuedFailure.state.heldSwitch = wsB.id; queuedFailure.state.failSwitch = wsC.id; const queuedStarted = new Promise(resolve => { queuedFailure.state.switchStarted = resolve }); await queuedFailure.page.evaluate(() => { window.libraryTest.switchLibrary('ws-b') }); await queuedStarted; await queuedFailure.page.evaluate(() => { window.libraryTest.switchLibrary('ws-c') }); queuedFailure.state.heldSwitch = null; queuedFailure.state.releaseSwitch(); await queuedFailure.page.waitForFunction(() => !window.libraryTest.session().loading && window.libraryTest.session().error)
  const queuedCurrent = await queuedFailure.page.evaluate(() => window.libraryTest.session().current?.id); assert.notEqual(queuedCurrent, wsC.id); assert.equal(queuedCurrent || '', queuedFailure.state.selected, 'After a superseded successful B followed by failed C, UI and personal backend must agree'); await queuedFailure.page.getByText('检修库资料.txt', { exact: true }).waitFor(); await queuedFailure.close('desktop-queued-failure'); pass('B请求成功而最后C选择失败时，界面与后端上下文保持同一个安全库')
  const logoutFailure = await desktopFixture({ selected: wsA.id }); await logoutFailure.page.getByText('运维库资料.txt', { exact: true }).waitFor(); logoutFailure.state.failLogout = true; await logoutFailure.page.evaluate(() => window.libraryTest.logout()); await logoutFailure.page.getByText('未退出云服务：退出暂时未完成', { exact: false }).first().waitFor(); assert.equal(await logoutFailure.page.evaluate(() => window.libraryTest.session().user?.id), userA.id); assert.equal(await logoutFailure.page.evaluate(() => window.libraryTest.session().current?.id), wsA.id); await logoutFailure.page.evaluate(() => window.libraryTest.restore()); assert.equal(await logoutFailure.page.evaluate(() => window.libraryTest.session().user?.id), userA.id); assert.equal(await logoutFailure.page.evaluate(() => window.libraryTest.session().current?.id), wsA.id); await logoutFailure.close('desktop-logout-failure')
  const lateLogout = await desktopFixture({ selected: wsA.id }); await lateLogout.page.getByText('运维库资料.txt', { exact: true }).waitFor(); lateLogout.state.heldLogout = true; const oldLogoutStarted = new Promise(resolve => { lateLogout.state.logoutStarted = resolve }); await lateLogout.page.evaluate(() => { window.libraryTest.logout() }); await oldLogoutStarted; await lateLogout.page.evaluate(user => window.libraryTest.login(user), userB); await lateLogout.page.getByTestId('shared-library-entry-ws-a').waitFor(); const logoutResponse = lateLogout.page.waitForResponse(response => new URL(response.url()).pathname === '/api/sync/logout'); lateLogout.state.heldLogout = false; lateLogout.state.releaseLogout(); await logoutResponse; await uiSettled(lateLogout.page); assert.equal(await lateLogout.page.evaluate(() => window.libraryTest.session().user?.id), userB.id); assert.equal(await lateLogout.page.evaluate(() => window.libraryTest.session().current), null); await lateLogout.close('desktop-late-logout'); pass('退出失败明确保留原账号，旧退出成功的迟到响应不会清除新登录账号')
  for (const [route, entry] of [['/settings', '设置'], ['/team', '组织管理'], ['/shared', '顶部账号菜单']]) {
    const buttonLogout = await desktopFixture({ selected: wsA.id, route }); buttonLogout.state.failLogout = true
    if (route === '/shared') { await buttonLogout.page.getByText('运维库资料.txt', { exact: true }).waitFor(); await buttonLogout.page.getByTitle('云服务账号', { exact: true }).click(); await buttonLogout.page.getByText('退出云服务', { exact: true }).click() }
    else { await buttonLogout.page.getByRole('button', { name: '退出登录', exact: true }).click() }
    await buttonLogout.page.locator('.el-message--error').getByText('未退出云服务：退出暂时未完成', { exact: true }).waitFor()
    assert.equal(await buttonLogout.page.locator('.el-message--success').count(), 0, 'Failed logout must not report successful exit')
    assert.equal(await buttonLogout.page.evaluate(() => window.libraryTest.session().user?.id), userA.id); assert.equal(await buttonLogout.page.evaluate(() => window.libraryTest.session().current?.id), wsA.id); assert.equal(buttonLogout.state.user.id, userA.id)
    await buttonLogout.close(`desktop-${route.slice(1)}-logout-button`); pass(`${entry}真实退出按钮失败时保留账号并提示错误，不报告退出成功`)
  }
  const inFlightLogout = await desktopFixture({ selected: wsA.id }); await inFlightLogout.page.getByText('运维库资料.txt', { exact: true }).waitFor(); inFlightLogout.state.heldSwitch = wsB.id; inFlightLogout.state.failLogout = true; const inFlightStarted = new Promise(resolve => { inFlightLogout.state.switchStarted = resolve }); await inFlightLogout.page.evaluate(() => { window.libraryTest.switchLibrary('ws-b') }); await inFlightStarted; await inFlightLogout.page.getByTitle('云服务账号', { exact: true }).click(); await inFlightLogout.page.getByText('退出云服务', { exact: true }).click(); assert.equal(inFlightLogout.calls.filter(call => call.endpoint === '/api/sync/logout').length, 0, 'Logout waits for the owned in-flight selection instead of losing its acknowledged target')
  const selectedResponse = inFlightLogout.page.waitForResponse(response => new URL(response.url()).pathname === '/api/sync/workspace' && response.request().postDataJSON()?.workspace_id === wsB.id); inFlightLogout.state.heldSwitch = null; inFlightLogout.state.releaseSwitch(); await selectedResponse; await inFlightLogout.page.locator('.el-message--error').getByText('未退出云服务：退出暂时未完成', { exact: true }).waitFor(); await inFlightLogout.page.getByText('检修库资料.txt', { exact: true }).waitFor(); assert.equal(await inFlightLogout.page.evaluate(() => window.libraryTest.session().user?.id), userA.id); assert.equal(await inFlightLogout.page.evaluate(() => window.libraryTest.session().current?.id), inFlightLogout.state.selected); assert.equal(inFlightLogout.state.selected, wsB.id); assert.equal(await inFlightLogout.page.locator('.el-message--success').count(), 0); await inFlightLogout.close('desktop-inflight-logout-failure'); pass('在飞切库B成功后退出失败，保留原账号并显示与后端一致的B资料库')
  for (const memberProblem of ['removed', 'unavailable']) {
    const frozenReceipt = await desktopFixture({ selected: wsB.id, route: '/library' }); frozenReceipt.state.publicationMode = 'uncertain'; await openPublication(frozenReceipt.page); let receiptDialog = frozenReceipt.page.locator('.el-dialog:visible').last(); await receiptDialog.getByText('指定成员', { exact: true }).click(); await receiptDialog.getByRole('combobox').last().click(); await frozenReceipt.page.getByRole('option', { name: '检修库成员', exact: true }).click(); await receiptDialog.getByRole('button', { name: '确认共享', exact: true }).click(); await receiptDialog.getByText('连接暂时中断，请重试本次请求', { exact: true }).waitFor()
    const originalBody = frozenReceipt.calls.findLast(call => call.endpoint === '/api/sharing/publish').body; assert.equal(originalBody.visibility, 'restricted'); assert.deepEqual(originalBody.access, [{ user_id: 'member-b', role: 'reader' }]); await receiptDialog.getByRole('button', { name: '关闭', exact: true }).click(); if (memberProblem === 'removed') frozenReceipt.state.removedMembers = wsB.id; else frozenReceipt.state.failedMembers = wsB.id
    const membersReceived = frozenReceipt.page.waitForResponse(response => new URL(response.url()).pathname === '/api/deliveries/members' && new URL(response.url()).searchParams.get('workspace_id') === wsB.id); await openPublication(frozenReceipt.page); await membersReceived; await uiSettled(frozenReceipt.page); receiptDialog = frozenReceipt.page.locator('.el-dialog:visible').last(); const retryButton = receiptDialog.getByRole('button', { name: '重试本次共享', exact: true }); await retryButton.waitFor(); assert.equal(await retryButton.isEnabled(), true, 'The original request may already have committed; checking its receipt must not depend on current recipient membership'); assert.equal(await receiptDialog.getByRole('combobox', { name: '发布到', exact: true }).isDisabled(), true)
    frozenReceipt.state.publicationMode = 'success'; await retryButton.click(); await frozenReceipt.page.getByText('共享成功', { exact: true }).waitFor(); assert.deepEqual(frozenReceipt.calls.findLast(call => call.endpoint === '/api/sharing/publish').body, originalBody, 'Retry preserves request ID, original recipient, destination and every payload field'); await frozenReceipt.close(`desktop-frozen-receipt-${memberProblem}`); pass(memberProblem === 'removed' ? '受限发布结果不明且原成员被移除后，仍用完整原请求核对收据' : '受限发布结果不明且成员列表不可用时，仍用完整原请求核对收据')
  }
  const frozenLibraryList = await desktopFixture({ selected: wsB.id, route: '/library' }); frozenLibraryList.state.publicationMode = 'uncertain'; await openPublication(frozenLibraryList.page); let frozenLibraryDialog = frozenLibraryList.page.locator('.el-dialog:visible').last(); await frozenLibraryDialog.getByRole('button', { name: '确认共享', exact: true }).click(); await frozenLibraryDialog.getByText('连接暂时中断，请重试本次请求', { exact: true }).waitFor(); const frozenLibraryBody = frozenLibraryList.calls.findLast(call => call.endpoint === '/api/sharing/publish').body; await frozenLibraryDialog.getByRole('button', { name: '关闭', exact: true }).click(); frozenLibraryList.state.failedWorkspaces = true
  const libraryRefreshFailed = frozenLibraryList.page.waitForResponse(response => new URL(response.url()).pathname === '/api/sync/workspaces' && response.status() === 503); await openPublication(frozenLibraryList.page); await libraryRefreshFailed; await uiSettled(frozenLibraryList.page); frozenLibraryDialog = frozenLibraryList.page.locator('.el-dialog:visible').last(); assert.equal(await frozenLibraryDialog.getByRole('button', { name: '重试本次共享', exact: true }).isEnabled(), true, 'Known frozen target may verify the original receipt when a fresh library listing is unavailable'); frozenLibraryList.state.publicationMode = 'success'; await frozenLibraryDialog.getByRole('button', { name: '重试本次共享', exact: true }).click(); await frozenLibraryList.page.getByText('共享成功', { exact: true }).waitFor(); assert.deepEqual(frozenLibraryList.calls.findLast(call => call.endpoint === '/api/sharing/publish').body, frozenLibraryBody); await frozenLibraryList.close('desktop-frozen-library-list')
  const freshLibraryList = await desktopFixture({ selected: wsB.id, route: '/library' }); freshLibraryList.state.failedWorkspaces = true; const newLibraryRefreshFailed = freshLibraryList.page.waitForResponse(response => new URL(response.url()).pathname === '/api/sync/workspaces' && response.status() === 503); await openPublication(freshLibraryList.page); await newLibraryRefreshFailed; await uiSettled(freshLibraryList.page); const freshLibraryDialog = freshLibraryList.page.locator('.el-dialog:visible').last(); assert.equal(await freshLibraryDialog.getByRole('button', { name: '确认共享', exact: true }).isDisabled(), true, 'A new publication must still verify available libraries'); assert.equal(freshLibraryList.calls.filter(call => call.endpoint === '/api/sharing/publish').length, 0); await freshLibraryList.close('desktop-new-library-list-failure'); pass('资料库列表不可用时已冻结原请求可核对收据，新发布仍被阻止')
}

async function openPublication(page) { await page.locator('.doc-card .el-dropdown button').click(); await page.getByText('↑ 发布 / 更新共享资料', { exact: true }).click(); await page.locator('.el-dialog:visible').last().getByTestId('publication-library-picker').waitFor() }

main().then(() => { saveResults('passed'); console.log('Evidence: ' + evidence) }).catch(async error => { await currentPage?.screenshot({ path: path.join(evidence, 'failure.png') }).catch(() => {}); saveResults('failed', error); console.error(error); console.error('Evidence: ' + evidence); process.exitCode = 1 }).finally(async () => { await browser?.close(); if (service && service.exitCode === null) { service.kill('SIGTERM'); await wait(100) } fs.rmSync(harnessPath, { force: true }) })
