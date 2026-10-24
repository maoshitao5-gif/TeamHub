const { createHmac } = require('crypto');

const CHALLENGE_PATH = '/__teamhub_desktop_challenge';

function validateFrontendURL(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('桌面开发界面地址无效，请使用 npm run electron:dev');
  }
  return url.href;
}

function challengeResponse(secret, challenge) {
  return createHmac('sha256', secret).update(challenge).digest('hex');
}

function createChallengeMiddleware(secret) {
  if (!/^[a-f0-9]{64}$/.test(secret)) throw new Error('Invalid desktop development secret');
  return (request, response, next) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname !== CHALLENGE_PATH) return next();
    const challenge = url.searchParams.get('challenge') || '';
    if (request.method !== 'GET' || !/^[a-f0-9]{64}$/.test(challenge)) {
      response.statusCode = 400;
      response.end();
      return;
    }
    response.setHeader('Content-Type', 'text/plain; charset=utf-8');
    response.setHeader('Cache-Control', 'no-store');
    response.end(challengeResponse(secret, challenge));
  };
}

module.exports = { CHALLENGE_PATH, validateFrontendURL, challengeResponse, createChallengeMiddleware };
