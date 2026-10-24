const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const { EventEmitter } = require('node:events');
const vm = require('node:vm');
const BackendManager = require('../electron/backend-manager');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-m6-manager-'));
  const script = path.join(root, 'backend.cjs');
  fs.writeFileSync(script, `require('http').createServer((req,res) => { res.setHeader('Content-Type','application/json'); res.end(JSON.stringify({status:'ok',instance_id:process.env.TEAMHUB_INSTANCE_ID})); }).listen(Number(process.env.PORT),'127.0.0.1');`);
  const probe = http.createServer();
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const manager = new BackendManager(port, root, { isDevelopment: () => true,
    getBackendExecutablePath: () => process.execPath, getBackendScriptPath: () => script });
  t.after(async () => { await manager.stop(); fs.rmSync(root, { recursive: true, force: true }); });
  return { manager, port, script };
}
test('parallel start/restart owns one child and shutdown cancels queued restart', async t => {
  const { manager } = await setup(t);
  const readiness = [];
  manager.onReady = () => readiness.push({ pid: manager.backendProcess.pid, instance: manager.instance });
  await Promise.all([manager.start(), manager.start()]);
  const first = manager.backendProcess;
  await Promise.all([manager.restart(), manager.restart()]);
  assert.notEqual(manager.backendProcess.pid, first.pid);
  assert.equal(manager.restartCount, 1);
  assert.equal(readiness.length, 2);
  assert.notEqual(readiness[0].pid, readiness[1].pid);
  assert.notEqual(readiness[0].instance, readiness[1].instance);
  assert.ok(first.exitCode !== null || first.signalCode !== null);
  manager.backendProcess.kill('SIGTERM');
  await wait(100);
  await manager.stop();
  await wait(1200);
  assert.equal(manager.backendProcess, null);
  assert.equal(manager.restartTimer, null);
});
test('wrong service on same port cannot satisfy readiness or be killed', async t => {
  const { manager, port } = await setup(t);
  const foreign = http.createServer((req, res) => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ status: 'ok', instance_id: 'foreign' })); });
  await new Promise(resolve => foreign.listen(port, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => foreign.close(resolve)));
  await assert.rejects(manager.start(), /exited/);
  assert.equal((await (await fetch(`http://127.0.0.1:${port}/api/health`)).json()).instance_id, 'foreign');
});
test('failed executable startup reports failure promptly', async t => {
  const { manager } = await setup(t);
  manager.pathHelper.getBackendExecutablePath = () => '/nonexistent/teamhub-backend';
  const before = Date.now();
  await assert.rejects(manager.start(), /ENOENT/);
  assert.ok(Date.now() - before < 3000);
});

test('AI capability is private to a desktop session and survives backend restart', async t => {
  const { manager } = await setup(t);
  const { manager: other } = await setup(t);
  assert.match(manager.aiCapability, /^[a-f0-9]{64}$/);
  assert.notEqual(manager.aiCapability, other.aiCapability);
  const original = manager.aiCapability;
  await manager.start();
  await manager.restart();
  assert.equal(manager.aiCapability, original);
});

test('restart reaps the owned frozen-backend descendant after its wrapper exits', { skip: process.platform === 'win32' }, async t => {
  const { manager, port, script } = await setup(t);
  const childScript = path.join(path.dirname(script), 'server.cjs');
  fs.writeFileSync(childScript, `
    process.on('SIGTERM', () => {});
    require('http').createServer((req,res) => {
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify({status:'ok',instance_id:process.env.TEAMHUB_INSTANCE_ID,pid:process.pid}));
    }).listen(Number(process.env.PORT),'127.0.0.1');
  `);
  fs.writeFileSync(script, `
    require('child_process').spawn(process.execPath, [${JSON.stringify(childScript)}], {stdio:'inherit'});
    setInterval(() => {}, 1000);
  `);
  const descendants = new Set();
  t.after(() => { for (const pid of descendants) { try { process.kill(pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } } });
  await manager.start();
  const firstWrapper = manager.backendProcess;
  const firstServer = await (await fetch(`http://127.0.0.1:${port}/api/health`)).json();
  descendants.add(firstServer.pid);
  const exited = once(firstWrapper, 'exit');
  firstWrapper.kill('SIGTERM');
  await exited;
  const restartResult = await manager.restart().then(() => null, error => error);
  assert.equal(restartResult, null, 'restart must wait until its old descendant releases the port');
  const next = await (await fetch(`http://127.0.0.1:${port}/api/health`)).json();
  descendants.add(next.pid);
  assert.notEqual(next.pid, firstServer.pid);
  assert.notEqual(next.instance_id, firstServer.instance_id);
  await manager.stop();
  await assert.rejects(fetch(`http://127.0.0.1:${port}/api/health`));
});

test('start waits for an in-flight stop before reusing the backend port', async t => {
  const { manager, script } = await setup(t);
  fs.appendFileSync(script, `\nprocess.on('SIGTERM', () => setTimeout(() => process.exit(0), 250));`);
  await manager.start();
  const first = manager.backendProcess;
  const stopping = manager.stop(false);
  const starting = manager.start();
  await Promise.all([stopping, starting]);
  assert.ok(manager.backendProcess, 'a requested start must not resolve while the old process is stopping');
  assert.notEqual(manager.backendProcess.pid, first.pid);
});

test('Windows shutdown targets only its spawned PID tree using structured taskkill arguments', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-manager-windows-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const child = Object.assign(new EventEmitter(), {
    pid: 123456, exitCode: null, signalCode: null,
    stdout: new EventEmitter(), stderr: new EventEmitter(),
  });
  let spawnedOptions;
  const commands = [];
  const sandbox = {
    module: { exports: {} }, __dirname: path.join(__dirname, '..', 'electron'),
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    process: { platform: 'win32', env: { SystemRoot: 'C:\\Windows' } },
    require(name) {
      if (name === 'child_process') return {
        spawn(command, args, options) { spawnedOptions = options; return child; },
        execFile(command, args, options, callback) {
          commands.push({ command, args, options });
          child.exitCode = 0;
          child.emit('exit', 0, null);
          child.emit('close', 0, null);
          callback(null);
        },
      };
      if (name === 'axios') return { create: () => ({
        get: async () => ({ status: 200, data: { instance_id: spawnedOptions.env.TEAMHUB_INSTANCE_ID } }),
      }) };
      if (name === 'fs') return { ...fs, createWriteStream: () => ({
        writableEnded: false, on() {}, write() {}, end() { this.writableEnded = true; },
      }) };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'electron', 'backend-manager.js'), 'utf8'), sandbox);
  const manager = new sandbox.module.exports(12345, root, {
    isDevelopment: () => false, getBackendExecutablePath: () => 'C:\\TeamHub\\backend.exe',
  });
  await manager.start();
  await manager.stop();
  assert.equal(spawnedOptions.detached, false);
  assert.equal(commands.length, 1);
  assert.equal(commands[0].command, path.join('C:\\Windows', 'System32', 'taskkill.exe'));
  assert.deepEqual(Array.from(commands[0].args), ['/PID', '123456', '/T', '/F']);
  assert.equal(commands[0].options.timeout, 3000);
  assert.equal(manager.backendProcess, null);
});
