/**
 * Compose Farm Web UI JavaScript
 */

// ============================================================================
// CONSTANTS
// ============================================================================

// ANSI escape codes for terminal output
const ANSI = {
    RED: '\x1b[31m',
    GREEN: '\x1b[32m',
    DIM: '\x1b[2m',
    RESET: '\x1b[0m',
    CRLF: '\r\n'
};

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

// Detect Mac for keyboard shortcut display
const IS_MAC = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
const MOD_KEY = IS_MAC ? '⌘' : 'Ctrl';

// ============================================================================
// STATE
// ============================================================================

// Store active terminals and editors
const terminals = {};
const editors = {};
let monacoLoaded = false;
let monacoLoading = false;

// LocalStorage key prefix for active tasks (scoped by page)
const TASK_KEY_PREFIX = 'cf_task:';
const getTaskKey = () => TASK_KEY_PREFIX + window.location.pathname;

// Exec terminal state
let execTerminalWrapper = null;  // {term, dispose}
let execWs = null;

// ============================================================================
// UTILITIES
// ============================================================================

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
 * Wait for xterm.js to load, then execute callback
 * @param {function} callback - Function to call when xterm is ready
 * @param {number} maxAttempts - Max attempts (default 20 = 2 seconds)
 */
function whenXtermReady(callback, maxAttempts = 20) {
    const tryInit = (attempts) => {
        if (typeof Terminal !== 'undefined' && typeof FitAddon !== 'undefined') {
            callback();
        } else if (attempts > 0) {
            setTimeout(() => tryInit(attempts - 1), 100);
        } else {
            console.error('xterm.js failed to load');
        }
    };
    tryInit(maxAttempts);
}
window.whenXtermReady = whenXtermReady;

// ============================================================================
// TERMINAL
// ============================================================================

/**
 * Create a terminal with fit addon and resize observer
 * @param {HTMLElement} container - Container element
 * @param {object} extraOptions - Additional terminal options
 * @param {function} onResize - Optional callback called with (cols, rows) after resize
 * @returns {{term: Terminal, fitAddon: FitAddon, dispose: function}}
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

    const handleResize = () => {
        fitAddon.fit();
        onResize?.(term.cols, term.rows);
    };

    // Use ResizeObserver only (handles both container and window resize)
    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(container);

    handleResize(); // Initial fit

    return {
        term,
        fitAddon,
        dispose() {
            resizeObserver.disconnect();
            term.dispose();
        }
    };
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

    const wrapper = createTerminal(container);
    const { term } = wrapper;
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

    terminals[taskId] = { ...wrapper, ws };
    return { term, ws };
}

window.initTerminal = initTerminal;

/**
 * Initialize an interactive exec terminal
 */
function initExecTerminal(stack, container, host) {
    const containerEl = document.getElementById('exec-terminal-container');
    const terminalEl = document.getElementById('exec-terminal');

    if (!containerEl || !terminalEl) {
        console.error('Exec terminal elements not found');
        return;
    }

    containerEl.classList.remove('hidden');

    // Clean up existing (use wrapper's dispose to clean up ResizeObserver)
    if (execWs) { execWs.close(); execWs = null; }
    if (execTerminalWrapper) { execTerminalWrapper.dispose(); execTerminalWrapper = null; }

    // Create WebSocket first so resize callback can use it
    execWs = createWebSocket(`/ws/exec/${stack}/${container}/${host}`);

    // Resize callback sends size to WebSocket
    const sendSize = (cols, rows) => {
        if (execWs && execWs.readyState === WebSocket.OPEN) {
            execWs.send(JSON.stringify({ type: 'resize', cols, rows }));
        }
    };

    execTerminalWrapper = createTerminal(terminalEl, { cursorBlink: true }, sendSize);
    const term = execTerminalWrapper.term;

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

// ============================================================================
// EDITOR (Monaco)
// ============================================================================

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
    const { readonly = false, onSave = null } = opts;

    const options = {
        value: content,
        language,
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
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
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
    Object.values(editors).forEach(ed => ed?.dispose?.());
    for (const key in editors) delete editors[key];

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
            editors[id] = createEditor(el, content, language, { readonly });
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

// ============================================================================
// UI HELPERS
// ============================================================================

/**
 * Refresh dashboard partials by dispatching a custom event.
 * Elements with hx-trigger="cf:refresh from:body" will automatically refresh.
 */
function refreshDashboard() {
    document.body.dispatchEvent(new CustomEvent('cf:refresh'));
}

/**
 * Filter sidebar stacks by name and host
 */
function sidebarFilter() {
    const q = (document.getElementById('sidebar-filter')?.value || '').toLowerCase();
    const h = document.getElementById('sidebar-host-select')?.value || '';
    let n = 0;
    document.querySelectorAll('#sidebar-stacks li').forEach(li => {
        const show = (!q || li.dataset.stack.includes(q)) && (!h || !li.dataset.h || li.dataset.h === h);
        li.hidden = !show;
        if (show) n++;
    });
    document.getElementById('sidebar-count').textContent = '(' + n + ')';
}
window.sidebarFilter = sidebarFilter;

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

// ============================================================================
// COMMAND PALETTE
// ============================================================================

(function() {
    const dialog = document.getElementById('cmd-palette');
    const input = document.getElementById('cmd-input');
    const list = document.getElementById('cmd-list');
    const fab = document.getElementById('cmd-fab');
    const themeBtn = document.getElementById('theme-btn');
    if (!dialog || !input || !list) return;

    // Load icons from template (rendered server-side from icons.html)
    const iconTemplate = document.getElementById('cmd-icons');
    const icons = {};
    if (iconTemplate) {
        iconTemplate.content.querySelectorAll('[data-icon]').forEach(el => {
            icons[el.dataset.icon] = el.innerHTML;
        });
    }

    // All available DaisyUI themes
    const THEMES = ['light', 'dark', 'cupcake', 'bumblebee', 'emerald', 'corporate', 'synthwave', 'retro', 'cyberpunk', 'valentine', 'halloween', 'garden', 'forest', 'aqua', 'lofi', 'pastel', 'fantasy', 'wireframe', 'black', 'luxury', 'dracula', 'cmyk', 'autumn', 'business', 'acid', 'lemonade', 'night', 'coffee', 'winter', 'dim', 'nord', 'sunset', 'caramellatte', 'abyss', 'silk'];
    const THEME_KEY = 'cf_theme';

    const colors = { stack: '#22c55e', action: '#eab308', nav: '#3b82f6', app: '#a855f7', theme: '#ec4899', service: '#14b8a6' };
    let commands = [];
    let filtered = [];
    let selected = 0;
    let originalTheme = null; // Store theme when palette opens for preview/restore

    const post = (url) => () => htmx.ajax('POST', url, {swap: 'none'});
    const nav = (url, afterNav) => () => {
        // Set hash before HTMX swap so inline scripts can read it
        const hashIndex = url.indexOf('#');
        if (hashIndex !== -1) {
            window.location.hash = url.substring(hashIndex);
        }
        htmx.ajax('GET', url, {target: '#main-content', select: '#main-content', swap: 'outerHTML'}).then(() => {
            history.pushState({}, '', url);
            afterNav?.();
        });
    };
    // Navigate to dashboard (if needed) and trigger action
    const dashboardAction = (endpoint) => async () => {
        if (window.location.pathname !== '/') {
            await htmx.ajax('GET', '/', {target: '#main-content', select: '#main-content', swap: 'outerHTML'});
            history.pushState({}, '', '/');
        }
        htmx.ajax('POST', `/api/${endpoint}`, {swap: 'none'});
    };
    // Apply theme and save to localStorage
    const setTheme = (theme) => () => {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(THEME_KEY, theme);
    };
    // Preview theme without saving (for hover)
    const previewTheme = (theme) => {
        document.documentElement.setAttribute('data-theme', theme);
    };
    // Restore original theme (when closing without selection)
    const restoreTheme = () => {
        if (originalTheme) {
            document.documentElement.setAttribute('data-theme', originalTheme);
        }
    };
    // Generate color swatch HTML for a theme
    const themeSwatch = (theme) => `<span class="flex gap-0.5" data-theme="${theme}"><span class="w-2 h-4 rounded-l bg-primary"></span><span class="w-2 h-4 bg-secondary"></span><span class="w-2 h-4 bg-accent"></span><span class="w-2 h-4 rounded-r bg-neutral"></span></span>`;

    const cmd = (type, name, desc, action, icon = null, themeId = null) => ({ type, name, desc, action, icon, themeId });

    // Reopen palette with theme filter
    const openThemePicker = () => {
        // Small delay to let dialog close before reopening
        setTimeout(() => open('theme:'), 50);
    };

    function buildCommands() {
        const openExternal = (url) => () => window.open(url, '_blank');

        const actions = [
            cmd('action', 'Apply', 'Make reality match config', dashboardAction('apply'), icons.check),
            cmd('action', 'Refresh', 'Update state from reality', dashboardAction('refresh'), icons.refresh_cw),
            cmd('action', 'Pull All', 'Pull latest images for all stacks', dashboardAction('pull-all'), icons.cloud_download),
            cmd('action', 'Update All', 'Update all stacks', dashboardAction('update-all'), icons.refresh_cw),
            cmd('app', 'Theme', 'Change color theme', openThemePicker, icons.palette),
            cmd('app', 'Dashboard', 'Go to dashboard', nav('/'), icons.home),
            cmd('app', 'Console', 'Go to console', nav('/console'), icons.terminal),
            cmd('app', 'Edit Config', 'Edit compose-farm.yaml', nav('/console#editor'), icons.file_code),
            cmd('app', 'Docs', 'Open documentation', openExternal('https://compose-farm.nijho.lt/'), icons.book_open),
        ];

        // Add stack-specific actions if on a stack page
        const match = window.location.pathname.match(/^\/stack\/(.+)$/);
        if (match) {
            const stack = decodeURIComponent(match[1]);
            const stackCmd = (name, desc, endpoint, icon) => cmd('stack', name, `${desc} ${stack}`, post(`/api/stack/${stack}/${endpoint}`), icon);
            actions.unshift(
                stackCmd('Up', 'Start', 'up', icons.play),
                stackCmd('Down', 'Stop', 'down', icons.square),
                stackCmd('Restart', 'Restart', 'restart', icons.rotate_cw),
                stackCmd('Pull', 'Pull', 'pull', icons.cloud_download),
                stackCmd('Update', 'Pull + restart', 'update', icons.refresh_cw),
                stackCmd('Logs', 'View logs for', 'logs', icons.file_text),
            );

            // Add Open Website commands if website URLs are available
            const websiteUrlsAttr = document.querySelector('[data-website-urls]')?.getAttribute('data-website-urls');
            if (websiteUrlsAttr) {
                const websiteUrls = JSON.parse(websiteUrlsAttr);
                for (const url of websiteUrls) {
                    const displayUrl = url.replace(/^https?:\/\//, '');
                    const label = websiteUrls.length > 1 ? `Open: ${displayUrl}` : 'Open Website';
                    actions.unshift(cmd('stack', label, `Open ${displayUrl} in browser`, openExternal(url), icons.external_link));
                }
            }

            // Add service-specific commands from data-services and data-containers attributes
            // Grouped by action (all Logs together, all Pull together, etc.) with services sorted alphabetically
            const servicesAttr = document.querySelector('[data-services]')?.getAttribute('data-services');
            const containersAttr = document.querySelector('[data-containers]')?.getAttribute('data-containers');
            if (servicesAttr) {
                const services = servicesAttr.split(',').filter(s => s).sort();
                // Parse container info for shell access: {service: {container, host}}
                const containers = containersAttr ? JSON.parse(containersAttr) : {};

                const svcCmd = (action, service, desc, endpoint, icon) =>
                    cmd('service', `${action}: ${service}`, desc, post(`/api/stack/${stack}/service/${service}/${endpoint}`), icon);
                const svcActions = [
                    ['Logs', 'View logs for service', 'logs', icons.file_text],
                    ['Pull', 'Pull image for service', 'pull', icons.cloud_download],
                    ['Restart', 'Restart service', 'restart', icons.rotate_cw],
                    ['Stop', 'Stop service', 'stop', icons.square],
                    ['Up', 'Start service', 'up', icons.play],
                ];
                for (const [action, desc, endpoint, icon] of svcActions) {
                    for (const service of services) {
                        actions.push(svcCmd(action, service, desc, endpoint, icon));
                    }
                }
                // Add Shell commands if container info is available
                for (const service of services) {
                    const info = containers[service];
                    if (info?.container && info?.host) {
                        actions.push(cmd('service', `Shell: ${service}`, 'Open interactive shell',
                            () => initExecTerminal(stack, info.container, info.host), icons.terminal));
                    }
                }
            }
        }

        // Add nav commands for all stacks from sidebar
        const stacks = [...document.querySelectorAll('#sidebar-stacks li[data-stack] a[href]')].map(a => {
            const name = a.getAttribute('href').replace('/stack/', '');
            return cmd('nav', name, 'Go to stack', nav(`/stack/${name}`), icons.box);
        });

        // Add theme commands with color swatches
        const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
        const themeCommands = THEMES.map(theme =>
            cmd('theme', `theme: ${theme}`, theme === currentTheme ? '(current)' : 'Switch theme', setTheme(theme), themeSwatch(theme), theme)
        );

        commands = [...actions, ...stacks, ...themeCommands];
    }

    function filter() {
        // Fuzzy matching: all query words must match the START of a word in the command name
        // Examples: "r ba" matches "Restart: bazarr" but NOT "Logs: bazarr"
        const q = input.value.toLowerCase().trim();
        // Split query into words and strip non-alphanumeric chars
        const queryWords = q.split(/[^a-z0-9]+/).filter(w => w);

        filtered = commands.filter(c => {
            const name = c.name.toLowerCase();
            // Split command name into words (split on non-alphanumeric)
            const nameWords = name.split(/[^a-z0-9]+/).filter(w => w);
            // Each query word must match the start of some word in the command name
            return queryWords.every(qw =>
                nameWords.some(nw => nw.startsWith(qw))
            );
        });
        selected = Math.max(0, Math.min(selected, filtered.length - 1));
    }

    function render() {
        list.innerHTML = filtered.map((c, i) => `
            <a class="flex justify-between items-center px-3 py-2 rounded-r cursor-pointer hover:bg-base-200 border-l-4 ${i === selected ? 'bg-base-300' : ''}" style="border-left-color: ${colors[c.type] || '#666'}" data-idx="${i}"${c.themeId ? ` data-theme-id="${c.themeId}"` : ''}>
                <span class="flex items-center gap-2">${c.icon || ''}<span>${c.name}</span></span>
                <span class="opacity-40 text-xs">${c.desc}</span>
            </a>
        `).join('') || '<div class="opacity-50 p-2">No matches</div>';
        // Scroll selected item into view
        const sel = list.querySelector(`[data-idx="${selected}"]`);
        if (sel) sel.scrollIntoView({ block: 'nearest' });
        // Preview theme if selected item is a theme command
        const selectedCmd = filtered[selected];
        if (selectedCmd?.themeId) {
            previewTheme(selectedCmd.themeId);
        } else if (originalTheme) {
            // Restore original when navigating away from theme commands
            previewTheme(originalTheme);
        }
    }

    function open(initialFilter = '') {
        // Store original theme for preview/restore
        originalTheme = document.documentElement.getAttribute('data-theme') || 'dark';
        buildCommands();
        selected = 0;
        input.value = initialFilter;
        filter();
        // If opening theme picker, select current theme
        if (initialFilter.startsWith('theme:')) {
            const currentIdx = filtered.findIndex(c => c.themeId === originalTheme);
            if (currentIdx >= 0) selected = currentIdx;
        }
        render();
        dialog.showModal();
        input.focus();
    }

    function close() {
        dialog.close();
        restoreTheme();
    }

    function exec() {
        const cmd = filtered[selected];
        if (cmd) {
            if (cmd.themeId) {
                // Theme command commits the previewed choice.
                originalTheme = null;
            }
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

    // Hover previews theme without changing selection
    list.addEventListener('mouseover', e => {
        const a = e.target.closest('a[data-theme-id]');
        if (a) previewTheme(a.dataset.themeId);
    });

    // Mouse leaving list restores to selected item's theme (or original)
    list.addEventListener('mouseleave', () => {
        const cmd = filtered[selected];
        previewTheme(cmd?.themeId || originalTheme);
    });

    // Restore theme when dialog closes without selection (Escape, backdrop click)
    dialog.addEventListener('close', () => {
        if (originalTheme) {
            restoreTheme();
            originalTheme = null;
        }
    });

    // FAB click to open
    if (fab) fab.addEventListener('click', () => open());

    // Theme button opens palette with "theme:" filter
    if (themeBtn) themeBtn.addEventListener('click', () => open('theme:'));
})();

// ============================================================================
// THEME PERSISTENCE
// ============================================================================

// Restore saved theme on load (also handled in inline script to prevent flash)
(function() {
    const saved = localStorage.getItem('cf_theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
})();

// ============================================================================
// INITIALIZATION
// ============================================================================

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
                const focusedEditor = Object.values(editors).find(ed => ed?.hasTextFocus?.());
                if (!focusedEditor) {
                    e.preventDefault();
                    saveAllEditors();
                }
            }
        }
    });
}

/**
 * Update keyboard shortcut display based on OS
 * Replaces ⌘ with Ctrl on non-Mac platforms
 */
function updateShortcutKeys() {
    // Update elements with class 'shortcut-key' that contain ⌘
    document.querySelectorAll('.shortcut-key').forEach(el => {
        if (el.textContent === '⌘') {
            el.textContent = MOD_KEY;
        }
    });
}

/**
 * Initialize page components
 */
function initPage() {
    initMonacoEditors();
    initSaveButton();
    updateShortcutKeys();
}

/**
 * Attempt to reconnect to an active task from localStorage
 * @param {string} [path] - Optional path to use for task key lookup.
 *                          If not provided, uses current window.location.pathname.
 *                          This is important for HTMX navigation where pushState
 *                          hasn't happened yet when htmx:afterSwap fires.
 */
function tryReconnectToTask(path) {
    const taskKey = TASK_KEY_PREFIX + (path || window.location.pathname);
    const taskId = localStorage.getItem(taskKey);
    if (!taskId) return;

    whenXtermReady(() => {
        expandTerminal();
        initTerminal('terminal-output', taskId);
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initPage();
    initKeyboardShortcuts();
    playFabIntro();

    // Try to reconnect to any active task
    tryReconnectToTask();
});

// Re-initialize after HTMX swaps main content
document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.target.id === 'main-content') {
        initPage();
        // Try to reconnect to task for the TARGET page, not current URL.
        // When using command palette navigation (htmx.ajax + manual pushState),
        // window.location.pathname still reflects the OLD page at this point.
        // Use pathInfo.requestPath to get the correct target path.
        const targetPath = evt.detail.pathInfo?.requestPath?.split('?')[0] || window.location.pathname;
        tryReconnectToTask(targetPath);
    }
});

// Handle action responses (terminal streaming)
document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (!evt.detail.successful || !evt.detail.xhr) return;

    const text = evt.detail.xhr.responseText;
    // Only try to parse if it looks like JSON (starts with {)
    if (!text || !text.trim().startsWith('{')) return;

    try {
        const response = JSON.parse(text);
        if (response.task_id) {
            expandTerminal();
            whenXtermReady(() => initTerminal('terminal-output', response.task_id));
        }
    } catch (e) {
        // Not valid JSON, ignore
    }
});
