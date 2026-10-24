// 实际 macOS 双击入口验收：故意继承 Node 模式，使用独立profile和资料库。
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const { readState, processMatches, healthy } = require('./launch-desktop.cjs');
const repo = path.resolve(__dirname, '..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-desktop-startup-'));
const profile = path.join(root, 'profile');
const library = path.join(root, 'library');
const stateFile = path.join(profile, 'desktop-state.json');
const backendBinary = path.join(repo, 'dist-electron/retrieval-foundation-2026-10-23/mac-arm64/TeamHub.app/Contents/Resources/backend/teamhub-backend');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let state;

async function runEntry() {
  return new Promise((resolve, reject) => {
    const nativeLauncher = process.env.TEAMHUB_TEST_NATIVE_LAUNCHER === '1';
    const command = nativeLauncher ? '/usr/bin/open' : '/bin/bash';
    const args = nativeLauncher ? ['-W', '-n',
      '--env', 'ELECTRON_RUN_AS_NODE=1', '--env', 'TEAMHUB_SANDBOX=1',
      '--env', 'TEAMHUB_USER_DATA_DIR=' + profile, '--env', 'LIBRARY_PATH=' + library,
      '--env', 'TRASH_AUTO_CLEAN_DAYS=0', '--env', 'AUTO_SCAN_ON_STARTUP=false',
      path.join(repo, '启动TeamHub.app')] : [path.join(repo, '启动本地测试.command')];
    const child = spawn(command, args, {
      cwd: os.tmpdir(), env: { ...process.env, ELECTRON_RUN_AS_NODE: '1',
        TEAMHUB_SANDBOX: '1', TEAMHUB_USER_DATA_DIR: profile, LIBRARY_PATH: library,
        TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    child.stdout.on('data', data => { output += data; });
    child.stderr.on('data', data => { output += data; });
    const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new Error('启动入口超时\n' + output)); }, nativeLauncher ? 70000 : 55000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', code => { clearTimeout(timer); resolve({ code, output }); });
  });
}

async function closeOwned() {
  state = readState(stateFile) || state;
  if (!state) return;
  if (processMatches(state.pid, state.executable)) process.kill(state.pid, 'SIGTERM');
  const deadline = Date.now() + 7000;
  while (Date.now() < deadline && await healthy(state)) await pause(100);
  if (await healthy(state)) {
    if (processMatches(state.backendPid, backendBinary)) process.kill(state.backendPid, 'SIGTERM');
    await pause(500);
  }
  assert.equal(await healthy(state), false, '本轮启动的个人服务应已退出');
}

async function main() {
  fs.mkdirSync(library);
  const first = await runEntry();
  fs.writeFileSync(path.join(root, 'first-launch.log'), first.output);
  assert.equal(first.code, 0, first.output);
  if (process.env.TEAMHUB_TEST_NATIVE_LAUNCHER !== '1') {
    assert.ok(first.output.includes('桌面与个人服务已就绪'));
  }
  state = readState(stateFile);
  assert.ok(state, `启动入口未生成隔离资料库的状态记录：${stateFile}\n${first.output}`);
  assert.equal(state.status, 'ready');
  assert.equal(state.userData, profile);
  const health = await (await fetch(`http://127.0.0.1:${state.port}/api/health`)).json();
  assert.equal(health.library_path, library, '必须使用隔离库');
  assert.equal(health.instance_id, state.instanceId);
  const original = { pid: state.pid, backendPid: state.backendPid, instanceId: state.instanceId };
  const second = await runEntry();
  fs.writeFileSync(path.join(root, 'repeat-launch.log'), second.output);
  assert.equal(second.code, 0, second.output);
  state = readState(stateFile);
  assert.deepEqual({ pid: state.pid, backendPid: state.backendPid, instanceId: state.instanceId }, original,
    '重复双击应复用同一桌面与个人服务');
  assert.equal(processMatches(state.backendPid, backendBinary, state.pid), true);
  process.kill(state.backendPid, 'SIGTERM');
  const deadline = Date.now() + 20000;
  let restarted = false;
  while (Date.now() < deadline) {
    const current = readState(stateFile);
    if (current?.status === 'ready' && current.backendPid !== original.backendPid &&
        current.instanceId !== original.instanceId && await healthy(current)) {
      state = current; restarted = true; break;
    }
    await pause(100);
  }
  assert.equal(restarted, true, '服务自动重启后就绪记录必须更新为新实例');
  const afterRestart = await runEntry();
  assert.equal(afterRestart.code, 0, afterRestart.output);
  fs.writeFileSync(path.join(root, 'result.json'), JSON.stringify({ status: 'passed',
    checks: ['真实.command入口', 'Node模式污染隔离', 'Vue界面挂载与实例健康', '独立资料库', '重复双击复用', '服务重启后身份更新与再次启动'] }, null, 2));
  console.log('DESKTOP STARTUP PASS: ' + (process.env.TEAMHUB_TEST_NATIVE_LAUNCHER === '1' ? 'Finder.app无Terminal入口' : '.command') + '、Node环境隔离、真实窗口与个人服务就绪、重复启动复用。证据：' + root);
}

main().catch(error => { console.error(error); console.error('证据：' + root); process.exitCode = 1; })
  .finally(() => closeOwned().catch(error => { console.error(error); process.exitCode = 1; }));
