#!/usr/bin/env bun

import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync, appendFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';

type Experiment = {
  id: string;
  selectionUrl: string;
};

type DownloadFile = {
  experimentId: string;
  fileId: string;
  name: string;
  url: string;
  displaySize: string;
  md5?: string;
  kind?: string;
};

type ManifestRecord = {
  experimentId: string;
  fileId?: string;
  sourceUrl: string;
  filename?: string;
  expectedName?: string;
  path?: string;
  bytes?: number;
  displaySize?: string;
  md5?: string;
  kind?: string;
  status: 'complete' | 'failed' | 'skipped';
  attempt?: number;
  round?: number;
  startedAt: string;
  finishedAt: string;
  error?: string;
};

type CdpEvent = {
  method: string;
  params?: Record<string, unknown>;
  sessionId?: string;
};

type PendingCommand = {
  resolve: (value: any) => void;
  reject: (error: Error) => void;
  timeout: Timer;
};

const DEFAULT_EXPERIMENTS: Experiment[] = ['1124', '2045', '8408', '7222'].map((id) => ({
  id,
  selectionUrl: `http://flowrepository.org/experiments/${id}/download_ziped_files`,
}));

const DEFAULT_BROWSER_URL = 'http://127.0.0.1:9222';
const DEFAULT_PROFILE_DIR = '/tmp/opencode/flowrepo-browser';

class CdpClient {
  private nextId = 1;
  private pending = new Map<number, PendingCommand>();
  private listeners = new Set<(event: CdpEvent) => void>();

  constructor(private ws: WebSocket) {
    ws.addEventListener('message', (message) => {
      const data = typeof message.data === 'string' ? message.data : Buffer.from(message.data as ArrayBuffer).toString('utf8');
      const parsed = JSON.parse(data);

      if (parsed.id) {
        const pending = this.pending.get(parsed.id);
        if (!pending) return;

        clearTimeout(pending.timeout);
        this.pending.delete(parsed.id);
        if (parsed.error) {
          pending.reject(new Error(`${parsed.error.message}${parsed.error.data ? `: ${parsed.error.data}` : ''}`));
        } else {
          pending.resolve(parsed.result ?? {});
        }
        return;
      }

      if (parsed.method) {
        const event: CdpEvent = {
          method: parsed.method,
          params: parsed.params,
          sessionId: parsed.sessionId,
        };
        for (const listener of this.listeners) listener(event);
      }
    });

    ws.addEventListener('close', () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timeout);
        pending.reject(new Error('CDP websocket closed'));
      }
      this.pending.clear();
    });
  }

  send(method: string, params: Record<string, unknown> = {}, sessionId?: string, timeoutMs = 60_000): Promise<any> {
    const id = this.nextId++;
    const payload: Record<string, unknown> = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Timed out running CDP command ${method}`));
      }, timeoutMs);

      this.pending.set(id, { resolve, reject, timeout });
      this.ws.send(JSON.stringify(payload));
    });
  }

  waitForEvent(
    predicate: (event: CdpEvent) => boolean,
    timeoutMs: number,
    description: string,
  ): Promise<CdpEvent> {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.listeners.delete(listener);
        reject(new Error(`Timed out waiting for ${description}`));
      }, timeoutMs);

      const listener = (event: CdpEvent) => {
        if (!predicate(event)) return;
        clearTimeout(timeout);
        this.listeners.delete(listener);
        resolve(event);
      };

      this.listeners.add(listener);
    });
  }

  close() {
    this.ws.close();
  }
}

type Options = {
  browserUrl: string;
  outputDir: string;
  profileDir: string;
  experiments: Experiment[];
  downloadStartTimeoutMs: number;
  downloadTimeoutMs: number;
  loginTimeoutMs: number;
  maxAttempts: number;
  retryDelayMs: number;
  force: boolean;
  dryRun: boolean;
  launchBrowser: boolean;
};

function usage() {
  console.log(`Usage: bun datasets/scripts/download-flowrepository.ts [options]

Uses each experiment's /download_ziped_files page to discover files, then downloads
one file at a time through the logged-in browser into:
  datasets/import/<experiment-id>/

Options:
  --browser-url URL             CDP endpoint to use (default: ${DEFAULT_BROWSER_URL})
  --out DIR                     Output root (default: datasets/import)
  --profile-dir DIR             Chromium profile if auto-launching (default: ${DEFAULT_PROFILE_DIR})
  --ids 1124,2045               Experiment IDs to download (default: 1124,2045,8408,7222)
  --download-start-timeout-min N Time to wait for each browser download to begin (default: 10)
  --download-timeout-min N      Per-file download timeout (default: 60)
  --login-timeout-min N         Time to wait for manual login (default: 30)
  --max-attempts N              Attempts per file; 0 means retry forever (default: 0)
  --retry-delay-min N           Delay before retrying failed files (default: 5)
  --force                       Download even if manifest has a completed file
  --dry-run                     Discover files without starting downloads
  --no-launch                   Do not launch Chromium if CDP is unavailable
  --help                        Show this help

If FlowRepository asks for login, use the opened Chromium window; the script waits.
`);
}

function parseArgs(argv: string[]): Options {
  const options: Options = {
    browserUrl: DEFAULT_BROWSER_URL,
    outputDir: resolve('datasets/import'),
    profileDir: DEFAULT_PROFILE_DIR,
    experiments: DEFAULT_EXPERIMENTS,
    downloadStartTimeoutMs: 10 * 60 * 1000,
    downloadTimeoutMs: 60 * 60 * 1000,
    loginTimeoutMs: 30 * 60 * 1000,
    maxAttempts: 0,
    retryDelayMs: 5 * 60 * 1000,
    force: false,
    dryRun: false,
    launchBrowser: true,
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => {
      const value = argv[++i];
      if (!value) throw new Error(`Missing value for ${arg}`);
      return value;
    };

    if (arg === '--help' || arg === '-h') {
      usage();
      process.exit(0);
    } else if (arg === '--browser-url') {
      options.browserUrl = next();
    } else if (arg === '--out') {
      options.outputDir = resolve(next());
    } else if (arg === '--profile-dir') {
      options.profileDir = next();
    } else if (arg === '--ids') {
      options.experiments = next().split(',').map((id) => id.trim()).filter(Boolean).map((id) => ({
        id,
        selectionUrl: `http://flowrepository.org/experiments/${id}/download_ziped_files`,
      }));
    } else if (arg === '--download-timeout-min') {
      options.downloadTimeoutMs = Number(next()) * 60 * 1000;
    } else if (arg === '--download-start-timeout-min') {
      options.downloadStartTimeoutMs = Number(next()) * 60 * 1000;
    } else if (arg === '--login-timeout-min') {
      options.loginTimeoutMs = Number(next()) * 60 * 1000;
    } else if (arg === '--max-attempts') {
      options.maxAttempts = Number(next());
    } else if (arg === '--retry-delay-min') {
      options.retryDelayMs = Number(next()) * 60 * 1000;
    } else if (arg === '--force') {
      options.force = true;
    } else if (arg === '--dry-run') {
      options.dryRun = true;
    } else if (arg === '--no-launch') {
      options.launchBrowser = false;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return options;
}

async function connect(browserUrl: string): Promise<CdpClient> {
  const versionResponse = await fetch(`${browserUrl.replace(/\/$/, '')}/json/version`);
  if (!versionResponse.ok) throw new Error(`Unable to read ${browserUrl}/json/version`);

  const version = await versionResponse.json() as { webSocketDebuggerUrl?: string };
  if (!version.webSocketDebuggerUrl) throw new Error('CDP endpoint did not provide webSocketDebuggerUrl');

  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Timed out opening CDP websocket')), 10_000);
    ws.addEventListener('open', () => {
      clearTimeout(timeout);
      resolve();
    });
    ws.addEventListener('error', () => {
      clearTimeout(timeout);
      reject(new Error('Failed to open CDP websocket'));
    });
  });

  return new CdpClient(ws);
}

async function connectOrLaunch(options: Options): Promise<CdpClient> {
  try {
    return await connect(options.browserUrl);
  } catch (error) {
    if (!options.launchBrowser) throw error;
  }

  const chromium = findChromium();
  if (!chromium) {
    throw new Error('No Chromium binary found. Start Chrome/Chromium with --remote-debugging-port=9222 and rerun.');
  }

  mkdirSync(options.profileDir, { recursive: true });
  console.log(`Starting Chromium with profile ${options.profileDir}`);
  const child = spawn(chromium, [
    '--remote-debugging-port=9222',
    `--user-data-dir=${options.profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank',
  ], {
    detached: true,
    stdio: 'ignore',
  });
  child.unref();

  const deadline = Date.now() + 30_000;
  let lastError: unknown;
  while (Date.now() < deadline) {
    try {
      return await connect(options.browserUrl);
    } catch (error) {
      lastError = error;
      await sleep(500);
    }
  }

  throw new Error(`Chromium started but CDP did not become available: ${String(lastError)}`);
}

function findChromium(): string | undefined {
  for (const path of ['/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable']) {
    if (existsSync(path)) return path;
  }
  return undefined;
}

async function getPage(cdp: CdpClient): Promise<string> {
  const { targetInfos } = await cdp.send('Target.getTargets');
  const pages = (targetInfos as Array<{ targetId: string; type: string; title: string; url: string }>)
    .filter((target) => target.type === 'page')
    .sort((a, b) => scoreTarget(b) - scoreTarget(a));

  for (const page of pages) {
    try {
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: page.targetId, flatten: true }, undefined, 10_000);
      await cdp.send('Page.enable', {}, sessionId, 10_000);
      await cdp.send('Runtime.enable', {}, sessionId, 10_000);
      await cdp.send('Page.stopLoading', {}, sessionId, 5_000).catch(() => undefined);
      await evaluate<string>(cdp, sessionId, 'document.title', 10_000);
      return sessionId;
    } catch (_error) {
      // Try the next tab; old FlowRepository download tabs can become unresponsive.
    }
  }

  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
  await cdp.send('Page.enable', {}, sessionId);
  await cdp.send('Runtime.enable', {}, sessionId);
  return sessionId;
}

function scoreTarget(target: { title: string; url: string }): number {
  if (target.url.includes('flowrepository.org/experiments/') && !target.url.includes('download_ziped_files') && target.title.startsWith('FlowRepository -')) return 100;
  if (target.url.includes('flowrepository.org') && !target.url.includes('/login')) return 80;
  if (target.url === 'about:blank') return 20;
  return 10;
}

async function navigate(cdp: CdpClient, sessionId: string, url: string, timeoutMs = 120_000) {
  const load = cdp.waitForEvent(
    (event) => event.sessionId === sessionId && event.method === 'Page.loadEventFired',
    Math.min(timeoutMs, 15_000),
    `page load for ${url}`,
  ).catch(() => undefined);

  await cdp.send('Page.navigate', { url }, sessionId, 30_000);
  await load;
  await waitForExpression(cdp, sessionId, 'Boolean(document.body)', Math.min(timeoutMs, 30_000)).catch(() => undefined);
}

async function evaluate<T>(cdp: CdpClient, sessionId: string, expression: string, timeoutMs = 60_000): Promise<T> {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  }, sessionId, timeoutMs);

  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text ?? 'Runtime.evaluate failed');
  }

  return result.result?.value as T;
}

async function waitForExpression(cdp: CdpClient, sessionId: string, expression: string, timeoutMs: number) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate<boolean>(cdp, sessionId, expression, 10_000)) return;
    await sleep(1000);
  }
  throw new Error(`Timed out waiting for expression: ${expression}`);
}

async function ensureLoggedIn(cdp: CdpClient, sessionId: string, loginTimeoutMs: number) {
  if (await isLoggedIn(cdp, sessionId).catch(() => false)) return;

  await navigate(cdp, sessionId, 'http://flowrepository.org/experiments');
  if (await isLoggedIn(cdp, sessionId).catch(() => false)) return;

  console.log('FlowRepository login required. Use the Chromium window to log in; waiting...');
  const deadline = Date.now() + loginTimeoutMs;
  while (Date.now() < deadline) {
    if (await isLoggedIn(cdp, sessionId).catch(() => false)) return;
    await sleep(2000);
  }

  throw new Error('Timed out waiting for manual login');
}

async function isLoggedIn(cdp: CdpClient, sessionId: string): Promise<boolean> {
  return evaluate<boolean>(cdp, sessionId, `Boolean(document.body && /Logout|Welcome,/.test(document.body.innerText))`, 10_000);
}

async function discoverFiles(cdp: CdpClient, sessionId: string, experiment: Experiment): Promise<DownloadFile[]> {
  const files = await evaluate<DownloadFile[]>(cdp, sessionId, `
    (async () => {
      const response = await fetch(${JSON.stringify(experiment.selectionUrl)}, { credentials: 'include' });
      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const text = doc.body ? doc.body.innerText : '';
      if (/You must be logged in|Welcome to FlowRepository/.test(text) && !/Logout|Welcome,/.test(text)) {
        throw new Error('FlowRepository returned login page for ${experiment.id}');
      }

      return Array.from(doc.querySelectorAll('#download-data-table tbody tr')).map((row) => {
        const checkbox = row.querySelector('input.downcheck');
        const link = row.querySelector('a[href$="/download"]');
        const cells = Array.from(row.querySelectorAll('td'));
        const title = row.getAttribute('title') || '';
        const md5 = title.match(/md5sum: ([0-9a-f]+)/i)?.[1] || '';

        if (!checkbox || !link) return null;

        return {
          experimentId: ${JSON.stringify(experiment.id)},
          fileId: checkbox.value,
          name: (link.textContent || '').trim(),
          url: new URL(link.getAttribute('href'), response.url).href,
          displaySize: (cells[2]?.textContent || '').trim().replace(/\s+/g, ' '),
          md5,
          kind: (cells[4]?.textContent || '').trim().replace(/\s+/g, ' '),
        };
      }).filter(Boolean);
    })()
  `, 10 * 60 * 1000);

  if (!files.length) throw new Error(`[${experiment.id}] No downloadable files found on ${experiment.selectionUrl}`);
  return files;
}

async function allowDownloads(cdp: CdpClient, downloadPath: string, sessionId: string) {
  mkdirSync(downloadPath, { recursive: true });
  try {
    await cdp.send('Browser.setDownloadBehavior', {
      behavior: 'allow',
      downloadPath,
      eventsEnabled: true,
    });
  } catch (_error) {
    await cdp.send('Page.setDownloadBehavior', {
      behavior: 'allow',
      downloadPath,
    }, sessionId);
  }
}

async function downloadFile(cdp: CdpClient, sessionId: string, file: DownloadFile, datasetDir: string, options: Options, attempt: number, round: number): Promise<ManifestRecord> {
  const startedAt = new Date();
  const completed = latestCompletedRecordForFile(join(datasetDir, 'manifest.jsonl'), file.fileId);
  if (!options.force && completed?.path && existsSync(completed.path) && statSync(completed.path).size > 0) {
    return {
      ...completed,
      status: 'skipped',
      attempt,
      round,
      startedAt: startedAt.toISOString(),
      finishedAt: new Date().toISOString(),
    };
  }

  await allowDownloads(cdp, datasetDir, sessionId);
  const downloadWillBegin = cdp.waitForEvent(
    (event) => event.method === 'Browser.downloadWillBegin',
    options.downloadStartTimeoutMs,
    `download start for ${file.experimentId}/${file.name}`,
  );

  console.log(`[${file.experimentId}] Downloading ${file.fileId}: ${file.name} (${file.displaySize || 'unknown size'})`);
  const navigatePromise = cdp.send('Page.navigate', { url: file.url }, sessionId, 30_000).catch(() => undefined);
  const beginEvent = await downloadWillBegin;
  await navigatePromise;

  const guid = String(beginEvent.params?.guid ?? '');
  const suggestedFilename = String(beginEvent.params?.suggestedFilename ?? file.name);

  await cdp.waitForEvent(
    (event) => event.method === 'Browser.downloadProgress'
      && event.params?.guid === guid
      && ['completed', 'canceled'].includes(String(event.params?.state)),
    options.downloadTimeoutMs,
    `download completion for ${file.experimentId}/${file.name}`,
  ).then((event) => {
    if (event.params?.state === 'canceled') throw new Error(`[${file.experimentId}] Browser canceled ${file.name}`);
  });

  const path = await findDownloadedFile(datasetDir, suggestedFilename, startedAt.getTime());
  const bytes = statSync(path).size;
  const record: ManifestRecord = {
    experimentId: file.experimentId,
    fileId: file.fileId,
    sourceUrl: file.url,
    filename: suggestedFilename,
    expectedName: file.name,
    path,
    bytes,
    displaySize: file.displaySize,
    md5: file.md5,
    kind: file.kind,
    status: 'complete',
    attempt,
    round,
    startedAt: startedAt.toISOString(),
    finishedAt: new Date().toISOString(),
  };
  writeManifest(datasetDir, record);
  console.log(`[${file.experimentId}] Complete ${file.fileId}: ${path} (${bytes} bytes)`);
  return record;
}

async function downloadExperimentFiles(cdp: CdpClient, sessionId: string, experiment: Experiment, options: Options): Promise<ManifestRecord[]> {
  const datasetDir = join(options.outputDir, experiment.id);
  mkdirSync(datasetDir, { recursive: true });

  await ensureLoggedIn(cdp, sessionId, options.loginTimeoutMs);
  const files = await discoverFiles(cdp, sessionId, experiment);
  writeFileSync(join(datasetDir, 'files.json'), `${JSON.stringify(files, null, 2)}\n`);
  console.log(`[${experiment.id}] Found ${files.length} files on ${experiment.selectionUrl}`);

  if (options.dryRun) {
    for (const file of files.slice(0, 5)) console.log(`[${experiment.id}] ${file.fileId}: ${file.name} ${file.displaySize}`);
    if (files.length > 5) console.log(`[${experiment.id}] ... ${files.length - 5} more`);
    return [];
  }

  const records: ManifestRecord[] = [];
  const attempts = new Map(files.map((file) => [file.fileId, 0]));
  let pending = files.filter((file) => options.force || !isFileCompleted(datasetDir, file.fileId));
  let round = 1;

  while (pending.length > 0) {
    const failedThisRound: DownloadFile[] = [];
    console.log(`\n[${experiment.id}] Round ${round}: ${pending.length} pending files`);

    for (const file of pending) {
      const attempt = (attempts.get(file.fileId) ?? 0) + 1;
      attempts.set(file.fileId, attempt);

      try {
        const record = await downloadFile(cdp, sessionId, file, datasetDir, options, attempt, round);
        records.push(record);
      } catch (error) {
        const record: ManifestRecord = {
          experimentId: experiment.id,
          fileId: file.fileId,
          sourceUrl: file.url,
          expectedName: file.name,
          displaySize: file.displaySize,
          md5: file.md5,
          kind: file.kind,
          status: 'failed',
          attempt,
          round,
          startedAt: new Date().toISOString(),
          finishedAt: new Date().toISOString(),
          error: error instanceof Error ? error.message : String(error),
        };
        writeManifest(datasetDir, record);
        records.push(record);
        console.error(`[${experiment.id}] Failed ${file.fileId} attempt ${attempt}: ${record.error}`);

        if (options.maxAttempts > 0 && attempt >= options.maxAttempts) {
          console.error(`[${experiment.id}] ${file.fileId} reached max attempts (${options.maxAttempts}); leaving for manual follow-up.`);
        } else {
          failedThisRound.push(file);
        }
      }
    }

    pending = failedThisRound.filter((file) => options.force || !isFileCompleted(datasetDir, file.fileId));
    if (pending.length > 0) {
      console.log(`[${experiment.id}] Retrying ${pending.length} failed files after ${Math.round(options.retryDelayMs / 1000)} seconds`);
      await sleep(options.retryDelayMs);
    }
    round += 1;
  }

  return records;
}

function isFileCompleted(datasetDir: string, fileId: string): boolean {
  const completed = latestCompletedRecordForFile(join(datasetDir, 'manifest.jsonl'), fileId);
  return Boolean(completed?.path && existsSync(completed.path) && statSync(completed.path).size > 0);
}

function latestCompletedRecordForFile(manifestPath: string, fileId: string): ManifestRecord | undefined {
  if (!existsSync(manifestPath)) return undefined;

  const records = readFileSync(manifestPath, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as ManifestRecord)
    .filter((record) => record.fileId === fileId && record.status === 'complete');

  return records.at(-1);
}

function writeManifest(datasetDir: string, record: ManifestRecord) {
  appendFileSync(join(datasetDir, 'manifest.jsonl'), `${JSON.stringify(record)}\n`);
  writeFileSync(join(datasetDir, 'manifest-latest.json'), `${JSON.stringify(record, null, 2)}\n`);
}

async function findDownloadedFile(datasetDir: string, suggestedFilename: string, startedAtMs: number): Promise<string> {
  const exactPath = join(datasetDir, suggestedFilename);
  if (existsSync(exactPath) && statSync(exactPath).size > 0) return exactPath;

  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const candidates = readdirSync(datasetDir)
      .filter((name) => !name.endsWith('.crdownload') && !name.startsWith('manifest') && name !== 'files.json')
      .map((name) => ({ name, path: join(datasetDir, name), stat: statSync(join(datasetDir, name)) }))
      .filter((entry) => entry.stat.isFile() && entry.stat.size > 0 && entry.stat.mtimeMs >= startedAtMs - 5000)
      .sort((a, b) => b.stat.mtimeMs - a.stat.mtimeMs);

    if (candidates[0]) return candidates[0].path;
    await sleep(500);
  }

  throw new Error(`Download completed, but no downloaded file was found in ${datasetDir}`);
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  mkdirSync(options.outputDir, { recursive: true });

  const cdp = await connectOrLaunch(options);
  const sessionId = await getPage(cdp);
  const records: ManifestRecord[] = [];

  try {
    for (const experiment of options.experiments) {
      const experimentRecords = await downloadExperimentFiles(cdp, sessionId, experiment, options);
      records.push(...experimentRecords);
    }
  } finally {
    cdp.close();
  }

  const completed = records.filter((record) => record.status === 'complete').length;
  const failed = records.filter((record) => record.status === 'failed').length;
  const skipped = records.filter((record) => record.status === 'skipped').length;
  console.log(`\nSummary: ${completed} complete, ${skipped} skipped, ${failed} failed records`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
