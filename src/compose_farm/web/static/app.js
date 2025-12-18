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

    ws.onopen = () => {
        term.write(`${ANSI.DIM}[Connected]${ANSI.RESET}${ANSI.CRLF}`);
        setTerminalLoading(true);
    };
    ws.onmessage = (event) => term.write(event.data);
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
 * @param {boolean} readonly - Whether editor is read-only
 * @returns {object} Monaco editor instance
 */
function createEditor(container, content, language, readonly = false) {
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
            saveAllEditors();
        });
    }

    return editor;
}

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
            if (!data.success) {
                results.push({ id, success: false, error: data.detail || 'Unknown error' });
            } else {
                results.push({ id, success: true });
            }
        } catch (e) {
            results.push({ id, success: false, error: e.message });
        }
    }

    // Show result
    const errors = results.filter(r => !r.success);
    if (errors.length > 0) {
        alert('Errors saving:\n' + errors.map(e => `${e.id}: ${e.error}`).join('\n'));
    } else if (saveBtn && results.length > 0) {
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

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initPage();
    initKeyboardShortcuts();
});

// Re-initialize after HTMX swaps main content
document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'main-content') {
        initPage();
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
    if (!dialog || !input || !list) return;

    let commands = [];
    let selected = 0;

    const actions = [
        { type: 'action', name: 'Apply', desc: 'Make reality match config', action: () => htmx.ajax('POST', '/api/apply', {swap: 'none'}) },
        { type: 'action', name: 'Refresh', desc: 'Update state from reality', action: () => htmx.ajax('POST', '/api/refresh', {swap: 'none'}) },
        { type: 'nav', name: 'Dashboard', desc: 'Go to dashboard', action: () => window.location.href = '/' },
    ];

    function buildCommands() {
        const services = [...document.querySelectorAll('#sidebar-services li[data-svc]')].map(li => {
            const name = li.querySelector('a')?.textContent.trim();
            return { type: 'service', name, desc: 'Go to service', action: () => window.location.href = `/service/${name}` };
        });
        commands = [...actions, ...services];
    }

    function render(filter = '') {
        const q = filter.toLowerCase();
        const filtered = commands.filter(c => c.name.toLowerCase().includes(q));
        selected = Math.max(0, Math.min(selected, filtered.length - 1));

        list.innerHTML = filtered.map((c, i) => `
            <a class="flex justify-between items-center px-3 py-2 rounded cursor-pointer hover:bg-base-200 ${i === selected ? 'bg-base-300' : ''}" data-idx="${i}">
                <span><span class="opacity-50 text-xs mr-2">${c.type}</span>${c.name}</span>
                <span class="opacity-40 text-xs">${c.desc}</span>
            </a>
        `).join('') || '<div class="opacity-50 p-2">No matches</div>';
        return filtered;
    }

    function open() {
        buildCommands();
        selected = 0;
        input.value = '';
        render();
        dialog.showModal();
        input.focus();
    }

    function exec(filtered) {
        const cmd = filtered[selected];
        if (cmd) {
            dialog.close();
            cmd.action();
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
    input.addEventListener('input', () => render(input.value));

    // Keyboard nav inside palette (use dialog to catch all keys)
    dialog.addEventListener('keydown', e => {
        if (!dialog.open) return;
        const filtered = commands.filter(c => c.name.toLowerCase().includes(input.value.toLowerCase()));
        if (e.key === 'ArrowDown') { e.preventDefault(); selected = Math.min(selected + 1, filtered.length - 1); render(input.value); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); selected = Math.max(selected - 1, 0); render(input.value); }
        else if (e.key === 'Enter') { e.preventDefault(); exec(filtered); }
    });

    // Click to execute
    list.addEventListener('click', e => {
        const a = e.target.closest('a[data-idx]');
        if (a) {
            selected = parseInt(a.dataset.idx);
            exec(commands.filter(c => c.name.toLowerCase().includes(input.value.toLowerCase())));
        }
    });
})();
