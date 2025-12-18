/**
 * Compose Farm Web UI JavaScript
 */

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
 * Initialize a terminal and connect to WebSocket for streaming
 */
function initTerminal(elementId, taskId) {
    const container = document.getElementById(elementId);
    if (!container) {
        console.error('Terminal container not found:', elementId);
        return;
    }

    container.innerHTML = '';

    const term = new Terminal({
        convertEol: true,
        theme: TERMINAL_THEME,
        fontSize: 13,
        fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
        scrollback: 5000
    });

    // Fit addon
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);

    // Open terminal
    term.open(container);
    fitAddon.fit();

    // Handle window and container resize
    window.addEventListener('resize', () => fitAddon.fit());

    // Refit when container is manually resized (drag handle)
    const resizeObserver = new ResizeObserver(() => fitAddon.fit());
    resizeObserver.observe(container);

    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/terminal/${taskId}`);

    ws.onopen = () => {
        term.write('\x1b[2m[Connected]\x1b[0m\r\n');
        setTerminalLoading(true);
    };

    ws.onmessage = (event) => {
        term.write(event.data);
    };

    ws.onclose = () => {
        // Terminal already shows [Done] or [Failed] from server
        setTerminalLoading(false);
    };

    ws.onerror = (error) => {
        term.write('\x1b[31m[WebSocket Error]\x1b[0m\r\n');
        console.error('WebSocket error:', error);
        setTerminalLoading(false);
    };

    // Store reference
    terminals[taskId] = { term, ws, fitAddon };

    return { term, ws };
}

// Export for global use
window.initTerminal = initTerminal;

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
        setTimeout(() => saveBtn.textContent = saveBtn.id === 'save-config-btn' ? 'Save' : 'Save All', 2000);

        // Refresh config tables if on config page
        const configTables = document.getElementById('config-tables');
        if (configTables) {
            htmx.ajax('GET', '/partials/config-tables', {target: '#config-tables', swap: 'outerHTML'});
        }

        // Refresh sidebar to show updated services
        const sidebar = document.querySelector('#sidebar nav');
        if (sidebar) {
            htmx.ajax('GET', '/partials/sidebar', {target: '#sidebar nav', swap: 'innerHTML'});
        }
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
