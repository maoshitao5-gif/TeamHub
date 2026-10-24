const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { setTimeout: pause } = require('node:timers/promises');

const repo = path.resolve(__dirname, '../..');
const alive = pid => {
  if (!Number.isInteger(pid)) return false;
  try { process.kill(pid, 0); return true; } catch { return false; }
};

test('SIGTERM on the development launcher closes its Electron and detached personal backend', { timeout: 60000 }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-dev-shutdown-'));
  const profile = path.join(root, 'profile');
  const stateFile = path.join(profile, 'desktop-state.json');
  const log = fs.openSync(path.join(root, 'launcher.log'), 'w');
  const env = { ...process.env, TEAMHUB_SANDBOX: '1', TEAMHUB_USER_DATA_DIR: profile,
    LIBRARY_PATH: path.join(root, 'library'), TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false' };
  delete env.ELECTRON_RUN_AS_NODE;
  const launcher = spawn(process.execPath, [path.join(repo, 'start-electron-delay.js')], {
    cwd: repo, env, stdio: ['ignore', log, log],
  });
  fs.closeSync(log);
  let state;
  try {
    const deadline = Date.now() + 40000;
    while (Date.now() < deadline) {
      if (fs.existsSync(stateFile)) {
        state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
        if (state.status === 'ready') break;
        if (state.status === 'error') throw new Error(state.error);
      }
      assert.equal(launcher.exitCode, null, fs.readFileSync(path.join(root, 'launcher.log'), 'utf8'));
      await pause(100);
    }
    assert.equal(state?.status, 'ready', `桌面未就绪；证据 ${root}`);
    assert.equal(state.userData, profile);
    assert(alive(state.pid) && alive(state.backendPid), '先验证这次启动器确实拥有两层运行进程');
    launcher.kill('SIGTERM');
    const stopDeadline = Date.now() + 8000;
    while (Date.now() < stopDeadline && (alive(state.pid) || alive(state.backendPid))) await pause(100);
    assert.equal(alive(state.pid), false, `Electron 未退出；证据 ${root}`);
    assert.equal(alive(state.backendPid), false, `个人后端遗留；证据 ${root}`);
    console.log(`PASS launcher/Electron/backend exit; artifacts: ${root}`);
  } finally {
    // 仅清理由本次随机 profile 的启动状态明确记录的自有测试进程。
    if (alive(state?.pid)) process.kill(state.pid, 'SIGKILL');
    if (alive(state?.backendPid)) {
      try { process.kill(process.platform === 'win32' ? state.backendPid : -state.backendPid, 'SIGTERM'); } catch {}
    }
    if (launcher.exitCode === null && launcher.signalCode === null) launcher.kill('SIGKILL');
  }
});
