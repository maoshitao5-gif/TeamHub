// macOS Finder 入口；AppleScript applet 的 do shell script 不唤起 Terminal。
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

if (process.platform !== 'darwin') throw new Error('无终端双击入口目前只支持 macOS');
const root = path.resolve(__dirname, '..');
const mode = process.argv[2] || 'desktop';
if (!['desktop', 'accounts'].includes(mode)) throw new Error('未知入口类型');
const title = mode === 'accounts' ? '开通云端账号' : '启动TeamHub';
const target = path.join(root, title + '.app');
const source = path.join(__dirname, mode === 'accounts' ? 'cloud-account-setup.applescript' : 'desktop-launcher.applescript');
execFileSync('/usr/bin/osacompile', ['-o', target, source], { stdio: 'inherit' });
const plist = path.join(target, 'Contents/Info.plist');
const info = JSON.parse(execFileSync('/usr/bin/plutil', ['-convert', 'json', '-o', '-', plist], { encoding: 'utf8' }));
Object.assign(info, { CFBundleIdentifier: mode === 'accounts' ? 'com.teamhub.account-setup' : 'com.teamhub.local-launcher',
  CFBundleName: title, CFBundleDisplayName: title,
  CFBundleIconFile: 'applet', LSUIElement: true });
fs.writeFileSync(plist, JSON.stringify(info));
execFileSync('/usr/bin/plutil', ['-convert', 'xml1', plist]);
fs.copyFileSync(path.join(root, 'build/icon.icns'), path.join(target, 'Contents/Resources/applet.icns'));
execFileSync('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', target], { stdio: 'inherit' });
execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', target], { stdio: 'inherit' });
console.log('Finder 无终端启动入口已生成：' + target);
