import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as http from 'http';

const VECTR_DIR = path.join(os.homedir(), '.vectr');
const INSTANCES_FILE = path.join(VECTR_DIR, 'instances.json');

let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;

// T23: per-workspace polling timers
const workspacePollers: Map<string, NodeJS.Timeout> = new Map();
// T23: per-workspace active port
const workspacePorts: Map<string, number> = new Map();

export function activate(context: vscode.ExtensionContext): void {
    outputChannel = vscode.window.createOutputChannel('Vectr');

    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBarItem.command = 'vectr.showStatus';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    setStatus('stopped');

    context.subscriptions.push(
        vscode.commands.registerCommand('vectr.showStatus', showStatus),
        vscode.commands.registerCommand('vectr.reindex', reindex),
        vscode.commands.registerCommand('vectr.stop', stopDaemon),
    );

    const cfg = vscode.workspace.getConfiguration('vectr');
    if (cfg.get<boolean>('autoStart', true)) {
        startAllWorkspaces(context);
    }

    // T23: react to workspace folder changes (add/remove)
    context.subscriptions.push(
        vscode.workspace.onDidChangeWorkspaceFolders(event => {
            for (const added of event.added) {
                startWorkspace(context, added.uri.fsPath);
            }
            for (const removed of event.removed) {
                stopWorkspacePoller(removed.uri.fsPath);
            }
        })
    );
}

export function deactivate(): void {
    for (const timer of workspacePollers.values()) {
        clearInterval(timer);
    }
}

// ---------------------------------------------------------------------------
// T23: Multi-workspace lifecycle
// ---------------------------------------------------------------------------

async function startAllWorkspaces(context: vscode.ExtensionContext): Promise<void> {
    const folders = vscode.workspace.workspaceFolders ?? [];
    if (folders.length === 0) return;
    // Start daemons for all workspace folders in parallel
    await Promise.all(folders.map(f => startWorkspace(context, f.uri.fsPath)));
}

async function startWorkspace(context: vscode.ExtensionContext, workspaceRoot: string): Promise<void> {
    const cfg = vscode.workspace.getConfiguration('vectr');
    const preferredPort = cfg.get<number>('port', 8765);
    const embedModel = cfg.get<string>('embedModel', 'Snowflake/snowflake-arctic-embed-m-v1.5');

    // Check if this workspace already has a live daemon (via instances.json)
    const existingPort = readWorkspacePort(workspaceRoot);
    if (existingPort && await isVectrAlive(existingPort)) {
        outputChannel.appendLine(`[${path.basename(workspaceRoot)}] Already running on port ${existingPort}`);
        workspacePorts.set(workspaceRoot, existingPort);
        startWorkspacePoller(workspaceRoot, existingPort);
        updateAggregateStatus();
        return;
    }

    const vectrBin = await findVectrBin();
    if (!vectrBin) {
        outputChannel.appendLine(`Vectr not found in PATH. Install: pip install vectr`);
        setStatus('stopped');
        return;
    }

    outputChannel.appendLine(`[${path.basename(workspaceRoot)}] Starting daemon...`);
    setStatus('indexing', preferredPort);

    const env: NodeJS.ProcessEnv = {
        ...process.env,
        VECTR_WORKSPACE: workspaceRoot,
        VECTR_PORT: String(preferredPort),
        VECTR_EMBED_MODEL: embedModel,
    };

    cp.spawn(vectrBin, ['start', '--path', workspaceRoot, '--port', String(preferredPort)], {
        env,
        detached: true,
        stdio: 'ignore',
    }).unref();

    // Wait up to 30s for the server to come up
    for (let i = 0; i < 30; i++) {
        await sleep(1000);
        // Re-read port from instances.json (vectr may have picked a different port)
        const assignedPort = readWorkspacePort(workspaceRoot) ?? preferredPort;
        if (await isVectrAlive(assignedPort)) {
            outputChannel.appendLine(`[${path.basename(workspaceRoot)}] Ready on port ${assignedPort}`);
            workspacePorts.set(workspaceRoot, assignedPort);
            startWorkspacePoller(workspaceRoot, assignedPort);
            // T24: auto-update .vscode/mcp.json if port changed
            await syncMcpConfig(workspaceRoot, assignedPort);
            updateAggregateStatus();
            return;
        }
    }

    outputChannel.appendLine(`[${path.basename(workspaceRoot)}] Did not start within 30s`);
    setStatus('error');
}

// ---------------------------------------------------------------------------
// T24: Auto-update .vscode/mcp.json when port changes
// ---------------------------------------------------------------------------

async function syncMcpConfig(workspaceRoot: string, port: number): Promise<void> {
    const mcpPath = path.join(workspaceRoot, '.vscode', 'mcp.json');
    let existingPort: number | null = null;

    try {
        const raw = JSON.parse(fs.readFileSync(mcpPath, 'utf8'));
        const url: string = raw?.servers?.vectr?.url ?? raw?.mcpServers?.vectr?.url ?? '';
        const match = url.match(/:(\d+)\//);
        if (match) existingPort = parseInt(match[1], 10);
    } catch {
        // file doesn't exist yet — that's fine, vectr start wrote it
    }

    if (existingPort !== null && existingPort !== port) {
        outputChannel.appendLine(`[${path.basename(workspaceRoot)}] Port changed ${existingPort} → ${port}. Updating .vscode/mcp.json`);
        try {
            fs.mkdirSync(path.join(workspaceRoot, '.vscode'), { recursive: true });
            fs.writeFileSync(mcpPath, JSON.stringify({
                servers: {
                    vectr: { type: 'http', url: `http://localhost:${port}/mcp` }
                }
            }, null, 2));
        } catch (e) {
            outputChannel.appendLine(`Failed to update .vscode/mcp.json: ${e}`);
        }
    }
}

// ---------------------------------------------------------------------------
// Polling — per workspace
// ---------------------------------------------------------------------------

function startWorkspacePoller(workspaceRoot: string, port: number): void {
    stopWorkspacePoller(workspaceRoot);
    const timer = setInterval(async () => {
        try {
            await httpGet(port, '/v1/health');
            updateAggregateStatus();
        } catch {
            workspacePorts.delete(workspaceRoot);
            stopWorkspacePoller(workspaceRoot);
            updateAggregateStatus();
        }
    }, 5000);
    workspacePollers.set(workspaceRoot, timer);
}

function stopWorkspacePoller(workspaceRoot: string): void {
    const timer = workspacePollers.get(workspaceRoot);
    if (timer) {
        clearInterval(timer);
        workspacePollers.delete(workspaceRoot);
    }
}

// Aggregate status across all tracked workspaces
function updateAggregateStatus(): void {
    const livePorts = Array.from(workspacePorts.values());
    if (livePorts.length === 0) {
        setStatus('stopped');
        return;
    }
    // Use the first live port for the status bar (simplification for multi-workspace)
    const port = livePorts[0];
    httpGet(port, '/v1/status')
        .then(data => {
            const chunks = (data as any).total_chunks ?? 0;
            const workspaceCount = workspacePorts.size;
            statusBarItem.text = workspaceCount > 1
                ? `$(database) Vectr: Ready (${workspaceCount} workspaces, ${chunks.toLocaleString()} chunks)`
                : `$(database) Vectr: Ready (${chunks.toLocaleString()} chunks)`;
            statusBarItem.tooltip = `Vectr MCP: http://localhost:${port}/mcp`;
            statusBarItem.backgroundColor = undefined;
        })
        .catch(() => setStatus('error'));
}

// ---------------------------------------------------------------------------
// Daemon control
// ---------------------------------------------------------------------------

function stopDaemon(): void {
    const folders = vscode.workspace.workspaceFolders ?? [];
    const wsPath = folders[0]?.uri.fsPath;
    if (!wsPath) {
        vscode.window.showInformationMessage('Vectr: no workspace open');
        return;
    }
    cp.exec(`vectr stop --path "${wsPath}"`, err => {
        if (err) outputChannel.appendLine(`Stop error: ${err.message}`);
    });
    stopWorkspacePoller(wsPath);
    workspacePorts.delete(wsPath);
    updateAggregateStatus();
}

async function reindex(): Promise<void> {
    const folders = vscode.workspace.workspaceFolders ?? [];
    const wsPath = folders[0]?.uri.fsPath;
    if (!wsPath) return;
    const port = workspacePorts.get(wsPath);
    if (!port) {
        vscode.window.showErrorMessage('Vectr is not running. Start it first.');
        return;
    }
    await httpPost(port, '/v1/index', { path: wsPath, force: true });
    vscode.window.showInformationMessage('Vectr: re-indexing started');
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

async function showStatus(): Promise<void> {
    const folders = vscode.workspace.workspaceFolders ?? [];
    if (folders.length === 0) {
        vscode.window.showInformationMessage('No workspace open.');
        return;
    }
    const messages: string[] = [];
    for (const folder of folders) {
        const port = workspacePorts.get(folder.uri.fsPath);
        if (!port) {
            messages.push(`${folder.name}: stopped`);
            continue;
        }
        try {
            const data = await httpGet(port, '/v1/status') as any;
            messages.push(`${folder.name}: ${data.indexed_files} files · ${data.total_chunks} chunks (port ${port})`);
        } catch {
            messages.push(`${folder.name}: unreachable`);
        }
    }
    vscode.window.showInformationMessage(messages.join(' | '));
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readWorkspacePort(workspaceRoot: string): number | null {
    try {
        const instances = JSON.parse(fs.readFileSync(INSTANCES_FILE, 'utf8'));
        // workspace hash used as key in instances.json
        for (const entry of Object.values(instances) as any[]) {
            if (entry.workspace === workspaceRoot && entry.port) {
                return entry.port;
            }
        }
    } catch {
        // instances.json may not exist yet
    }
    return null;
}

async function findVectrBin(): Promise<string | null> {
    const candidates = ['vectr', 'python3.14 -m vectr', 'python3 -m vectr', 'python -m vectr'];
    for (const cmd of candidates) {
        try {
            cp.execSync(`${cmd} --help`, { stdio: 'ignore', timeout: 3000 });
            return cmd.split(' ')[0];
        } catch { /* try next */ }
    }
    return null;
}

function isVectrAlive(port: number): Promise<boolean> {
    return new Promise(resolve => {
        http.get(`http://localhost:${port}/v1/health`, res => {
            resolve(res.statusCode === 200);
        }).on('error', () => resolve(false));
    });
}

function httpGet(port: number, urlPath: string): Promise<unknown> {
    return new Promise((resolve, reject) => {
        http.get(`http://localhost:${port}${urlPath}`, res => {
            let body = '';
            res.on('data', chunk => body += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(body)); } catch { reject(new Error('Invalid JSON')); }
            });
        }).on('error', reject);
    });
}

function httpPost(port: number, urlPath: string, payload: object): Promise<unknown> {
    return new Promise((resolve, reject) => {
        const body = JSON.stringify(payload);
        const req = http.request(
            { hostname: 'localhost', port, path: urlPath, method: 'POST',
              headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } },
            res => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => { try { resolve(JSON.parse(data)); } catch { reject(new Error('Invalid JSON')); } });
            }
        );
        req.on('error', reject);
        req.write(body);
        req.end();
    });
}

function setStatus(state: 'indexing' | 'ready' | 'stopped' | 'error', port?: number, chunks?: number): void {
    switch (state) {
        case 'indexing':
            statusBarItem.text = '$(database~spin) Vectr: Indexing...';
            statusBarItem.tooltip = port ? `Vectr MCP server on port ${port}` : 'Vectr starting...';
            statusBarItem.backgroundColor = undefined;
            break;
        case 'ready':
            statusBarItem.text = `$(database) Vectr: Ready (${(chunks ?? 0).toLocaleString()} chunks)`;
            statusBarItem.tooltip = `Vectr MCP: http://localhost:${port}/mcp`;
            statusBarItem.backgroundColor = undefined;
            break;
        case 'stopped':
            statusBarItem.text = '$(database) Vectr: Stopped';
            statusBarItem.tooltip = 'Click to see Vectr status';
            statusBarItem.backgroundColor = undefined;
            break;
        case 'error':
            statusBarItem.text = '$(warning) Vectr: Error';
            statusBarItem.tooltip = 'Vectr daemon error — check Output panel';
            statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
            break;
    }
}

function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
}
