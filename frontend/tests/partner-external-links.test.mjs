import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)
const mainPath = new URL('../../electron/main.js', import.meta.url)
const mainSource = await fs.readFile(mainPath, 'utf8')
const preloadSource = await fs.readFile(new URL('../../electron/preload.js', import.meta.url), 'utf8')
const safeError = '无法打开此链接，请检查网址后重试'

function mainFixture({ dev = false, response } = {}) {
  const handlers = new Map()
  const listeners = new Map()
  let ready, beforeSendHeaders
  const opened = []
  const localURL = pathToFileURL(fileURLToPath(new URL('../dist/index.html', import.meta.url))).href
  const frame = { url: localURL }
  const contents = { mainFrame: frame }
  const window = { webContents: contents, isDestroyed: () => false }
  const shell = { openExternal: async url => { opened.push(url) }, openPath: async () => '',
    showItemInFolder() {} }
  const electron = {
    app: { isPackaged: !dev, on() {}, whenReady: () => ({ then(callback) { ready = callback } }) },
    BrowserWindow: class {}, ipcMain: { handle: (name, handler) => handlers.set(name, handler),
      on: (name, handler) => listeners.set(name, handler) },
    Menu: {}, shell, dialog: { showOpenDialog: async () => ({ canceled: true }) },
    session: { defaultSession: { setProxy: async () => {}, webRequest: {
      onHeadersReceived() {}, onBeforeSendHeaders(callback) { beforeSendHeaders = callback },
    } } },
  }
  const context = vm.createContext({
    require: name => name === 'electron' ? electron
      : name === 'axios' ? { create: () => ({ get: async (...args) => response?.(...args) ?? { status: 200, data: '<html>other app</html>' } }) }
      : name === './utils/file-access' ? { isDirectory: () => true, listDirectoryContents: () => [] }
      : ['path', 'fs', 'url', 'crypto'].includes(name) ? require(name)
      : name.startsWith('./utils/') ? require(path.resolve(path.dirname(fileURLToPath(mainPath)), name)) : {},
    __dirname: path.dirname(fileURLToPath(mainPath)),
    process: { env: {}, platform: 'darwin', versions: {}, on() {} },
    console: { log() {}, error() {} }, URL, Buffer, setTimeout, __window: window,
  })
  vm.runInContext(mainSource, context, { filename: fileURLToPath(mainPath) })
  vm.runInContext('mainWindow = __window; backendManager = { getBackendURL: () => "http://127.0.0.1:8001", aiCapability: "private-capability" }', context)
  return {
    opened, shell, frame, contents, window, localURL, handlers, listeners,
    event: { sender: contents, senderFrame: frame },
    setWindow(value) { context.__window = value; vm.runInContext('mainWindow = __window', context) },
    setFloatingWindow(value) { context.__floating = value; vm.runInContext('floatingWindow = __floating', context) },
    setDevURL(url) { context.__devURL = url; vm.runInContext('verifiedDevFrontendURL = __devURL', context) },
    async filterHeaders(url, requestHeaders) {
      vm.runInContext('createWindow = () => {}', context)
      await ready()
      if (!beforeSendHeaders) return requestHeaders
      return new Promise(resolve => beforeSendHeaders({ url, requestHeaders }, result => resolve(result.requestHeaders)))
    },
    wait(url, secret, timeout = 1) {
      context.__url = url; context.__secret = secret
      context.__timeout = timeout
      return vm.runInContext('waitForFrontend(__url, __timeout, __secret)', context)
    },
    async open(event, url) {
      const handler = handlers.get('open-external-link')
      assert.equal(typeof handler, 'function', 'main process must register an explicit external-link handler')
      return handler(event, url)
    },
  }
}

test('preload exposes an explicit Promise bridge without opening a link on load', async () => {
  let exposed
  const invoked = []
  const context = vm.createContext({
    require: () => ({
      contextBridge: { exposeInMainWorld: (name, api) => { assert.equal(name, 'electron'); exposed = api } },
      ipcRenderer: {
        sendSync: () => ({}),
        invoke: async (...args) => { invoked.push(args); return { success: true } },
      }, webUtils: {},
    }),
    process: { platform: 'darwin' }, console: { log() {} },
  })
  vm.runInContext(preloadSource, context)
  assert.deepEqual(invoked, [])
  assert.equal(typeof exposed.openExternalLink, 'function')
  const pending = exposed.openExternalLink('https://example.test/reference')
  assert.equal(typeof pending.then, 'function')
  assert.deepEqual(await pending, { success: true })
  assert.deepEqual(invoked, [['open-external-link', 'https://example.test/reference']])
})

for (const [address, normalized] of [
  ['https://EXAMPLE.test:443/reference?q=1&next=2#source', 'https://example.test/reference?q=1&next=2#source'],
  ['http://example.test:80', 'http://example.test/'],
  ['https://example.test/参考', 'https://example.test/%E5%8F%82%E8%80%83'],
]) {
  test(`main opens a normalized HTTP(S) reference from its own local main frame: ${address}`, async () => {
    const f = mainFixture()
    assert.deepEqual(f.opened, [])
    const result = await f.open(f.event, address)
    assert.equal(result.success, true)
    assert.deepEqual(f.opened, [normalized])
  })
}

for (const address of [
  undefined, null, 123, {}, '', 'example.test', '//example.test',
  'file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,hello', 'mailto:person@example.test',
  'https://', 'https:example.test', 'https:\\example.test',
  ' https://example.test', 'https://example.test ',
  'https://user:secret@example.test',
  'https://example.test/\nother', 'https://example.test/\r', 'https://example.test/\t',
  'https://example.test/\u0000', 'https://example.test/\u007f', 'https://example.test/\u0085',
  `https://example.test/${'x'.repeat(8192)}`,
]) {
  test(`main refuses invalid or unsafe external addresses: ${JSON.stringify(address)?.slice(0, 90)}`, async () => {
    const f = mainFixture()
    await assert.rejects(f.open(f.event, address), error => error.message === safeError)
    assert.deepEqual(f.opened, [])
  })
}

test('main refuses untrusted contents, child frames, missing windows and remote origins', async () => {
  const f = mainFixture()
  for (const event of [
    {},
    { sender: { mainFrame: f.frame }, senderFrame: f.frame },
    { sender: f.contents, senderFrame: { url: f.frame.url } },
    { sender: f.contents, senderFrame: null },
  ]) {
    await assert.rejects(f.open(event, 'https://example.test'), error => error.message === safeError)
  }
  for (const url of ['https://example.test', 'http://localhost:5173', 'about:blank', 'not-a-url', '']) {
    f.frame.url = url
    await assert.rejects(f.open(f.event, 'https://example.test'), error => error.message === safeError)
  }
  f.frame.url = f.localURL
  f.window.isDestroyed = () => true
  await assert.rejects(f.open(f.event, 'https://example.test'), error => error.message === safeError)
  f.setWindow(null)
  await assert.rejects(f.open(f.event, 'https://example.test'), error => error.message === safeError)
  assert.deepEqual(f.opened, [])
})

test('system browser failures reject with a fixed safe message', async () => {
  const f = mainFixture()
  f.shell.openExternal = async () => { throw new Error('/private/users/alice/secret-browser-token') }
  await assert.rejects(f.open(f.event, 'https://example.test'), error => error.message === safeError)
})

test('the default deny policy for popups remains in place', () => {
  assert.match(mainSource, /setWindowOpenHandler\(\(\) => \(\{ action: 'deny' \}\)\)/)
})

test('unrelated HTTP 200 frontends are rejected without a valid launch challenge', async () => {
  const f = mainFixture({ dev: true })
  await assert.rejects(f.wait('http://127.0.0.1:5173/', 'a'.repeat(64)))
})

test('external links work from the verified development main page only', async () => {
  const f = mainFixture({ dev: true })
  f.setDevURL('http://127.0.0.1:5178/')
  f.frame.url = 'http://127.0.0.1:5178/#/partner'
  assert.equal((await f.open(f.event, 'https://example.test')).success, true)
  for (const url of ['http://127.0.0.1:5177/', 'http://localhost:5178/',
    'http://127.0.0.1:5178/admin.html', 'http://127.0.0.1:5178/?changed=1']) {
    f.frame.url = url
    await assert.rejects(f.open(f.event, 'https://example.test'))
  }
})

test('IPC file operations reject all untrusted senders before accessing the filesystem', async () => {
  const f = mainFixture()
  for (const channel of ['open-path', 'show-item-in-folder', 'select-directory',
    'select-file', 'check-is-directory', 'list-directory-contents', 'toggle-floating-window',
    'floating-window-drop-files']) {
    for (const event of [{}, { sender: { mainFrame: f.frame }, senderFrame: f.frame },
      { sender: f.contents, senderFrame: { url: f.frame.url } }]) {
      await assert.rejects(async () => f.handlers.get(channel)(event, '/tmp'), /不可信/)
    }
  }
})

test('exact packaged page identity is required for IPC and capability disclosure', async () => {
  const f = mainFixture()
  for (const url of ['file:///tmp/untrusted.html', f.localURL.replace('index.html', 'admin.html'), f.localURL + '?changed=1']) {
    f.frame.url = url
    await assert.rejects(f.open(f.event, 'https://example.test'))
    await assert.rejects(async () => f.handlers.get('open-path')(f.event, '/tmp'), /不可信/)
    const sync = { ...f.event }
    f.listeners.get('get-app-config-sync')(sync)
    assert.equal(sync.returnValue?.aiCapability, undefined)
  }
  f.frame.url = f.localURL + '#/library'
  assert.equal(await f.handlers.get('open-path')(f.event, '/tmp'), '')
  const sync = { ...f.event }
  f.listeners.get('get-app-config-sync')(sync)
  assert.equal(sync.returnValue.aiCapability, 'private-capability')
})

test('main accepts only the current challenge response, and prohibits HTTP redirects', async () => {
  const secret = 'b'.repeat(64)
  let observedChallenge
  const f = mainFixture({ dev: true, response: (url, options) => {
    assert.equal(options.maxRedirects, 0)
    assert.ok(options.maxContentLength <= 128)
    assert.ok(!url.includes(secret))
    const target = new URL(url)
    assert.equal(target.pathname, '/__teamhub_desktop_challenge')
    observedChallenge = target.searchParams.get('challenge')
    return { status: 200, data: require('crypto').createHmac('sha256', secret).update(observedChallenge).digest('hex') }
  } })
  assert.equal(await f.wait('http://127.0.0.1:5178/', secret, 100), true)
  const previousChallenge = observedChallenge
  assert.equal(await f.wait('http://127.0.0.1:5178/', secret, 100), true)
  assert.notEqual(observedChallenge, previousChallenge)
  const redirected = mainFixture({ dev: true, response: () => ({ status: 302, data: 'a'.repeat(64) }) })
  await assert.rejects(redirected.wait('http://127.0.0.1:5178/', secret))
  for (const address of ['http://localhost:5178/', 'https://127.0.0.1:5178/', 'http://127.0.0.1:5178/other',
    'http://127.0.0.1:5178/?query=1', 'http://example.test:5178/', 'http://user:pass@127.0.0.1:5178/']) {
    await assert.rejects(f.wait(address, secret))
  }
})

test('floating frame has only its exact local document authority and cannot open browser links', async () => {
  const f = mainFixture()
  const frame = { url: f.localURL.replace('index.html', 'floating-window.html') }
  const contents = { mainFrame: frame }
  const floating = { webContents: contents, isDestroyed: () => false }
  f.setFloatingWindow(floating)
  const event = { sender: contents, senderFrame: frame }
  assert.equal(await f.handlers.get('open-path')(event, '/tmp'), '')
  const sync = { ...event }
  f.listeners.get('get-app-config-sync')(sync)
  assert.equal(sync.returnValue.aiCapability, 'private-capability')
  await assert.rejects(f.open(event, 'https://example.test'))
  frame.url = f.localURL
  await assert.rejects(async () => f.handlers.get('open-path')(event, '/tmp'), /不可信/)
  const remote = { ...event }
  f.listeners.get('get-app-config-sync')(remote)
  assert.equal(remote.returnValue.aiCapability, undefined)
})

test('Electron strips desktop capability on foreign request and redirect destinations', async () => {
  const f = mainFixture()
  for (const url of ['https://example.test/api', 'http://127.0.0.1:9000/api',
    'http://localhost:8001/api', 'http://user:secret@127.0.0.1:8001/api']) {
    const filtered = await f.filterHeaders(url, {
      'x-teamhub-ai-capability': 'private-capability', Accept: 'application/json',
    })
    assert.equal(filtered['x-teamhub-ai-capability'], undefined)
    assert.equal(filtered.Accept, 'application/json')
  }
  const local = await f.filterHeaders('http://127.0.0.1:8001/api/documents', {
    'X-TeamHub-AI-Capability': 'private-capability',
  })
  assert.equal(local['X-TeamHub-AI-Capability'], 'private-capability')
})
