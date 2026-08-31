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
const nativeSessions = new Map();

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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function nativeConfig(target) {
  if (target === 'claude-code-ide') {
    return { open: 'claude-vscode.editor.openLast', focus: 'claude-vscode.focus' };
  }
  if (['cursor-native', 'windsurf-native', 'copilot-chat'].includes(target)) {
    return { open: 'workbench.action.chat.open', focus: 'workbench.action.chat.open' };
  }
  if (target === 'codex') {
    // openai.chatgpt does not register a standard VS Code chat participant
    // (no @codex in a shared chat view) -- it exposes its own commands
    // instead. chatgpt.openSidebar is the closest analog to
    // claude-vscode.focus: it brings up Codex's own panel specifically,
    // via the real extension API, not a guess at "the last active window"
    // (2026-08-30, investigating whether VS Code fares better than
    // Antigravity IDE for reaching codex unattended).
    return { open: 'chatgpt.openSidebar', focus: 'chatgpt.openSidebar' };
  }
  return null;
}

function nativeTargetAvailable(target) {
  const appName = String(vscode.env.appName || '').toLowerCase();
  if (target === 'cursor-native') return appName.includes('cursor');
  if (target === 'windsurf-native') return appName.includes('windsurf');
  if (target === 'copilot-chat') return Boolean(vscode.extensions.getExtension('github.copilot-chat'));
  if (target === 'claude-code-ide') return Boolean(vscode.extensions.getExtension('anthropic.claude-code'));
  if (target === 'codex') return Boolean(vscode.extensions.getExtension('openai.chatgpt'));
  return false;
}

function buildNativeSession(projectPath, target, surfaceId) {
  const key = `${real(projectPath)}\0${target}\0${surfaceId}`;
  if (nativeSessions.has(key)) return nativeSessions.get(key);
  const manifestPath = path.join(real(projectPath), '.ai-collab', 'agents.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const agent = (manifest.agents || []).find((item) => item.agent === target);
  if (!agent || !agent.agent_id) throw new Error(`registered agent identity not found for ${target}`);
  const sessionId = `ses_${new Date().toISOString().replace(/[-:.]/g, '')}_${crypto.randomBytes(6).toString('hex')}`;
  const payload = {
    schema: 'ai-collab.session.v1', project: path.basename(real(projectPath)), project_path: real(projectPath),
    project_id: manifest.project_id, agent: target, agent_id: agent.agent_id, session_id: sessionId,
    container: 'ide-native', surface_kind: 'ide-native-chat', surface_id: surfaceId,
    host_pid: process.pid, adapter: 'ide-native-chat', status: 'active', started: new Date().toISOString(),
    heartbeat_at: new Date().toISOString(),
  };
  return payload;
}

function persistNativeSession(projectPath, target, surfaceId, payload) {
  const key = `${real(projectPath)}\0${target}\0${surfaceId}`;
  const sessionDir = path.join(real(projectPath), '.ai-collab', 'live', 'sessions');
  atomicWrite(path.join(sessionDir, `${payload.session_id}.json`), payload);
  atomicWrite(path.join(sessionDir, `current-${target}.json`), payload);
  nativeSessions.set(key, payload);
  return payload;
}

function nativeBootstrap(projectPath, target, session) {
  const manifest = JSON.parse(fs.readFileSync(path.join(real(projectPath), '.ai-collab', 'agents.json'), 'utf8'));
  const agent = (manifest.agents || []).find((item) => item.agent === target) || {};
  const rules = Array.isArray(agent.rules) ? agent.rules.join(', ') : '';
  return [
    `AI Collab exact identity: agent=${target}; project_id=${session.project_id}; agent_id=${session.agent_id}; session_id=${session.session_id}; surface_id=${session.surface_id}.`,
    rules ? `Read your identity-specific rules at ${rules}.` : 'Read your registered AI Collab rules.',
    'Do not adopt another agent block with a different agent_id or session_id.',
  ].join(' ');
}

async function focusNative(target) {
  const config = nativeConfig(target);
  if (!config) throw new Error(`native chat adapter does not support target ${target}`);
  if (!nativeTargetAvailable(target)) {
    throw new Error(`native target ${target} is not installed in ${vscode.env.appName || 'this IDE'}`);
  }
  const commands = new Set(await vscode.commands.getCommands(true));
  if (!commands.has(config.focus)) throw new Error(`native command unavailable: ${config.focus}`);
  if (commands.has(config.open)) await vscode.commands.executeCommand(config.open);
  await delay(250);
  await vscode.commands.executeCommand(config.focus);
  await delay(150);
}

async function pasteNative(prompt) {
  const previous = await vscode.env.clipboard.readText();
  await vscode.env.clipboard.writeText(prompt);
  try {
    const appName = String(vscode.env.appName || '').replace(/"/g, '');
    const script = `tell application "System Events"\nset frontmost of first process whose name contains "${appName}" to true\ndelay 0.2\nkeystroke "v" using command down\ndelay 0.1\nkey code 36\nend tell`;
    await runStrict('/usr/bin/osascript', ['-e', script]);
    await delay(150);
  } finally {
    await delay(100);
    await vscode.env.clipboard.writeText(previous);
  }
}

function run(command, args) {
  return new Promise((resolve) => {
    execFile(command, args, { timeout: 5000, maxBuffer: 2 * 1024 * 1024 }, (error, stdout) => {
      resolve(error ? '' : String(stdout || ''));
    });
  });
}

function runStrict(command, args) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { timeout: 5000, maxBuffer: 2 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) reject(new Error(String(stderr || error.message || error)));
      else resolve(String(stdout || ''));
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
  if (target === 'aider') {
    return /(^|\/)aider(?:\s|$)/.test(command);
  }
  if (target === 'kimi') {
    return /(^|\/)(?:kimi|kimi-cli)(?:\s|$)/.test(command);
  }
  if (target === 'kilo') {
    return /(^|\/)kilo(?:\s|$)/.test(command);
  }
  return false;
}

function registeredTerminalSession(projectPath, target) {
  const expected = real(projectPath);
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(expected, '.ai-collab', 'agents.json'), 'utf8'));
    const agent = (manifest.agents || []).find((item) => item.agent === target);
    if (!agent || !agent.agent_id) return null;
    const currentPath = path.join(expected, '.ai-collab', 'live', 'sessions', `current-${target}.json`);
    const session = JSON.parse(fs.readFileSync(currentPath, 'utf8'));
    if (session.status !== 'active' || session.surface_kind === 'ide-native-chat') return null;
    if (real(session.project_path || '') !== expected || session.agent !== target || session.agent_id !== agent.agent_id) return null;
    if (!session.session_id || !(Number(session.pid) > 0)) return null;
    return session;
  } catch (_error) {
    return null;
  }
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
  const registered = registeredTerminalSession(projectPath, target);
  const matches = [];
  for (const terminal of vscode.window.terminals) {
    const shellPid = await terminal.processId;
    if (!shellPid) continue;
    for (const child of descendants(rows, shellPid)) {
      const identityMatch = registered && child.pid === Number(registered.pid);
      if (!identityMatch && !commandMatches(target, child.command)) continue;
      const cwd = await cwdForPid(child.pid);
      if (cwd === expected) {
        matches.push({
          terminal, terminalName: terminal.name, shellPid, agentPid: child.pid, tty: child.tty, cwd,
          agentId: identityMatch ? registered.agent_id : '', sessionId: identityMatch ? registered.session_id : '',
          surfaceId: identityMatch ? registered.surface_id : '', identity: identityMatch ? 'registered-session' : 'process-fallback',
        });
      }
    }
  }
  const exact = matches.filter((item) => item.identity === 'registered-session');
  return exact.length ? exact : matches;
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
      const manifest = JSON.parse(fs.readFileSync(path.join(real(projectPath), '.ai-collab', 'agents.json'), 'utf8'));
      for (const target of (manifest.agents || []).map((item) => item.agent)) {
        const matches = await matchingTerminals(target, projectPath);
        for (const item of matches) {
          terminals.push({
            agent: target,
            terminal_name: item.terminalName,
            shell_pid: item.shellPid,
            agent_pid: item.agentPid,
            tty: item.tty,
            project_path: item.cwd,
            agent_id: item.agentId,
            session_id: item.sessionId,
            surface_id: item.surfaceId,
            identity: item.identity,
          });
        }
      }
      sendJson(response, 200, { status: 'success', project_path: real(projectPath), terminals });
      return;
    }
    if (request.method !== 'POST' || !['/terminal/send', '/terminal/show', '/native/send', '/native/show'].includes(requestUrl.pathname)) {
      sendJson(response, 404, { status: 'failed', message: 'not found' });
      return;
    }
    try {
      const payload = JSON.parse(await readBody(request));
      const projectPath = String(payload.project_path || '');
      const target = String(payload.target_slug || '');
      const prompt = String(payload.prompt || '');
      if (!projectPath || !target || (requestUrl.pathname.endsWith('/send') && !prompt)) {
        sendJson(response, 400, { status: 'failed', message: 'project_path, target_slug, and prompt are required for send' });
        return;
      }
      if (!workspacePaths().includes(real(projectPath))) {
        sendJson(response, 409, { status: 'failed', message: 'bridge workspace does not match requested project' });
        return;
      }
      if (requestUrl.pathname.startsWith('/native/')) {
        const surfaceId = `vscode-native:${target}:${crypto.createHash('sha256').update(real(projectPath)).digest('hex').slice(0, 12)}`;
        if (!nativeConfig(target)) {
          sendJson(response, 409, { status: 'failed', message: `no native adapter for ${target}` });
          return;
        }
        await focusNative(target);
        const session = buildNativeSession(projectPath, target, surfaceId);
        if (requestUrl.pathname === '/native/send') {
          await pasteNative(`${nativeBootstrap(projectPath, target, session)}\n\n${prompt}`);
        }
        session.heartbeat_at = new Date().toISOString();
        persistNativeSession(projectPath, target, surfaceId, session);
        sendJson(response, 200, {
          status: 'success', message: requestUrl.pathname === '/native/send' ? 'prompt submitted to exact native chat' : 'exact native chat focused',
          target_slug: target, project_path: real(projectPath), surface_id: surfaceId,
          agent_id: session.agent_id, session_id: session.session_id, bridge_pid: process.pid,
        });
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
        agent_id: match.agentId,
        session_id: match.sessionId,
        surface_id: match.surfaceId,
        identity: match.identity,
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
