#!/usr/bin/env node
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

function parseArgs(argv) {
  const args = {
    url: '',
    output: 'outputs/headless-browser-capture.mp4',
    fps: 2,
    warmup: 5,
    duration: 'auto',
    fallbackDuration: 60,
    maxDuration: 300,
    width: 1280,
    height: 720,
    chromePath: '',
    userDataDir: '',
    ffmpeg: '',
    keepFrames: false,
  };
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith('--')) {
      positional.push(item);
      continue;
    }
    const key = item.slice(2);
    const next = argv[i + 1];
    if (key === 'keep-frames') {
      args.keepFrames = true;
    } else if (key in args) {
      args[key] = next;
      i += 1;
    } else {
      throw new Error(`Unknown option: ${item}`);
    }
  }
  args.url = positional[0] || args.url;
  if (!args.url) throw new Error('Missing URL.');
  for (const key of ['fps', 'warmup', 'fallbackDuration', 'maxDuration', 'width', 'height']) {
    args[key] = Number(args[key]);
  }
  return args;
}

function findChrome(explicitPath) {
  const candidates = [
    explicitPath,
    process.env.CHROME_PATH,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error('Chrome/Edge executable not found. Pass --chromePath "C:\\path\\to\\chrome.exe".');
}

function findFfmpeg(explicitPath) {
  if (explicitPath && fs.existsSync(explicitPath)) return explicitPath;
  const skillDir = path.resolve(__dirname, '..');
  const ffmpegRoot = path.join(skillDir, 'tools', 'ffmpeg');
  const stack = [ffmpegRoot];
  while (stack.length) {
    const current = stack.pop();
    if (!current || !fs.existsSync(current)) continue;
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      if (entry.isFile() && entry.name.toLowerCase() === 'ffmpeg.exe') return full;
    }
  }
  return 'ffmpeg';
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForDevtoolsUrl(child, timeoutMs = 15000) {
  let buffer = '';
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Timed out waiting for Chrome DevTools endpoint.')), timeoutMs);
    const onData = (chunk) => {
      buffer += chunk.toString();
      const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        clearTimeout(timer);
        child.stderr.off('data', onData);
        resolve(match[1]);
      }
    };
    child.stderr.on('data', onData);
    child.once('exit', () => {
      clearTimeout(timer);
      reject(new Error('Chrome exited before DevTools endpoint became available.'));
    });
  });
}

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.sessions = new Map();
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', reject, { once: true });
    });
    this.ws.addEventListener('message', (event) => this.handleMessage(event.data));
  }

  handleMessage(raw) {
    const message = JSON.parse(raw);
    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message || JSON.stringify(message.error)));
      else resolve(message.result || {});
    }
  }

  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
  }

  close() {
    this.ws.close();
  }
}

async function launchChrome(args, tempRoot) {
  const chrome = findChrome(args.chromePath);
  const profile = args.userDataDir || path.join(tempRoot, 'chrome-profile');
  const chromeArgs = [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--autoplay-policy=no-user-gesture-required',
    `--window-size=${args.width},${args.height}`,
    `--user-data-dir=${profile}`,
    '--remote-debugging-port=0',
    'about:blank',
  ];
  const child = spawn(chrome, chromeArgs, { stdio: ['ignore', 'ignore', 'pipe'] });
  const browserWs = await waitForDevtoolsUrl(child);
  return { child, browserWs };
}

async function setupPage(client, url) {
  const { targetId } = await client.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await client.send('Target.attachToTarget', { targetId, flatten: true });
  await client.send('Page.enable', {}, sessionId);
  await client.send('Runtime.enable', {}, sessionId);
  await client.send('Page.navigate', { url }, sessionId);
  await delay(3000);
  return sessionId;
}

async function getVideoDuration(client, sessionId, fallbackDuration, maxDuration) {
  const expression = `(() => {
    const video = document.querySelector('video');
    if (!video) return { found: false, duration: 0, paused: true };
    video.muted = true;
    video.play().catch(() => {});
    return { found: true, duration: Number.isFinite(video.duration) ? video.duration : 0, paused: video.paused };
  })()`;
  const result = await client.send('Runtime.evaluate', { expression, returnByValue: true }, sessionId);
  const value = result?.result?.value || {};
  const duration = value.found && value.duration > 0 ? value.duration : fallbackDuration;
  return Math.max(1, Math.min(duration, maxDuration));
}

async function captureFrames(client, sessionId, frameDir, duration, fps, warmup) {
  await delay(Math.max(0, warmup) * 1000);
  const totalFrames = Math.max(1, Math.ceil(duration * fps));
  const intervalMs = 1000 / fps;
  for (let i = 0; i < totalFrames; i += 1) {
    const screenshot = await client.send('Page.captureScreenshot', { format: 'jpeg', quality: 85 }, sessionId);
    const name = `frame_${String(i + 1).padStart(6, '0')}.jpg`;
    await fsp.writeFile(path.join(frameDir, name), Buffer.from(screenshot.data, 'base64'));
    await delay(intervalMs);
  }
}

function runFfmpeg(ffmpeg, frameDir, fps, output) {
  return new Promise((resolve, reject) => {
    fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
    const child = spawn(ffmpeg, [
      '-y',
      '-hide_banner',
      '-framerate',
      String(fps),
      '-i',
      path.join(frameDir, 'frame_%06d.jpg'),
      '-c:v',
      'libx264',
      '-preset',
      'veryfast',
      '-pix_fmt',
      'yuv420p',
      path.resolve(output),
    ], { stdio: ['ignore', 'pipe', 'pipe'] });
    let errorText = '';
    child.stderr.on('data', (chunk) => { errorText += chunk.toString(); });
    child.on('exit', (code) => {
      if (code === 0) resolve();
      else reject(new Error(errorText || `ffmpeg exited with code ${code}`));
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), 'video-headless-record-'));
  const frameDir = path.join(tempRoot, 'frames');
  await fsp.mkdir(frameDir, { recursive: true });
  const ffmpeg = findFfmpeg(args.ffmpeg);
  let chrome;
  let client;
  try {
    chrome = await launchChrome(args, tempRoot);
    client = new CdpClient(chrome.browserWs);
    await client.open();
    const sessionId = await setupPage(client, args.url);
    const duration = args.duration === 'auto'
      ? await getVideoDuration(client, sessionId, args.fallbackDuration, args.maxDuration)
      : Math.min(Number(args.duration), args.maxDuration);
    console.error(`Headless recording duration: ${duration.toFixed(2)}s`);
    await captureFrames(client, sessionId, frameDir, duration, args.fps, args.warmup);
    await runFfmpeg(ffmpeg, frameDir, args.fps, args.output);
    console.log(path.resolve(args.output));
  } finally {
    if (client) client.close();
    if (chrome?.child) chrome.child.kill();
    if (!args.keepFrames) {
      await fsp.rm(tempRoot, { recursive: true, force: true }).catch(() => {});
    }
  }
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
