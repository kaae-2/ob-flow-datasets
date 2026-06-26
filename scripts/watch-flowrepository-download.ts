#!/usr/bin/env bun

import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';

type Options = {
  ids: string[];
  outputDir: string;
  intervalMs: number;
  stallMs: number;
  pidFile: string;
  downloaderLog: string;
  watchdogLog: string;
  downloaderArgs: string[];
};

type Status = {
  complete: boolean;
  total: number;
  completed: number;
  missingFilesJson: string[];
};

const DEFAULT_IDS = ['1124', '2045', '8408', '7222'];
const DEFAULT_PID_FILE = '/tmp/opencode/flowrepository-file-download.pid';
const DEFAULT_DOWNLOADER_LOG = '/tmp/opencode/flowrepository-file-download.log';
const DEFAULT_WATCHDOG_LOG = '/tmp/opencode/flowrepository-watchdog.log';

function usage() {
  console.log(`Usage: bun datasets/scripts/watch-flowrepository-download.ts [options]

Monitors the FlowRepository per-file downloader every 5 minutes by default.
If the downloader exits before all files are complete, or stalls, it is restarted
without --force so already-downloaded files are skipped.

Options:
  --ids 1124,2045               Dataset IDs (default: 1124,2045,8408,7222)
  --out DIR                     Output root (default: datasets/import)
  --interval-min N              Monitor interval (default: 5)
  --stall-min N                 Restart if no progress for this long (default: 30)
  --pid-file PATH               Downloader PID file (default: ${DEFAULT_PID_FILE})
  --downloader-log PATH         Downloader log (default: ${DEFAULT_DOWNLOADER_LOG})
  --watchdog-log PATH           Watchdog log (default: ${DEFAULT_WATCHDOG_LOG})
  --help                        Show this help
`);
}

function parseArgs(argv: string[]): Options {
  const options: Options = {
    ids: DEFAULT_IDS,
    outputDir: resolve('datasets/import'),
    intervalMs: 5 * 60 * 1000,
    stallMs: 30 * 60 * 1000,
    pidFile: DEFAULT_PID_FILE,
    downloaderLog: DEFAULT_DOWNLOADER_LOG,
    watchdogLog: DEFAULT_WATCHDOG_LOG,
    downloaderArgs: ['datasets/scripts/download-flowrepository.ts', '--no-launch'],
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
    } else if (arg === '--ids') {
      options.ids = next().split(',').map((id) => id.trim()).filter(Boolean);
      options.downloaderArgs.push('--ids', options.ids.join(','));
    } else if (arg === '--out') {
      options.outputDir = resolve(next());
      options.downloaderArgs.push('--out', options.outputDir);
    } else if (arg === '--interval-min') {
      options.intervalMs = Number(next()) * 60 * 1000;
    } else if (arg === '--stall-min') {
      options.stallMs = Number(next()) * 60 * 1000;
    } else if (arg === '--pid-file') {
      options.pidFile = next();
    } else if (arg === '--downloader-log') {
      options.downloaderLog = next();
    } else if (arg === '--watchdog-log') {
      options.watchdogLog = next();
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return options;
}

function log(options: Options, message: string) {
  const line = `[${new Date().toISOString()}] ${message}`;
  console.log(line);
  mkdirSync('/tmp/opencode', { recursive: true });
  writeFileSync(options.watchdogLog, `${line}\n`, { flag: 'a' });
}

function readPid(pidFile: string): number | undefined {
  if (!existsSync(pidFile)) return undefined;
  const raw = readFileSync(pidFile, 'utf8').trim();
  const pid = Number(raw);
  return Number.isFinite(pid) && pid > 0 ? pid : undefined;
}

function isRunning(pid: number | undefined): boolean {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (_error) {
    return false;
  }
}

function stop(pid: number | undefined) {
  if (!isRunning(pid)) return;
  process.kill(pid!, 'SIGTERM');
}

function startDownloader(options: Options): number {
  mkdirSync('/tmp/opencode', { recursive: true });
  const child = spawn('bun', options.downloaderArgs, {
    detached: true,
    stdio: ['ignore', 'ignore', 'ignore'],
    env: process.env,
  });
  child.unref();
  writeFileSync(options.pidFile, `${child.pid}\n`);
  log(options, `started downloader pid=${child.pid} args=${options.downloaderArgs.join(' ')}`);
  return child.pid ?? 0;
}

function completionStatus(options: Options): Status {
  let total = 0;
  let completed = 0;
  const missingFilesJson: string[] = [];

  for (const id of options.ids) {
    const datasetDir = join(options.outputDir, id);
    const filesPath = join(datasetDir, 'files.json');
    const manifestPath = join(datasetDir, 'manifest.jsonl');

    if (!existsSync(filesPath)) {
      missingFilesJson.push(id);
      continue;
    }

    const files = JSON.parse(readFileSync(filesPath, 'utf8')) as Array<{ fileId: string }>;
    const expected = new Set(files.map((file) => file.fileId));
    total += expected.size;

    if (!existsSync(manifestPath)) continue;
    for (const line of readFileSync(manifestPath, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      const record = JSON.parse(line) as { fileId?: string; status?: string; path?: string };
      if (!record.fileId || record.status !== 'complete' || !expected.has(record.fileId)) continue;
      if (record.path && existsSync(record.path) && statSync(record.path).size > 0) expected.delete(record.fileId);
    }

    completed += files.length - expected.size;
  }

  return {
    complete: missingFilesJson.length === 0 && total > 0 && completed === total,
    total,
    completed,
    missingFilesJson,
  };
}

function progressSignature(options: Options): string {
  const parts: string[] = [];
  for (const id of options.ids) {
    const datasetDir = join(options.outputDir, id);
    const manifestPath = join(datasetDir, 'manifest.jsonl');
    if (existsSync(manifestPath)) {
      const stat = statSync(manifestPath);
      parts.push(`${id}:manifest:${stat.size}:${Math.floor(stat.mtimeMs)}`);
    }
    if (existsSync(datasetDir)) {
      for (const name of readdirSync(datasetDir)) {
        if (!name.endsWith('.crdownload')) continue;
        const stat = statSync(join(datasetDir, name));
        parts.push(`${id}:partial:${name}:${stat.size}:${Math.floor(stat.mtimeMs)}`);
      }
    }
  }
  return parts.join('|');
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  let lastSignature = progressSignature(options);
  let lastProgressAt = Date.now();

  log(options, `watchdog started interval=${Math.round(options.intervalMs / 1000)}s stall=${Math.round(options.stallMs / 1000)}s`);

  while (true) {
    const status = completionStatus(options);
    const pid = readPid(options.pidFile);
    const running = isRunning(pid);
    const signature = progressSignature(options);

    if (signature !== lastSignature) {
      lastSignature = signature;
      lastProgressAt = Date.now();
    }

    log(options, `status completed=${status.completed}/${status.total} running=${running ? pid : 'no'} missing_files_json=${status.missingFilesJson.join(',') || 'none'}`);

    if (status.complete) {
      log(options, 'all files are confirmed downloaded; watchdog exiting');
      return;
    }

    if (!running) {
      startDownloader(options);
      lastSignature = progressSignature(options);
      lastProgressAt = Date.now();
    } else if (Date.now() - lastProgressAt > options.stallMs) {
      log(options, `no manifest or partial-file progress for ${Math.round(options.stallMs / 60000)} minutes; restarting downloader pid=${pid}`);
      stop(pid);
      await sleep(15_000);
      startDownloader(options);
      lastSignature = progressSignature(options);
      lastProgressAt = Date.now();
    }

    await sleep(options.intervalMs);
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
