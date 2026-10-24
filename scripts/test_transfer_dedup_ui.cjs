#!/usr/bin/env node
// Uses production Vue pages with bounded API mocks; no real account, library or default port is accessed.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn } = require('node:child_process')
const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-transfer-dedup-ui-'))
const harnessName = `.dedup-${crypto.randomUUID()}.html`
const harness = path.join(repo, 'frontend', harnessName)
const playwrightPath = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(playwrightPath) ? playwrightPath : 'playwright'))
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const groups = [], errors = [], requests = []
let service, browser, page
let localStatus = 'organized', publicationMode = 'duplicate', linked = false, holdTransfer = false, held, replayDuplicate = false, pendingIntake = false, denyCloudList = false, denyCloudDetail = false, anotherDelivery = false
const localDoc = { id: 'local-existing', name: '已有设备说明.txt', status: 'organized', storage_path: '设备/已有设备说明.txt', storage_mode: 'move', tags: [], file_count: 1, total_size: 20, updated_at: '2026-10-13T00:00:00Z', current_version: { id: 'lv1', version_number: 1, content_hash: 'a'.repeat(64) } }
const cloudDoc = { id: 'cloud-existing', name: '云端设备说明.txt', doc_type: 'file', is_folder: false, total_size: 20, tags: [], current_version_id: 'cv1', visibility: 'workspace', capabilities: { contribute: true }, versions: [{ id: 'cv1', version_number: 1, is_current: true, original_filename: '设备说明.txt', file_size: 20 }] }
const cloudBrief = { document_id: cloudDoc.id, name: cloudDoc.name, doc_type: 'file', current_version_id: 'cv1', current_version_number: 1, total_size: 20 }
const duplicateLocal = () => ({ status: 'duplicate', duplicate: true, code: 'DUPLICATE_CONTENT', message: '本地已存在相同文件，未重复收录', document_id: localDoc.id, name: localDoc.name, document_status: localStatus, duplicate_document: { ...localDoc, status: localStatus } })
const duplicateCloud = { status: 'failed', code: 'DUPLICATE_CONTENT', error: { code: 'DUPLICATE_CONTENT', message: '云端已存在相同文件，未重复新增资料' }, duplicate_document: cloudBrief }
async function port() { return new Promise((resolve, reject) => { const socket = net.createServer().once('error', reject); socket.listen(0, '127.0.0.1', () => { const value = socket.address().port; socket.close(() => resolve(value)) }) }) }
function report(name) { groups.push(name); console.log('PASS ' + name) }
async function waitHeld() { for (let attempts=0;attempts<500;attempts++){if(held)return;await delay(10)}throw new Error('Transfer request was not held') }
fs.writeFileSync(harness, `<!doctype html><html><body><div id="app"></div><script type="module">
import {createApp,h,ref} from 'vue'; import {createPinia} from 'pinia'; import {createRouter,createWebHashHistory,RouterView} from 'vue-router'; import ElementPlus from 'element-plus'; import 'element-plus/dist/index.css';
import ShareBrowser from '/src/cloud/ShareBrowser.vue'; import SharedInbox from '/src/pages/SharedInboxPage.vue'; import Library from '/src/pages/LibraryPage.vue'; import Deliveries from '/src/pages/DeliveriesPage.vue'; import {useAuthStore} from '/src/stores/auth.js'; import {useAppStore} from '/src/stores/app.js'; import {webSession} from '/src/cloud/api.js';
const pinia=createPinia(); const auth=useAuthStore(pinia), app=useAppStore(pinia); const ws=ref('ws-test');
app.libraryPath='/synthetic/library'; app.libraryInitialized=true; app.settings={...app.settings,cloud_api_url:'http://synthetic.invalid',library_show_tree_view:false}; app.syncStatus={workspace_id:'ws-test',workspace_name:'测试空间',server_id:'test-server'}; await auth.restoreSession(); webSession.user={id:'test-user',display_name:'测试'}; webSession.serverId='test-server';
const routes=[{path:'/shared',component:SharedInbox},{path:'/library',component:Library},{path:'/pending',component:{render:()=>h('div',{'data-testid':'pending-destination'},'待整理入口')}},{path:'/deliveries',component:Deliveries},{path:'/cloud',component:{render:()=>h(ShareBrowser,{workspaceId:ws.value})}}];
const router=createRouter({history:createWebHashHistory(),routes});
window.dedupTest={navigate:p=>router.push(p),switchIdentity:()=>{auth.user={id:'other-user'};webSession.user={id:'other-user'}},switchLibrary:()=>{app.libraryPath='/synthetic/other-library'},switchWorkspace:()=>{ws.value='ws-other';auth.currentWorkspace={id:'ws-other'}}};
createApp({render:()=>h(RouterView)}).use(pinia).use(router).use(ElementPlus).mount('#app');
</script></body></html>`)
async function main() {
  const testPort = await port(), origin = `http://127.0.0.1:${testPort}`
  const log = fs.openSync(path.join(temp, 'vite.log'), 'w')
  service = spawn(process.execPath, [path.join(repo, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(testPort), '--strictPort'], { cwd: path.join(repo, 'frontend'), stdio: ['ignore', log, log] }); fs.closeSync(log)
  for (let attempts = 0; attempts < 200; attempts++) { try { if ((await fetch(origin)).ok) break } catch {} if (attempts === 199) throw new Error('Vite did not start'); await delay(50) }
  browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' })
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } }); page.setDefaultTimeout(8000); page.on('pageerror', e => errors.push(e.message))
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname
    if (!pathname.startsWith('/api/')) { if (url.origin !== origin) throw new Error('Unexpected external request: ' + url.href); return route.continue() }
    let body; try { body = request.postDataJSON() } catch { body = request.postData() }
    requests.push({ path: pathname, method: request.method(), body })
    let value, status = 200
    if (['/api/sharing/pull', '/api/sharing/publish', '/api/sharing/submit-version'].includes(pathname) || pathname.endsWith('/intake') || pathname.endsWith('/retry')) {
      if (holdTransfer) { await new Promise(resolve => { held = resolve }); holdTransfer = false }
      if (pathname.endsWith('/pull') || pathname.endsWith('/intake') || pathname.endsWith('/retry')) value = duplicateLocal()
      else if (publicationMode === 'unchanged') value = { status: 'succeeded', code: 'CONTENT_UNCHANGED', document_id: cloudDoc.id }
      else { status = 409; value = { error: { code: 'DUPLICATE_CONTENT', message: duplicateCloud.error.message, details: { workspace_id: 'ws-test', duplicate_document: cloudBrief } } } }
    } else if (/\/publications\/[^/]+$/.test(pathname)) { if(replayDuplicate)value=duplicateCloud;else{status = 404; value = { error: { code: 'NOT_FOUND', message: '不存在' } }} }
    else if (pathname.endsWith('/publications') || /\/documents\/cloud-existing\/versions$/.test(pathname) && request.method() === 'POST') { if(holdTransfer){await new Promise(resolve=>{held=resolve});holdTransfer=false}value = publicationMode === 'unchanged' ? { status: 'succeeded', code: 'CONTENT_UNCHANGED', document_id: cloudDoc.id } : duplicateCloud }
    else if (pathname.endsWith('/uploads')) value = { upload_id: 'upload-test', sha256: 'a'.repeat(64) }
    else if (pathname === '/api/sync/status') value = { logged_in: true, workspace_id: 'ws-test', user: { id: 'test-user' } }
    else if (pathname === '/api/sync/teams') value = [{ id: 'team-test', name: '测试组织' }]
    else if (pathname === '/api/sync/workspaces') value = { workspaces: [{ id: 'ws-test', name: '测试空间', team_id: 'team-test' }] }
    else if (pathname === '/api/sharing/context') value = { workspace_id: 'ws-test', workspace_name: '测试空间', server_id: 'test-server', user_id: 'test-user', can_tag: true }
    else if (pathname === '/api/documents/search') value = { documents: [localDoc], total: 1 }
    else if (pathname === '/api/sync/docs/search' || pathname.endsWith('/documents/search')) value = { items: [cloudDoc], total: 1 }
    else if (pathname === '/api/sync/cloud-docs') value = { documents: denyCloudList ? [] : [cloudDoc] }
    else if (pathname === '/api/documents/local-existing') value = localDoc
    else if (pathname === '/api/ai/indexes/local-existing') value = { state: 'not_indexed', unit_count: 0, units: [] }
    else if (pathname.endsWith('/documents/cloud-existing')) value = cloudDoc
    else if (pathname.startsWith('/api/sharing/links/')) value = { linked }
    else if (pathname.endsWith('/versions') || pathname.startsWith('/api/sharing/tags/')) {
      if(denyCloudDetail){status=403;value={error:{message:'资料已不存在或当前账号无权查看'}}}
      else value=pathname.endsWith('/versions')?cloudDoc.versions:[]
    }
    else if (pathname.endsWith('/delivery-members') || pathname === '/api/deliveries/members') value = [{ id: 'test-user', user_id: 'test-user', display_name: '测试成员' }]
    else if (pathname === '/api/deliveries' || pathname.endsWith('/deliveries')) value = { items: [{ id: 'delivery-test', status: 'sent', items: [{ id: 'item-test', filename: anotherDelivery ? '之前查看的投递.txt' : '投递资料.txt' }], sender_name: '成员 A', sent_at: '2026-10-13T00:00:00Z' }], total: 1 }
    else if (pathname.endsWith('/deliveries/delivery-test') || pathname === '/api/deliveries/delivery-test') value = { id: 'delivery-test', status: 'sent', can_download: true, items: [{ id: 'item-test', filename: anotherDelivery ? '之前查看的投递.txt' : '投递资料.txt' }], sender_name: '成员 A' }
    else if (pathname.endsWith('/unread-count')) value = { count: 1 }
    else if (pathname === '/api/deliveries/operations') value = pendingIntake ? [{ request_id: 'pending-intake-test', kind: 'delivery_intake' }] : []
    else if (pathname.includes('/favorites') || pathname.startsWith('/api/tags') || pathname.includes('/missing') || pathname.startsWith('/api/sharing/tags/') || pathname.includes('/operations') || pathname.includes('/tree') || pathname.includes('/directories')) value = []
    else if (request.method() === 'POST') value = {}
    else { throw new Error('Unmocked endpoint, refusing real service access: ' + pathname) }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value), headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*' } })
  })
  const visibleDialog = () => page.locator('.el-dialog:visible').last()
  async function start(target) { await page.goto(`${origin}/${harnessName}?run=${crypto.randomUUID()}#${target}`); await page.waitForFunction(() => window.dedupTest); await page.locator('h1,h2,.doc-card').first().waitFor() }
  async function upload() { await page.getByTestId('new-publication').click(); await page.getByLabel('上传文件', { exact: true }).setInputFiles({ name: '相同内容.txt', mimeType: 'text/plain', buffer: Buffer.from('same-content') }); await page.getByText('确认文件内容、目标资料与访问范围', { exact: true }).click(); await visibleDialog().getByRole('button', { name: '确认上传', exact: true }).click() }
  await start('/cloud'); await upload()
  await page.getByTestId('transfer-duplicate').waitFor({ timeout: 4000 })
  assert.equal(await visibleDialog().getByRole('button', { name: '核对并重试' }).count(), 0, 'duplicate is terminal')
  assert(!requests.find(r => r.path.endsWith('/publications') && r.method === 'POST').body.force_new, 'UI must not bypass dedup')
  await page.getByTestId('duplicate-open-existing').click(); await page.locator('.el-drawer:visible').getByRole('heading', { name: cloudDoc.name }).waitFor()
  report('网页上传重复为终态，禁止强制重复并可查看已有云资料')
  replayDuplicate=true; const previousUploadCount=requests.filter(r=>r.path.endsWith('/uploads')).length; await start('/cloud');await upload();await page.getByTestId('transfer-duplicate').waitFor();assert.equal(requests.filter(r=>r.path.endsWith('/uploads')).length,previousUploadCount,'previous duplicate result must not upload again');replayDuplicate=false
  report('网页核对已有重复终态时不再次上传文件')
  await start('/shared'); await page.getByRole('button', { name: '取回到个人库', exact: true }).click(); await visibleDialog().getByRole('button', { name: '确认取回', exact: true }).click()
  await page.getByTestId('transfer-duplicate').waitFor(); assert.equal(await page.getByText('已取回到个人库', { exact: true }).count(), 0)
  await page.getByTestId('duplicate-open-existing').click(); await page.waitForURL('**#/library?highlight=local-existing'); await page.locator('.el-drawer:visible').getByRole('heading', { name: localDoc.name }).waitFor()
  report('取回命中文档库实体：未报告取回成功，定位已有资料')
  localStatus = 'pending'; await start('/shared'); await page.getByRole('button', { name: '取回到个人库', exact: true }).click(); await visibleDialog().getByRole('button', { name: '确认取回', exact: true }).click(); await page.getByTestId('transfer-duplicate').waitFor(); await page.getByTestId('duplicate-open-existing').click(); await page.waitForURL('**#/pending?highlight=local-existing'); await page.getByTestId('pending-destination').waitFor()
  report('取回命中待整理实体：跳转待整理已有资料')
  if(!process.env.TEAMHUB_DEDUP_REVIEW_CASE||process.env.TEAMHUB_DEDUP_REVIEW_CASE==='highlight'){
    await start('/shared');await page.getByRole('button',{name:'查看详情',exact:true}).click();await page.locator('.el-drawer:visible').getByText(cloudDoc.name,{exact:true}).waitFor();denyCloudList=true;await page.evaluate(()=>window.dedupTest.navigate('/shared?highlight=cloud-hidden&workspace=ws-test'));await page.getByText('资料已不存在或当前账号无权查看',{exact:true}).waitFor();await delay(350);assert.equal(await page.locator('.el-drawer:visible').count(),0,'inaccessible highlight must clear previous detail');denyCloudList=false
    report('查看已有云资料失权或不存在时清空此前详情')
  }
  if(!process.env.TEAMHUB_DEDUP_REVIEW_CASE||process.env.TEAMHUB_DEDUP_REVIEW_CASE==='detail'){
    await start('/shared');await page.getByRole('button',{name:'查看详情',exact:true}).click();await page.locator('.el-drawer:visible').getByText(cloudDoc.name,{exact:true}).waitFor();denyCloudDetail=true;await page.evaluate(()=>window.dedupTest.navigate('/shared?highlight=cloud-existing&workspace=ws-test'));await page.getByText('资料已不存在或当前账号无权查看',{exact:true}).first().waitFor();await delay(350);assert.equal(await page.locator('.el-drawer:visible').count(),0,'revoked version or tag access must clear cached document');denyCloudDetail=false
    report('资料列表和详情读取之间撤权时不显示缓存名称或版本')
  }
  localStatus = 'organized'; await start('/library'); await page.locator('.doc-card .el-dropdown button').click(); await page.getByText('↑ 发布 / 更新共享资料', { exact: true }).click(); await visibleDialog().getByRole('button', { name: '确认共享', exact: true }).click(); await page.getByTestId('transfer-duplicate').waitFor()
  assert.equal(await page.locator('.el-message--error').count(), 0, 'duplicate should not cause global error toast'); assert.equal(await page.getByText('即使内容重复，也另建一份共享资料', { exact: true }).count(), 0)
  await page.getByTestId('duplicate-open-existing').click(); await page.waitForURL('**#/shared?highlight=cloud-existing&workspace=ws-test'); await page.locator('.el-drawer:visible').getByText(cloudDoc.name, { exact: true }).waitFor()
  report('本地发布重复只显示明确结果，可查看已有云资料')
  linked=true;await start('/library');await page.locator('.doc-card .el-dropdown button').click();await page.getByText('↑ 发布 / 更新共享资料',{exact:true}).click();await page.getByText('另建资料',{exact:true}).click();await visibleDialog().getByRole('button',{name:'确认共享',exact:true}).click();await page.getByTestId('transfer-duplicate').waitFor();const detached=requests.filter(r=>r.path==='/api/sharing/publish'&&r.method==='POST').at(-1);assert.equal(detached.body.force_new,true,'linked new publication keeps separate intent');assert.equal(await page.getByText('共享成功',{exact:true}).count(),0)
  report('已关联资料另建时保留意图，相同实体仍被阻止')
  publicationMode = 'unchanged'; linked = true; await start('/library'); await page.locator('.doc-card .el-dropdown button').click(); await page.getByText('↑ 发布 / 更新共享资料', { exact: true }).click(); await visibleDialog().getByRole('button', { name: '确认共享', exact: true }).click(); await page.getByText('内容未变，保留云端当前版本', { exact: true }).waitFor(); assert.equal(await page.getByText('共享成功', { exact: true }).count(), 0)
  report('更新内容未变：保留当前版，不误报新增或共享成功')
  await start('/deliveries'); await page.getByText('投递资料.txt', { exact: true }).click(); await visibleDialog().getByRole('button', { name: '收录到待整理', exact: true }).click(); await page.getByTestId('transfer-duplicate').waitFor(); assert.equal(await page.getByText('已收录到待整理', { exact: true }).count(), 0); await page.getByTestId('duplicate-open-existing').click(); await page.waitForURL('**#/library?highlight=local-existing')
  report('投递收录同样提示实体已存在，并定位已有资料')
  pendingIntake=true;await start('/deliveries');await page.getByRole('button',{name:'核对并继续',exact:true}).click();await page.getByTestId('transfer-duplicate').waitFor();assert.equal(await page.getByText('操作已完成',{exact:true}).count(),0);await page.getByTestId('duplicate-open-existing').click();await page.waitForURL('**#/library?highlight=local-existing');pendingIntake=false
  report('未完成投递收录重试命中已有实体，明确查重结果')
  pendingIntake=true;anotherDelivery=true;await start('/deliveries');await page.getByText('之前查看的投递.txt',{exact:true}).click();await visibleDialog().getByRole('heading',{name:'之前查看的投递.txt',exact:true}).waitFor();await visibleDialog().getByRole('button',{name:'关闭',exact:true}).click();await page.getByRole('button',{name:'核对并继续',exact:true}).click();await page.getByTestId('transfer-duplicate').waitFor();assert.equal(await visibleDialog().getByRole('heading',{name:'之前查看的投递.txt',exact:true}).count(),0,'retry result must not retain another receipt');assert.equal(await visibleDialog().getByRole('button',{name:'下载附件',exact:true}).count(),0,'retry result must not retain another download');await page.getByTestId('duplicate-open-existing').click();await page.waitForURL('**#/library?highlight=local-existing');pendingIntake=false;anotherDelivery=false
  report('投递恢复查重结果不混入此前投递标题或下载动作')
  holdTransfer = true; await start('/shared'); await page.getByRole('button', { name: '取回到个人库', exact: true }).click(); await visibleDialog().getByRole('button', { name: '确认取回', exact: true }).click(); await waitHeld(); await page.evaluate(() => window.dedupTest.switchIdentity()); held(); held = null; await delay(150); assert.equal(await page.getByTestId('transfer-duplicate').count(), 0, 'late duplicate must not leak previous account name'); assert.equal(await page.locator('.el-message').count(), 0, JSON.stringify(await page.locator('.el-message').allTextContents()))
  report('账号变化后迟到查重结果不泄漏、不跳转')
  publicationMode='duplicate';linked=false;holdTransfer=true;await start('/library');await page.locator('.doc-card .el-dropdown button').click();await page.getByText('↑ 发布 / 更新共享资料',{exact:true}).click();await visibleDialog().getByRole('button',{name:'确认共享',exact:true}).click();await waitHeld();await page.evaluate(()=>window.dedupTest.switchLibrary());held();held=null;await delay(150);assert.equal(await page.getByTestId('transfer-duplicate').count(),0);assert.equal(await page.locator('.el-message').count(),0)
  report('本地库变化后迟到发布查重结果不泄漏')
  holdTransfer=true;await start('/cloud');await upload();await waitHeld();await page.evaluate(()=>window.dedupTest.switchIdentity());held();held=null;await delay(150);assert.equal(await page.getByTestId('transfer-duplicate').count(),0);assert.equal(await page.getByText('原请求已保留，可核对并重试。',{exact:true}).count(),0)
  report('网页账号变化后迟到发布结果不泄漏、不显示旧重试')
  holdTransfer=true;await start('/deliveries');await page.getByText('投递资料.txt',{exact:true}).click();await visibleDialog().getByRole('button',{name:'收录到待整理',exact:true}).click();await waitHeld();await page.evaluate(()=>window.dedupTest.switchIdentity());held();held=null;await delay(150);assert.equal(await page.getByTestId('transfer-duplicate').count(),0);assert.equal(await page.getByText('已收录到待整理',{exact:true}).count(),0)
  report('投递收录在账号变化后不泄漏迟到查重结果')
  assert.deepEqual(errors, []); await page.screenshot({ path: path.join(temp, 'final.png') }); console.log('Evidence: ' + temp)
}
main().then(() => { fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify({ status: 'passed', groups, browserErrors: errors }, null, 2)) }).catch(async error => { if (page) await page.screenshot({ path: path.join(temp, 'failure.png') }).catch(() => {}); fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify({ status: 'failed', groups, error: error.stack, browserErrors: errors }, null, 2)); console.error(error); process.exitCode = 1 }).finally(async () => { held?.(); await browser?.close(); if (service && service.exitCode === null) { service.kill('SIGTERM'); await delay(100) } fs.rmSync(harness, { force: true }) })
