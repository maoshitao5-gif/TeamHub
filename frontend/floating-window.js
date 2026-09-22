(function () {
  'use strict';

  const statusEl = document.getElementById('debug-status');
  const btn = document.getElementById('floatingBtn');

  function setStatus(msg) {
    console.log('[Floating]', msg);
    if (statusEl) statusEl.textContent = msg;
  }

  setStatus('START');

  // 检查 Electron API
  if (!window.electron) {
    setStatus('NO ELECTRON API');
    return;
  }

  if (!window.electron.dropFilesToFloatingWindow) {
    setStatus('NO DROP API');
    return;
  }

  if (!window.electron.send) {
    setStatus('NO SEND API');
    return;
  }

  setStatus('API READY');

  let dragCounter = 0;
  let isMoving = false;
  let lastScreenX = 0, lastScreenY = 0;

  // ============ 文件拖放处理 ============
  document.addEventListener('dragenter', function (e) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter++;
    setStatus('DRAG ' + dragCounter);
    if (dragCounter === 1) {
      btn.classList.remove('idle');
      btn.classList.add('drag-over');
    }
  }, true);

  document.addEventListener('dragover', function (e) {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) {
      e.dataTransfer.dropEffect = 'copy';
    }
  }, true);

  document.addEventListener('dragleave', function (e) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      btn.classList.remove('drag-over');
      btn.classList.add('idle');
      setStatus('IDLE');
    }
  }, true);

  document.addEventListener('drop', function (e) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter = 0;
    btn.classList.remove('drag-over');
    setStatus('DROP RECEIVED');

    if (!e.dataTransfer) {
      setStatus('NO DATA TRANSFER');
      btn.classList.add('idle');
      return;
    }

    var files = Array.from(e.dataTransfer.files || []);
    if (files.length === 0) {
      setStatus('NO FILES');
      btn.classList.add('idle');
      return;
    }

    var filePaths = files.map(function (f) { return f.path; }).filter(function (p) { return p && p.length > 0; });

    if (filePaths.length === 0) {
      setStatus('NO PATHS');
      btn.classList.add('idle');
      return;
    }

    setStatus('SENDING ' + filePaths.length);

    window.electron.dropFilesToFloatingWindow(filePaths).then(function () {
      setStatus('SENT OK');
      btn.classList.add('success');
      setTimeout(function () {
        btn.classList.remove('success');
        btn.classList.add('idle');
        setStatus('READY');
      }, 1000);
    }).catch(function (err) {
      setStatus('ERR: ' + (err.message || 'FAILED'));
      btn.classList.add('idle');
    });
  }, true);

  // ============ 窗口拖动处理 ============
  btn.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return;
    isMoving = true;
    lastScreenX = e.screenX;
    lastScreenY = e.screenY;
    btn.classList.add('moving');
    e.preventDefault();
  });

  document.addEventListener('mousemove', function (e) {
    if (!isMoving) return;
    var dx = e.screenX - lastScreenX;
    var dy = e.screenY - lastScreenY;
    if (dx !== 0 || dy !== 0) {
      window.electron.send('floating-window-move', { deltaX: dx, deltaY: dy });
      lastScreenX = e.screenX;
      lastScreenY = e.screenY;
    }
  });

  document.addEventListener('mouseup', function () {
    if (!isMoving) return;
    isMoving = false;
    btn.classList.remove('moving');
  });

  window.addEventListener('blur', function () {
    if (isMoving) {
      isMoving = false;
      btn.classList.remove('moving');
    }
  });

  setStatus('READY');
}());
