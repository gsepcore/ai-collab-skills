'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');
const vscode = require('vscode');

const registryDir = path.join(os.homedir(), '.ai-collab', 'ide-bridges');
let registryPath = '';
let server;

function real(value) {
  try {
    return fs.realpathSync(value);
  } catch (_error) {
    return path.resolve(value);
  }
}

function workspacePaths() {
  return (vscode.workspace.workspaceFolders || []).map((folder) => real(folder.uri.fsPath));
}

function atomicWrite(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temp, file);
}

function run(command, args) {
  return new Promise((resolve) => {
    execFile(command, args, { timeout: 5000, maxBuffer: 2 * 1024 * 1024 }, (error, stdout) => {
      resolve(error ? '' : String(stdout || ''));
    });
  });
}

function parseProcesses(text) {
  const rows = [];
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.+)$/.exec(line);
    if (match) {
      rows.push({ pid: Number(match[1]), ppid: Number(match[2]), tty: match[3], command: match[4] });
    }
  }
  return rows;
}

function descendants(rows, rootPid) {
  const found = new Set([rootPid]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const row of rows) {
      if (found.has(row.ppid) && !found.has(row.pid)) {
        found.add(row.pid);
        changed = true;
      }
    }
  }
  return rows.filter((row) => found.has(row.pid) && row.pid !== rootPid);
}

function commandMatches(target, command) {
  if (target === 'claude' || target === 'claude-code') {
    return /(^|\/)claude(?:\s|$)/.test(command);
  }
  if (target === 'opencode') {
    return /(^|\/)opencode(?:\s|$)/.test(command);
  }
  if (target === 'hermes') {
    return /(^|\/)hermes(?:\s|$)/.test(command);
  }
  return false;
}

async function cwdForPid(pid) {
  if (process.platform === 'darwin') {
    const output = await run('/usr/sbin/lsof', ['-a', '-p', String(pid), '-d', 'cwd', '-Fn']);
    const line = output.split(/\r?\n/).find((item) => item.startsWith('n'));
    return line ? real(line.slice(1)) : '';
  }
  if (process.platform === 'linux') {
    try {
      return real(fs.readlinkSync(`/proc/${pid}/cwd`));
    } catch (_error) {
      return '';
    }
  }
  return '';
}

async function matchingTerminals(target, projectPath) {
  const processText = await run('/bin/ps', ['-axo', 'pid=,ppid=,tty=,command=']);
  const rows = parseProcesses(processText);
  const expected = real(projectPath);
  const matches = [];
  for (const terminal of vscode.window.terminals) {
    const shellPid = await terminal.processId;
    if (!shellPid) continue;
    for (const child of descendants(rows, shellPid)) {
      if (!commandMatches(target, child.command)) continue;
      const cwd = await cwdForPid(child.pid);
      if (cwd === expected) {
        matches.push({ terminal, terminalName: terminal.name, shellPid, agentPid: child.pid, tty: child.tty, cwd });
      }
    }
  }
  return matches;
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > 1024 * 1024) {
        reject(new Error('request too large'));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    request.on('error', reject);
  });
}

function sendJson(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
}

async function activate(context) {
  const token = crypto.randomBytes(32).toString('hex');
  server = http.createServer(async (request, response) => {
    if (request.headers.authorization !== `Bearer ${token}`) {
      sendJson(response, 401, { status: 'failed', message: 'unauthorized' });
      return;
    }
    const requestUrl = new URL(request.url, 'http://127.0.0.1');
    if (request.method === 'GET' && requestUrl.pathname === '/health') {
      sendJson(response, 200, { status: 'ok', project_paths: workspacePaths(), terminals: vscode.window.terminals.length });
      return;
    }
    if (request.method === 'GET' && requestUrl.pathname === '/terminals') {
      const projectPath = String(requestUrl.searchParams.get('project_path') || '');
      if (!projectPath || !workspacePaths().includes(real(projectPath))) {
        sendJson(response, 409, { status: 'failed', message: 'bridge workspace does not match requested project' });
        return;
      }
      const terminals = [];
      for (const target of ['claude-code', 'opencode', 'hermes']) {
        const matches = await matchingTerminals(target, projectPath);
        for (const item of matches) {
          terminals.push({
            agent: target,
            terminal_name: item.terminalName,
            shell_pid: item.shellPid,
            agent_pid: item.agentPid,
            tty: item.tty,
            project_path: item.cwd,
          });
        }
      }
      sendJson(response, 200, { status: 'success', project_path: real(projectPath), terminals });
      return;
    }
    if (request.method !== 'POST' || !['/terminal/send', '/terminal/show'].includes(requestUrl.pathname)) {
      sendJson(response, 404, { status: 'failed', message: 'not found' });
      return;
    }
    try {
      const payload = JSON.parse(await readBody(request));
      const projectPath = String(payload.project_path || '');
      const target = String(payload.target_slug || '');
      const prompt = String(payload.prompt || '');
      if (!projectPath || !target || (requestUrl.pathname === '/terminal/send' && !prompt)) {
        sendJson(response, 400, { status: 'failed', message: 'project_path, target_slug, and prompt are required for send' });
        return;
      }
      if (!workspacePaths().includes(real(projectPath))) {
        sendJson(response, 409, { status: 'failed', message: 'bridge workspace does not match requested project' });
        return;
      }
      const matches = await matchingTerminals(target, projectPath);
      if (matches.length !== 1) {
        sendJson(response, 409, {
          status: 'failed',
          message: matches.length === 0 ? 'no matching visible agent terminal' : 'multiple matching visible agent terminals',
          matches: matches.map((item) => ({ pid: item.agentPid, tty: item.tty, cwd: item.cwd })),
        });
        return;
      }
      const match = matches[0];
      match.terminal.show(false);
      if (requestUrl.pathname === '/terminal/send') {
        match.terminal.sendText(prompt, true);
      }
      sendJson(response, 200, {
        status: 'success',
        message: requestUrl.pathname === '/terminal/send'
          ? 'prompt submitted to visible integrated terminal'
          : 'visible integrated terminal focused',
        target_slug: target,
        terminal_name: match.terminalName,
        shell_pid: match.shellPid,
        agent_pid: match.agentPid,
        tty: match.tty,
        project_path: match.cwd,
      });
    } catch (error) {
      sendJson(response, 500, { status: 'failed', message: String(error && error.message ? error.message : error) });
    }
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  fs.mkdirSync(registryDir, { recursive: true });
  registryPath = path.join(registryDir, `${process.pid}.json`);
  atomicWrite(registryPath, {
    schema: 'ai-collab.ide-bridge.v1',
    pid: process.pid,
    port: address.port,
    token,
    project_paths: workspacePaths(),
    ide: vscode.env.appName,
    updated: new Date().toISOString(),
  });

  const refresh = () => {
    if (!registryPath || !server || !server.listening) return;
    atomicWrite(registryPath, {
      schema: 'ai-collab.ide-bridge.v1',
      pid: process.pid,
      port: server.address().port,
      token,
      project_paths: workspacePaths(),
      ide: vscode.env.appName,
      updated: new Date().toISOString(),
    });
  };
  context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(refresh));
  context.subscriptions.push(vscode.commands.registerCommand('aiCollab.visibleBridgeStatus', () => {
    vscode.window.showInformationMessage(`AI Collab visible bridge is active for ${workspacePaths().join(', ') || '(no folder)'}.`);
  }));
  context.subscriptions.push({ dispose: () => server && server.close() });
}

function deactivate() {
  if (server) server.close();
  if (registryPath) {
    try { fs.unlinkSync(registryPath); } catch (_error) {}
  }
}

module.exports = { activate, deactivate };
