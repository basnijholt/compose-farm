/**
 * Compose Farm Web UI JavaScript
 */

// ANSI escape codes for terminal output
const ANSI = {
    RED: '\x1b[31m',
    GREEN: '\x1b[32m',
    DIM: '\x1b[2m',
    RESET: '\x1b[0m',
    CRLF: '\r\n'
};

// Store active terminals and editors
const terminals = {};
const editors = {};
let monacoLoaded = false;
let monacoLoading = false;

// LocalStorage key prefix for active tasks (scoped by page)
const TASK_KEY_PREFIX = 'cf_task:';
const getTaskKey = () => TASK_KEY_PREFIX + window.location.pathname;

// Language detection from file path
const LANGUAGE_MAP = {
    'yaml': 'yaml', 'yml': 'yaml',
    'json': 'json',
    'js': 'javascript', 'mjs': 'javascript',
    'ts': 'typescript', 'tsx': 'typescript',
    'py': 'python',
    'sh': 'shell', 'bash': 'shell',
    'md': 'markdown',
    'html': 'html', 'htm': 'html',
    'css': 'css',
    'sql': 'sql',
    'toml': 'toml',
    'ini': 'ini', 'conf': 'ini',
    'dockerfile': 'dockerfile',
    'env': 'plaintext'
};

/**
 * Get Monaco language from file path
 * @param {string} path - File path
 * @returns {string} Monaco language identifier
 */
function getLanguageFromPath(path) {
    const ext = path.split('.').pop().toLowerCase();
    return LANGUAGE_MAP[ext] || 'plaintext';
}
window.getLanguageFromPath = getLanguageFromPath;

// Terminal color theme (dark mode matching PicoCSS)
const TERMINAL_THEME = {
    background: '#1a1a2e',
    foreground: '#e4e4e7',
    cursor: '#e4e4e7',
    cursorAccent: '#1a1a2e',
    black: '#18181b',
    red: '#ef4444',
    green: '#22c55e',
    yellow: '#eab308',
    blue: '#3b82f6',
    magenta: '#a855f7',
    cyan: '#06b6d4',
    white: '#e4e4e7',
    brightBlack: '#52525b',
    brightRed: '#f87171',
    brightGreen: '#4ade80',
    brightYellow: '#facc15',
    brightBlue: '#60a5fa',
    brightMagenta: '#c084fc',
    brightCyan: '#22d3ee',
    brightWhite: '#fafafa'
};

/**
 * Create a terminal with fit addon and resize observer
 * @param {HTMLElement} container - Container element
 * @param {object} extraOptions - Additional terminal options
 * @param {function} onResize - Optional callback called with (cols, rows) after resize
 * @returns {{term: Terminal, fitAddon: FitAddon}}
 */
function createTerminal(container, extraOptions = {}, onResize = null) {
    container.innerHTML = '';

    const term = new Terminal({
        convertEol: true,
        theme: TERMINAL_THEME,
        fontSize: 13,
        fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
        scrollback: 5000,
        ...extraOptions
    });

    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);
    fitAddon.fit();

    const handleResize = () => {
        fitAddon.fit();
        if (onResize) {
            onResize(term.cols, term.rows);
        }
    };

    window.addEventListener('resize', handleResize);
    new ResizeObserver(handleResize).observe(container);

    return { term, fitAddon };
}

/**
 * Create WebSocket connection with standard handlers
 * @param {string} path - WebSocket path
 * @returns {WebSocket}
 */
function createWebSocket(path) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return new WebSocket(`${protocol}//${window.location.host}${path}`);
}
window.createWebSocket = createWebSocket;

/**
 * Initialize a terminal and connect to WebSocket for streaming
 */
function initTerminal(elementId, taskId) {
    const container = document.getElementById(elementId);
    if (!container) {
        console.error('Terminal container not found:', elementId);
        return;
    }

    const { term, fitAddon } = createTerminal(container);
    const ws = createWebSocket(`/ws/terminal/${taskId}`);

    const taskKey = getTaskKey();
    ws.onopen = () => {
        term.write(`${ANSI.DIM}[Connected]${ANSI.RESET}${ANSI.CRLF}`);
        setTerminalLoading(true);
        localStorage.setItem(taskKey, taskId);
    };
    ws.onmessage = (event) => {
        term.write(event.data);
        if (event.data.includes('[Done]') || event.data.includes('[Failed]')) {
            localStorage.removeItem(taskKey);
        }
    };
    ws.onclose = () => setTerminalLoading(false);
    ws.onerror = (error) => {
        term.write(`${ANSI.RED}[WebSocket Error]${ANSI.RESET}${ANSI.CRLF}`);
        console.error('WebSocket error:', error);
        setTerminalLoading(false);
    };

    terminals[taskId] = { term, ws, fitAddon };
    return { term, ws };
}

window.initTerminal = initTerminal;

/**
 * Initialize an interactive exec terminal
 */
let execTerminal = null;
let execWs = null;

function initExecTerminal(service, container, host) {
    const containerEl = document.getElementById('exec-terminal-container');
    const terminalEl = document.getElementById('exec-terminal');

    if (!containerEl || !terminalEl) {
        console.error('Exec terminal elements not found');
        return;
    }

    containerEl.classList.remove('hidden');

    // Clean up existing
    if (execWs) { execWs.close(); execWs = null; }
    if (execTerminal) { execTerminal.dispose(); execTerminal = null; }

    // Create WebSocket first so resize callback can use it
    execWs = createWebSocket(`/ws/exec/${service}/${container}/${host}`);

    // Resize callback sends size to WebSocket
    const sendSize = (cols, rows) => {
        if (execWs && execWs.readyState === WebSocket.OPEN) {
            execWs.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
    };

    const { term } = createTerminal(terminalEl, { cursorBlink: true }, sendSize);
    execTerminal = term;

    execWs.onopen = () => { sendSize(term.cols, term.rows); term.focus(); };
    execWs.onmessage = (event) => term.write(event.data);
    execWs.onclose = () => term.write(`${ANSI.CRLF}${ANSI.DIM}[Connection closed]${ANSI.RESET}${ANSI.CRLF}`);
    execWs.onerror = (error) => {
        term.write(`${ANSI.RED}[WebSocket Error]${ANSI.RESET}${ANSI.CRLF}`);
        console.error('Exec WebSocket error:', error);
    };

    term.onData((data) => {
        if (execWs && execWs.readyState === WebSocket.OPEN) {
            execWs.send(data);
        }
    });
}

window.initExecTerminal = initExecTerminal;

/**
 * Refresh dashboard partials while preserving collapse states
 */
function refreshDashboard() {
    const isExpanded = (id) => document.getElementById(id)?.checked ?? true;
    htmx.ajax('GET', '/partials/sidebar', {target: '#sidebar nav', swap: 'innerHTML'});
    htmx.ajax('GET', '/partials/stats', {target: '#stats-cards', swap: 'outerHTML'});
    htmx.ajax('GET', `/partials/pending?expanded=${isExpanded('pending-collapse')}`, {target: '#pending-operations', swap: 'outerHTML'});
    htmx.ajax('GET', `/partials/services-by-host?expanded=${isExpanded('services-by-host-collapse')}`, {target: '#services-by-host', swap: 'outerHTML'});
    htmx.ajax('GET', '/partials/config-error', {target: '#config-error', swap: 'innerHTML'});
}

/**
 * Load Monaco editor dynamically (only once)
 */
function loadMonaco(callback) {
    if (monacoLoaded) {
        callback();
        return;
    }

    if (monacoLoading) {
        // Wait for it to load
        const checkInterval = setInterval(() => {
            if (monacoLoaded) {
                clearInterval(checkInterval);
                callback();
            }
        }, 100);
        return;
    }

    monacoLoading = true;

    // Load the Monaco loader script
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/loader.js';
    script.onload = function() {
        require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs' }});
        require(['vs/editor/editor.main'], function() {
            monacoLoaded = true;
            monacoLoading = false;
            callback();
        });
    };
    document.head.appendChild(script);
}

/**
 * Create a Monaco editor instance
 * @param {HTMLElement} container - Container element
 * @param {string} content - Initial content
 * @param {string} language - Editor language (yaml, plaintext, etc.)
 * @param {object} opts - Options: { readonly, onSave }
 * @returns {object} Monaco editor instance
 */
function createEditor(container, content, language, opts = {}) {
    // Support legacy boolean readonly parameter
    if (typeof opts === 'boolean') {
        opts = { readonly: opts };
    }
    const { readonly = false, onSave = null } = opts;

    const options = {
        value: content,
        language: language,
        theme: 'vs-dark',
        minimap: { enabled: false },
        automaticLayout: true,
        scrollBeyondLastLine: false,
        fontSize: 14,
        lineNumbers: 'on',
        wordWrap: 'on'
    };

    if (readonly) {
        options.readOnly = true;
        options.domReadOnly = true;
    }

    const editor = monaco.editor.create(container, options);

    // Add Command+S / Ctrl+S handler for editable editors
    if (!readonly) {
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, function() {
            if (onSave) {
                onSave(editor);
            } else {
                saveAllEditors();
            }
        });
    }

    return editor;
}
window.createEditor = createEditor;

/**
 * Initialize all Monaco editors on the page
 */
function initMonacoEditors() {
    // Dispose existing editors
    Object.values(editors).forEach(ed => {
        if (ed && ed.dispose) ed.dispose();
    });
    Object.keys(editors).forEach(key => delete editors[key]);

    const editorConfigs = [
        { id: 'compose-editor', language: 'yaml', readonly: false },
        { id: 'env-editor', language: 'plaintext', readonly: false },
        { id: 'config-editor', language: 'yaml', readonly: false },
        { id: 'state-viewer', language: 'yaml', readonly: true }
    ];

    // Check if any editor elements exist
    const hasEditors = editorConfigs.some(({ id }) => document.getElementById(id));
    if (!hasEditors) return;

    // Load Monaco and create editors
    loadMonaco(() => {
        editorConfigs.forEach(({ id, language, readonly }) => {
            const el = document.getElementById(id);
            if (!el) return;

            const content = el.dataset.content || '';
            editors[id] = createEditor(el, content, language, readonly);
            if (!readonly) {
                editors[id].saveUrl = el.dataset.saveUrl;
            }
        });
    });
}

/**
 * Save all editors
 */
async function saveAllEditors() {
    const saveBtn = document.getElementById('save-btn') || document.getElementById('save-config-btn');
    const results = [];

    for (const [id, editor] of Object.entries(editors)) {
        if (!editor || !editor.saveUrl) continue;

        const content = editor.getValue();
        try {
            const response = await fetch(editor.saveUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'text/plain' },
                body: content
            });
            const data = await response.json();
            if (!response.ok || !data.success) {
                results.push({ id, success: false, error: data.detail || 'Unknown error' });
            } else {
                results.push({ id, success: true });
            }
        } catch (e) {
            results.push({ id, success: false, error: e.message });
        }
    }

    // Show result
    if (saveBtn && results.length > 0) {
        saveBtn.textContent = 'Saved!';
        setTimeout(() => saveBtn.textContent = saveBtn.id === 'save-config-btn' ? 'Save Config' : 'Save All', 2000);
        refreshDashboard();
    }
}

/**
 * Initialize save button handler
 */
function initSaveButton() {
    const saveBtn = document.getElementById('save-btn') || document.getElementById('save-config-btn');
    if (!saveBtn) return;

    saveBtn.onclick = saveAllEditors;
}

/**
 * Global keyboard shortcut handler
 */
function initKeyboardShortcuts() {
    document.addEventListener('keydown', function(e) {
        // Command+S (Mac) or Ctrl+S (Windows/Linux)
        if ((e.metaKey || e.ctrlKey) && e.key === 's') {
            // Only handle if we have editors and no Monaco editor is focused
            if (Object.keys(editors).length > 0) {
                // Check if any Monaco editor is focused
                const focusedEditor = Object.values(editors).find(ed => ed && ed.hasTextFocus && ed.hasTextFocus());
                if (!focusedEditor) {
                    e.preventDefault();
                    saveAllEditors();
                }
            }
        }
    });
}

/**
 * Initialize page components
 */
function initPage() {
    initMonacoEditors();
    initSaveButton();
}

/**
 * Attempt to reconnect to an active task from localStorage
 */
function tryReconnectToTask() {
    const taskId = localStorage.getItem(getTaskKey());
    if (!taskId) return;

    // Wait for xterm to be loaded
    const tryInit = (attempts) => {
        if (typeof Terminal !== 'undefined' && typeof FitAddon !== 'undefined') {
            expandTerminal();
            initTerminal('terminal-output', taskId);
        } else if (attempts > 0) {
            setTimeout(() => tryInit(attempts - 1), 100);
        }
    };
    tryInit(20);
}

// Play intro animation on command palette button
function playFabIntro() {
    const fab = document.getElementById('cmd-fab');
    if (!fab) return;
    setTimeout(() => {
        fab.style.setProperty('--cmd-pos', '0');
        fab.style.setProperty('--cmd-opacity', '1');
        fab.style.setProperty('--cmd-blur', '30');
        setTimeout(() => {
            fab.style.removeProperty('--cmd-pos');
            fab.style.removeProperty('--cmd-opacity');
            fab.style.removeProperty('--cmd-blur');
        }, 3000);
    }, 500);
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initPage();
    initKeyboardShortcuts();
    playFabIntro();

    // Try to reconnect to any active task
    tryReconnectToTask();

    // Handle ?action= parameter (from command palette navigation)
    const params = new URLSearchParams(window.location.search);
    const action = params.get('action');
    if (action && window.location.pathname === '/') {
        // Clear the URL parameter
        history.replaceState({}, '', '/');
        // Trigger the action
        htmx.ajax('POST', `/api/${action}`, {swap: 'none'});
    }
});

// Re-initialize after HTMX swaps main content
document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'main-content') {
        initPage();
        // Try to reconnect when navigating back to dashboard
        tryReconnectToTask();
    }
});

/**
 * Expand terminal collapse and scroll to it
 */
function expandTerminal() {
    const toggle = document.getElementById('terminal-toggle');
    if (toggle) toggle.checked = true;

    const collapse = document.getElementById('terminal-collapse');
    if (collapse) {
        collapse.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

/**
 * Show/hide terminal loading spinner
 */
function setTerminalLoading(loading) {
    const spinner = document.getElementById('terminal-spinner');
    if (spinner) {
        spinner.classList.toggle('hidden', !loading);
    }
}

// Handle action responses (terminal streaming)
document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (!evt.detail.successful || !evt.detail.xhr) return;

    const text = evt.detail.xhr.responseText;
    // Only try to parse if it looks like JSON (starts with {)
    if (!text || !text.trim().startsWith('{')) return;

    try {
        const response = JSON.parse(text);
        if (response.task_id) {
            // Expand terminal and scroll to it
            expandTerminal();

            // Wait for xterm to be loaded if needed
            const tryInit = (attempts) => {
                if (typeof Terminal !== 'undefined' && typeof FitAddon !== 'undefined') {
                    initTerminal('terminal-output', response.task_id);
                } else if (attempts > 0) {
                    setTimeout(() => tryInit(attempts - 1), 100);
                } else {
                    console.error('xterm.js failed to load');
                }
            };
            tryInit(20); // Try for up to 2 seconds
        }
    } catch (e) {
        // Not valid JSON, ignore
    }
});

// Command Palette
(function() {
    const dialog = document.getElementById('cmd-palette');
    const input = document.getElementById('cmd-input');
    const list = document.getElementById('cmd-list');
    const fab = document.getElementById('cmd-fab');
    if (!dialog || !input || !list) return;

    // Load icons from template (rendered server-side from icons.html)
    const iconTemplate = document.getElementById('cmd-icons');
    const icons = {};
    if (iconTemplate) {
        iconTemplate.content.querySelectorAll('[data-icon]').forEach(el => {
            icons[el.dataset.icon] = el.innerHTML;
        });
    }

    const colors = { service: '#22c55e', action: '#eab308', nav: '#3b82f6', app: '#a855f7' };
    let commands = [];
    let filtered = [];
    let selected = 0;

    const post = (url) => () => htmx.ajax('POST', url, {swap: 'none'});
    const nav = (url) => () => window.location.href = url;
    // Navigate to dashboard and trigger action (or just POST if already on dashboard)
    const dashboardAction = (endpoint) => () => {
        if (window.location.pathname === '/') {
            htmx.ajax('POST', `/api/${endpoint}`, {swap: 'none'});
        } else {
            window.location.href = `/?action=${endpoint}`;
        }
    };
    const cmd = (type, name, desc, action, icon = null) => ({ type, name, desc, action, icon });

    function buildCommands() {
        const actions = [
            cmd('action', 'Apply', 'Make reality match config', dashboardAction('apply'), icons.check),
            cmd('action', 'Refresh', 'Update state from reality', dashboardAction('refresh'), icons.refresh_cw),
            cmd('app', 'Dashboard', 'Go to dashboard', nav('/'), icons.home),
            cmd('app', 'Console', 'Go to console', nav('/console'), icons.terminal),
        ];

        // Add service-specific actions if on a service page
        const match = window.location.pathname.match(/^\/service\/(.+)$/);
        if (match) {
            const svc = decodeURIComponent(match[1]);
            const svcCmd = (name, desc, endpoint, icon) => cmd('service', name, `${desc} ${svc}`, post(`/api/service/${svc}/${endpoint}`), icon);
            actions.unshift(
                svcCmd('Up', 'Start', 'up', icons.play),
                svcCmd('Down', 'Stop', 'down', icons.square),
                svcCmd('Restart', 'Restart', 'restart', icons.rotate_cw),
                svcCmd('Pull', 'Pull', 'pull', icons.cloud_download),
                svcCmd('Update', 'Pull + restart', 'update', icons.refresh_cw),
                svcCmd('Logs', 'View logs for', 'logs', icons.file_text),
            );
        }

        // Add nav commands for all services from sidebar
        const services = [...document.querySelectorAll('#sidebar-services li[data-svc] a[href]')].map(a => {
            const name = a.getAttribute('href').replace('/service/', '');
            return cmd('nav', name, 'Go to service', nav(`/service/${name}`), icons.box);
        });

        commands = [...actions, ...services];
    }

    function filter() {
        const q = input.value.toLowerCase();
        filtered = commands.filter(c => c.name.toLowerCase().includes(q));
        selected = Math.max(0, Math.min(selected, filtered.length - 1));
    }

    function render() {
        list.innerHTML = filtered.map((c, i) => `
            <a class="flex justify-between items-center px-3 py-2 rounded-r cursor-pointer hover:bg-base-200 border-l-4 ${i === selected ? 'bg-base-300' : ''}" style="border-left-color: ${colors[c.type] || '#666'}" data-idx="${i}">
                <span class="flex items-center gap-2">${c.icon || ''}<span>${c.name}</span></span>
                <span class="opacity-40 text-xs">${c.desc}</span>
            </a>
        `).join('') || '<div class="opacity-50 p-2">No matches</div>';
        // Scroll selected item into view
        const sel = list.querySelector(`[data-idx="${selected}"]`);
        if (sel) sel.scrollIntoView({ block: 'nearest' });
    }

    function open() {
        buildCommands();
        selected = 0;
        input.value = '';
        filter();
        render();
        dialog.showModal();
        input.focus();
    }

    function exec() {
        if (filtered[selected]) {
            dialog.close();
            filtered[selected].action();
        }
    }

    // Keyboard: Cmd+K to open
    document.addEventListener('keydown', e => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            open();
        }
    });

    // Input filtering
    input.addEventListener('input', () => { filter(); render(); });

    // Keyboard nav inside palette
    dialog.addEventListener('keydown', e => {
        if (!dialog.open) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); selected = Math.min(selected + 1, filtered.length - 1); render(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); selected = Math.max(selected - 1, 0); render(); }
        else if (e.key === 'Enter') { e.preventDefault(); exec(); }
    });

    // Click to execute
    list.addEventListener('click', e => {
        const a = e.target.closest('a[data-idx]');
        if (a) {
            selected = parseInt(a.dataset.idx, 10);
            exec();
        }
    });

    // FAB click to open
    if (fab) fab.addEventListener('click', open);
})();
