const { spawn } = require('child_process');
const http = require('http');

function checkPort(port) {
  return new Promise((resolve) => {
    const req = http.get(`http://localhost:${port}`, { timeout: 1000 }, (res) => {
      resolve(res.statusCode === 200 || res.statusCode === 304);
      res.destroy();
    });
    
    req.on('error', () => {
      resolve(false);
    });
    
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForFrontend(maxAttempts = 60) {
  const ports = [5173, 5174, 5175, 5176, 5177];
  
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    for (const port of ports) {
      const isAvailable = await checkPort(port);
      if (isAvailable) {
        return port;
      }
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  
  return 5173; // Default port, proceed anyway
}

async function startElectron() {
  try {
    await waitForFrontend();
    
    const electronProcess = spawn('electron', ['.'], {
      stdio: 'inherit',
      shell: true,
      cwd: __dirname
    });
    
    electronProcess.on('error', (error) => {
      console.error('Failed to start Electron:', error);
      process.exit(1);
    });
    
    electronProcess.on('exit', (code) => {
      process.exit(code || 0);
    });
  } catch (error) {
    console.error('Error starting Electron:', error);
    process.exit(1);
  }
}

startElectron();
