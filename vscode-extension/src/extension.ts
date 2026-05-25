import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as http from 'http';

const PID_DIR = path.join(os.homedir(), '.vectr');
const PID_FILE = path.join(PID_DIR, 'vectr.pid');
const PORT_FILE = path.join(PID_DIR, 'vectr.port');

let statusBarItem: vscode.StatusBarItem;
let pollInterval: NodeJS.Timeout | undefined;
let outputChannel: vscode.OutputChannel;

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
        startDaemon(context);
    }
}

export function deactivate(): void {
    if (pollInterval) clearInterval(pollInterval);
}

// ---------------------------------------------------------------------------
// Daemon lifecycle
// ---------------------------------------------------------------------------

async function startDaemon(context: vscode.ExtensionContext): Promise<void> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!workspaceRoot) return;

    const cfg = vscode.workspace.getConfiguration('vectr');
    const port = cfg.get<number>('port', 8765);
    const embedModel = cfg.get<string>('embedModel', 'jinaai/jina-embeddings-v2-base-code');

    // Check if daemon is already running
    const runningPort = readRunningPort();
    if (runningPort && await isVectrAlive(runningPort)) {
        outputChannel.appendLine(`Vectr already running on port ${runningPort}`);
        setStatus('indexing', runningPort);
        startPolling(runningPort);
        return;
    }

    // Find vectr executable
    const vectrBin = await findVectrBin();
    if (!vectrBin) {
        setStatus('stopped');
        outputChannel.appendLine('Vectr not found in PATH. Install with: pip install vectr');
        return;
    }

    outputChannel.appendLine(`Starting Vectr daemon for: ${workspaceRoot}`);
    setStatus('indexing', port);

    fs.mkdirSync(PID_DIR, { recursive: true });
    fs.writeFileSync(PORT_FILE, String(port));

    const env: NodeJS.ProcessEnv = {
        ...process.env,
        VECTR_WORKSPACE: workspaceRoot,
        VECTR_PORT: String(port),
        VECTR_EMBED_MODEL: embedModel,
    };

    const proc = cp.spawn(vectrBin, ['start', '--path', workspaceRoot, '--port', String(port)], {
        env,
        detached: true,
        stdio: 'ignore',
    });
    proc.unref();

    if (proc.pid) {
        fs.writeFileSync(PID_FILE, String(proc.pid));
    }

    // Wait up to 30s for the server to come up
    for (let i = 0; i < 30; i++) {
        await sleep(1000);
        if (await isVectrAlive(port)) {
            startPolling(port);
            return;
        }
    }
    setStatus('error');
    outputChannel.appendLine('Vectr did not start within 30 seconds');
}

async function findVectrBin(): Promise<string | null> {
    const candidates = ['vectr', 'python -m vectr', 'python3 -m vectr', 'python3.14 -m vectr'];
    for (const cmd of candidates) {
        try {
            cp.execSync(`${cmd} --help`, { stdio: 'ignore', timeout: 3000 });
            return cmd.split(' ')[0];
        } catch {
            // try next
        }
    }
    return null;
}

function stopDaemon(): void {
    const port = readRunningPort();
    if (!port) {
        vscode.window.showInformationMessage('Vectr: no running daemon found');
        return;
    }
    cp.exec('vectr stop', (err) => {
        if (err) {
            outputChannel.appendLine(`Stop error: ${err.message}`);
        }
    });
    if (pollInterval) clearInterval(pollInterval);
    setStatus('stopped');
}

async function reindex(): Promise<void> {
    const port = readRunningPort();
    if (!port) {
        vscode.window.showErrorMessage('Vectr is not running. Start it first.');
        return;
    }
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '.';
    await httpPost(port, '/v1/index', { path: workspaceRoot, force: true });
    vscode.window.showInformationMessage('Vectr: re-indexing started');
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

function startPolling(port: number): void {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(async () => {
        try {
            const data = await httpGet(port, '/v1/status');
            const chunks = (data as any).total_chunks ?? 0;
            setStatus('ready', port, chunks);
        } catch {
            setStatus('error');
        }
    }, 5000);
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

function setStatus(state: 'indexing' | 'ready' | 'stopped' | 'error', port?: number, chunks?: number): void {
    switch (state) {
        case 'indexing':
            statusBarItem.text = '$(database~spin) Vectr: Indexing...';
            statusBarItem.tooltip = `Vectr MCP server on port ${port}`;
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

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

async function showStatus(): Promise<void> {
    const port = readRunningPort();
    if (!port) {
        vscode.window.showInformationMessage('Vectr is not running. Enable autoStart or run: vectr start');
        return;
    }
    try {
        const data = await httpGet(port, '/v1/status') as any;
        vscode.window.showInformationMessage(
            `Vectr: ${data.indexed_files} files · ${data.total_chunks} chunks · ${data.embed_model}`
        );
    } catch {
        vscode.window.showErrorMessage('Could not reach Vectr daemon');
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readRunningPort(): number | null {
    try {
        return parseInt(fs.readFileSync(PORT_FILE, 'utf8').trim(), 10);
    } catch {
        return null;
    }
}

function isVectrAlive(port: number): Promise<boolean> {
    return new Promise(resolve => {
        http.get(`http://localhost:${port}/v1/health`, (res) => {
            resolve(res.statusCode === 200);
        }).on('error', () => resolve(false));
    });
}

function httpGet(port: number, path: string): Promise<unknown> {
    return new Promise((resolve, reject) => {
        http.get(`http://localhost:${port}${path}`, (res) => {
            let body = '';
            res.on('data', (chunk) => body += chunk);
            res.on('end', () => {
                try { resolve(JSON.parse(body)); } catch { reject(new Error('Invalid JSON')); }
            });
        }).on('error', reject);
    });
}

function httpPost(port: number, path: string, payload: object): Promise<unknown> {
    return new Promise((resolve, reject) => {
        const body = JSON.stringify(payload);
        const req = http.request(
            { hostname: 'localhost', port, path, method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } },
            (res) => {
                let data = '';
                res.on('data', (chunk) => data += chunk);
                res.on('end', () => { try { resolve(JSON.parse(data)); } catch { reject(new Error('Invalid JSON')); } });
            }
        );
        req.on('error', reject);
        req.write(body);
        req.end();
    });
}

function sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
}
