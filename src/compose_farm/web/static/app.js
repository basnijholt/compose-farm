/**
 * Compose Farm Web UI JavaScript
 */

// ============================================================================
// CONSTANTS
// ============================================================================

// ANSI escape codes for terminal output
const ANSI = {
    RED: '\x1b[31m',
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

    // Unhide the terminal container first, then expand/scroll
    containerEl.classList.remove('hidden');
    expandCollapse(document.getElementById('exec-collapse'), containerEl);

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
 * Expand a collapse component and scroll to a target element
 * @param {HTMLInputElement} toggle - The checkbox input that controls the collapse
 * @param {HTMLElement} [scrollTarget] - Element to scroll to (defaults to collapse container)
 */
function expandCollapse(toggle, scrollTarget = null) {
    if (!toggle) return;

    // Find the parent collapse container
    const collapse = toggle.closest('.collapse');
    if (!collapse) return;

    const target = scrollTarget || collapse;
    const scrollToTarget = () => {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    if (!toggle.checked) {
        // Collapsed - expand first, then scroll after transition
        const onTransitionEnd = () => {
            collapse.removeEventListener('transitionend', onTransitionEnd);
            scrollToTarget();
        };
        collapse.addEventListener('transitionend', onTransitionEnd);
        toggle.checked = true;
    } else {
        // Already expanded - just scroll
        scrollToTarget();
    }
}

/**
 * Expand terminal collapse and scroll to it
 */
function expandTerminal() {
    expandCollapse(document.getElementById('terminal-toggle'));
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
    const saveBtn = getSaveButton();
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
    const saveBtn = getSaveButton();
    if (!saveBtn) return;

    saveBtn.onclick = saveAllEditors;
}

function getSaveButton() {
    return document.getElementById('save-btn') || document.getElementById('save-config-btn');
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
            window.scrollTo(0, 0);
            afterNav?.();
        });
    };
    // Navigate to dashboard (if needed) and trigger action
    const dashboardAction = (endpoint) => async () => {
        if (window.location.pathname !== '/') {
            await htmx.ajax('GET', '/', {target: '#main-content', select: '#main-content', swap: 'outerHTML'});
            history.pushState({}, '', '/');
            window.scrollTo(0, 0);
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
            cmd('app', 'Live Stats', 'View all containers across hosts', nav('/live-stats'), icons.box),
            cmd('app', 'Console', 'Go to console', nav('/console'), icons.terminal),
            cmd('app', 'Edit Config', 'Edit compose-farm.yaml', nav('/console#editor'), icons.file_code),
            cmd('app', 'Docs', 'Open documentation', openExternal('https://compose-farm.nijho.lt/'), icons.book_open),
            cmd('app', 'GitHub Repo', 'Open GitHub repository', openExternal('https://github.com/basnijholt/compose-farm'), icons.external_link),
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
    initLiveStats();
    initSharedActionMenu();
    maybeRunStackAction();
}

function navigateToStack(stack, action = null) {
    const url = action ? `/stack/${stack}?action=${action}` : `/stack/${stack}`;
    window.location.href = url;
}

/**
 * Initialize shared action menu for container rows
 */
function initSharedActionMenu() {
    const menuEl = document.getElementById('shared-action-menu');
    if (!menuEl) return;
    if (menuEl.dataset.bound === '1') return;
    menuEl.dataset.bound = '1';

    let hoverTimeout = null;

    function showMenuForButton(btn, stack) {
        menuEl.dataset.stack = stack;

        // Position menu relative to button
        const rect = btn.getBoundingClientRect();
        menuEl.classList.remove('hidden');
        menuEl.style.visibility = 'hidden';
        const menuRect = menuEl.getBoundingClientRect();

        const left = rect.right - menuRect.width + window.scrollX;
        const top = rect.bottom + window.scrollY;

        menuEl.style.top = `${top}px`;
        menuEl.style.left = `${left}px`;
        menuEl.style.visibility = '';

        if (typeof liveStats !== 'undefined') liveStats.dropdownOpen = true;
    }

    function closeMenu() {
        menuEl.classList.add('hidden');
        if (typeof liveStats !== 'undefined') liveStats.dropdownOpen = false;
        menuEl.dataset.stack = '';
    }

    function scheduleClose() {
        if (hoverTimeout) clearTimeout(hoverTimeout);
        hoverTimeout = setTimeout(closeMenu, 100);
    }

    function cancelClose() {
        if (hoverTimeout) {
            clearTimeout(hoverTimeout);
            hoverTimeout = null;
        }
    }

    // Button hover: show menu (event delegation on tbody)
    const tbody = document.getElementById('container-rows');
    if (tbody) {
        tbody.addEventListener('mouseenter', (e) => {
            const btn = e.target.closest('button[onclick^="openActionMenu"]');
            if (!btn) return;

            // Extract stack from onclick attribute
            const match = btn.getAttribute('onclick')?.match(/openActionMenu\(event,\s*'([^']+)'\)/);
            if (!match) return;

            cancelClose();
            showMenuForButton(btn, match[1]);
        }, true);

        tbody.addEventListener('mouseleave', (e) => {
            const btn = e.target.closest('button[onclick^="openActionMenu"]');
            if (btn) scheduleClose();
        }, true);
    }

    // Keep menu open while hovering over it
    menuEl.addEventListener('mouseenter', cancelClose);
    menuEl.addEventListener('mouseleave', scheduleClose);

    // Click action in menu
    menuEl.addEventListener('click', (e) => {
        const link = e.target.closest('a[data-action]');
        const stack = menuEl.dataset.stack;
        if (!link || !stack) return;

        e.preventDefault();
        navigateToStack(stack, link.dataset.action);
        closeMenu();
    });

    // Also support click on button (for touch/accessibility)
    window.openActionMenu = function(event, stack) {
        event.stopPropagation();
        showMenuForButton(event.currentTarget, stack);
    };

    // Close on outside click
    document.body.addEventListener('click', (e) => {
        if (!menuEl.classList.contains('hidden') &&
            !menuEl.contains(e.target) &&
            !e.target.closest('button[onclick^="openActionMenu"]')) {
            closeMenu();
        }
    });

    // Close on Escape
    document.body.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeMenu();
    });
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

function maybeRunStackAction() {
    const params = new URLSearchParams(window.location.search);
    const stackEl = document.querySelector('[data-stack-name]');
    const stackName = stackEl?.dataset?.stackName;
    if (!stackName) return;

    const action = params.get('action');
    if (!action) return;

    const button = document.querySelector(`button[hx-post="/api/stack/${stackName}/${action}"]`);
    if (!button) return;

    params.delete('action');
    const newQuery = params.toString();
    const newUrl = newQuery ? `${window.location.pathname}?${newQuery}` : window.location.pathname;
    history.replaceState({}, '', newUrl);

    if (window.htmx) {
        htmx.trigger(button, 'click');
    } else {
        button.click();
    }
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

// ============================================================================
// LIVE STATS PAGE
// ============================================================================

// State persists across SPA navigation (intervals must be cleared on re-init)
let liveStats = {
    sortCol: 9,
    sortAsc: false,
    lastUpdate: 0,
    dropdownOpen: false,
    scrolling: false,
    scrollTimer: null,
    loadingHosts: new Set(),
    eventsBound: false,
    intervals: [],
    updateCheckTimes: new Map(),
    autoRefresh: true
};

const REFRESH_INTERVAL = 5000;
const UPDATE_CHECK_TTL = 120000;
const NUMERIC_COLS = new Set([8, 9, 10, 11]);  // uptime, cpu, mem, net

function filterTable() {
    const textFilter = document.getElementById('filter-input')?.value.toLowerCase() || '';
    const hostFilter = document.getElementById('host-filter')?.value || '';
    const rows = document.querySelectorAll('#container-rows tr');
    let visible = 0;
    let total = 0;

    rows.forEach(row => {
        // Skip loading/empty/error rows (they have colspan)
        if (row.cells[0]?.colSpan > 1) return;
        total++;
        const matchesText = !textFilter || row.textContent.toLowerCase().includes(textFilter);
        const matchesHost = !hostFilter || row.dataset.host === hostFilter;
        const show = matchesText && matchesHost;
        row.style.display = show ? '' : 'none';
        if (show) visible++;
    });

    const countEl = document.getElementById('container-count');
    if (countEl) {
        const isFiltering = textFilter || hostFilter;
        countEl.textContent = total > 0
            ? (isFiltering ? `${visible} of ${total} containers` : `${total} containers`)
            : '';
    }
}
window.filterTable = filterTable;

function sortTable(col) {
    if (liveStats.sortCol === col) {
        liveStats.sortAsc = !liveStats.sortAsc;
    } else {
        liveStats.sortCol = col;
        liveStats.sortAsc = false;
    }
    updateSortIndicators();
    doSort();
}
window.sortTable = sortTable;

function updateSortIndicators() {
    document.querySelectorAll('thead th').forEach((th, i) => {
        const span = th.querySelector('.sort-indicator');
        if (span) {
            span.textContent = (i === liveStats.sortCol) ? (liveStats.sortAsc ? '↑' : '↓') : '';
            span.style.opacity = (i === liveStats.sortCol) ? '1' : '0.3';
        }
    });
}

function doSort() {
    const tbody = document.getElementById('container-rows');
    if (!tbody) return;

    const rows = Array.from(tbody.querySelectorAll('tr'));
    if (rows.length === 0) return;
    if (rows.length === 1 && rows[0].cells[0]?.colSpan > 1) return;  // Empty state row

    const isNumeric = NUMERIC_COLS.has(liveStats.sortCol);
    rows.sort((a, b) => {
        // Pin placeholders/empty rows to the bottom
        const aLoading = a.classList.contains('loading-row') || a.classList.contains('host-empty') || a.cells[0]?.colSpan > 1;
        const bLoading = b.classList.contains('loading-row') || b.classList.contains('host-empty') || b.cells[0]?.colSpan > 1;
        if (aLoading && !bLoading) return 1;
        if (!aLoading && bLoading) return -1;
        if (aLoading && bLoading) return 0;

        const aVal = a.cells[liveStats.sortCol]?.dataset?.sort ?? '';
        const bVal = b.cells[liveStats.sortCol]?.dataset?.sort ?? '';
        const cmp = isNumeric ? aVal - bVal : aVal.localeCompare(bVal);
        return liveStats.sortAsc ? cmp : -cmp;
    });

    let index = 1;
    const fragment = document.createDocumentFragment();
    rows.forEach((row) => {
        if (row.cells.length > 1) {
            row.cells[0].textContent = index++;
        }
        fragment.appendChild(row);
    });
    tbody.appendChild(fragment);
}

function isLoading() {
    return liveStats.loadingHosts.size > 0;
}

function getLiveStatsHosts() {
    const tbody = document.getElementById('container-rows');
    if (!tbody) return [];
    const dataHosts = tbody.dataset.hosts || '';
    return dataHosts.split(',').map(h => h.trim()).filter(Boolean);
}

function buildHostRow(host, message, className) {
    return (
        `<tr class="${className}" data-host="${host}">` +
        `<td colspan="12" class="text-center py-2">` +
        `<span class="text-sm opacity-60">${message}</span>` +
        `</td></tr>`
    );
}

async function checkUpdatesForHost(host) {
    // Update checks always run - they only update small cells, not disruptive
    const last = liveStats.updateCheckTimes.get(host) || 0;
    if (Date.now() - last < UPDATE_CHECK_TTL) return;

    const cells = Array.from(
        document.querySelectorAll(`tr[data-host="${host}"] td.update-cell[data-image][data-tag]`)
    );
    if (cells.length === 0) return;

    const items = [];
    const seen = new Set();
    cells.forEach(cell => {
        const image = decodeURIComponent(cell.dataset.image || '');
        const tag = decodeURIComponent(cell.dataset.tag || '');
        const key = `${image}:${tag}`;
        if (!image || seen.has(key)) return;
        seen.add(key);
        items.push({ image, tag });
    });

    if (items.length === 0) return;

    try {
        const response = await fetch('/api/containers/check-updates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items })
        });
        if (!response.ok) return;
        const data = await response.json();
        const results = Array.isArray(data?.results) ? data.results : [];
        const htmlMap = new Map();
        results.forEach(result => {
            const key = `${result.image}:${result.tag}`;
            htmlMap.set(key, result.html);
        });

        cells.forEach(cell => {
            const image = decodeURIComponent(cell.dataset.image || '');
            const tag = decodeURIComponent(cell.dataset.tag || '');
            const key = `${image}:${tag}`;
            const html = htmlMap.get(key);
            if (html && cell.innerHTML !== html) {
                cell.innerHTML = html;
            }
        });

        liveStats.updateCheckTimes.set(host, Date.now());
    } catch (e) {
        console.error('Update check failed:', e);
    }
}

function replaceHostRows(host, html) {
    const tbody = document.getElementById('container-rows');
    if (!tbody) return;

    // Remove loading indicator for this host if present
    const loadingRow = tbody.querySelector(`tr.loading-row[data-host="${host}"]`);
    if (loadingRow) loadingRow.remove();

    const template = document.createElement('template');
    template.innerHTML = html.trim();
    let newRows = Array.from(template.content.children).filter(el => el.tagName === 'TR');

    if (newRows.length === 0) {
        // Only show empty message if we don't have any rows for this host
        const existing = tbody.querySelector(`tr[data-host="${host}"]:not(.loading-row)`);
        if (!existing) {
            template.innerHTML = buildHostRow(host, `No containers on ${host}`, 'host-empty');
            newRows = Array.from(template.content.children);
        }
    }

    // Track which IDs we've seen in this update
    const newIds = new Set();

    newRows.forEach(newRow => {
        const id = newRow.id;
        if (id) newIds.add(id);

        if (id) {
            const existing = document.getElementById(id);
            if (existing) {
                // Morph in place if Idiomorph is available, otherwise replace
                if (typeof Idiomorph !== 'undefined') {
                    Idiomorph.morph(existing, newRow);
                } else {
                    existing.replaceWith(newRow);
                }

                // Re-process HTMX if needed (though inner content usually carries attributes)
                const morphedRow = document.getElementById(id);
                if (window.htmx) htmx.process(morphedRow);

                // Trigger refresh animation
                if (morphedRow) {
                    morphedRow.classList.add('row-updated');
                    setTimeout(() => morphedRow.classList.remove('row-updated'), 500);
                }
            } else {
                // New row - append (will be sorted later)
                tbody.appendChild(newRow);
                if (window.htmx) htmx.process(newRow);
                // Animate new rows too
                newRow.classList.add('row-updated');
                setTimeout(() => newRow.classList.remove('row-updated'), 500);
            }
        } else {
            // Fallback for rows without ID (like error/empty messages)
            // Just append them, cleaning up previous generic rows handled below
            tbody.appendChild(newRow);
        }
    });

    // Remove orphaned rows for this host (rows that exist in DOM but not in new response)
    // Be careful not to remove rows that were just added (if they lack IDs)
    const currentHostRows = Array.from(tbody.querySelectorAll(`tr[data-host="${host}"]`));
    currentHostRows.forEach(row => {
        // Skip if it's one of the new rows we just appended (check presence in newRows?)
        // Actually, if we just appended it, it is in DOM.
        // We rely on ID matching.
        // Error/Empty rows usually don't have ID, but we handle them by clearing old ones?
        // Let's assume data rows have IDs.
        if (row.id && !newIds.has(row.id)) {
            row.remove();
        }
        // Also remove old empty/error messages if we now have data
        if (!row.id && newRows.length > 0 && newRows[0].id) {
             row.remove();
        }
    });

    liveStats.loadingHosts.delete(host);
    checkUpdatesForHost(host);
    scheduleRowUpdate();
}

async function loadHostRows(host) {
    liveStats.loadingHosts.add(host);
    try {
        const response = await fetch(`/api/containers/rows/${encodeURIComponent(host)}`);
        const html = response.ok ? await response.text() : '';
        replaceHostRows(host, html);
    } catch (e) {
        console.error(`Failed to load ${host}:`, e);
        const msg = e.message || String(e);
        // Fallback to simpler error display if replaceHostRows fails (e.g. Idiomorph missing)
        try {
            replaceHostRows(host, buildHostRow(host, `Error: ${msg}`, 'text-error'));
        } catch (err2) {
            // Last resort: find row and force innerHTML
            const tbody = document.getElementById('container-rows');
            const row = tbody?.querySelector(`tr[data-host="${host}"]`);
            if (row) row.innerHTML = `<td colspan="12" class="text-center text-error">Error: ${msg}</td>`;
        }
    } finally {
        liveStats.loadingHosts.delete(host);
    }
}

function refreshLiveStats() {
    if (liveStats.dropdownOpen || liveStats.scrolling) return;
    const hosts = getLiveStatsHosts();
    if (hosts.length === 0) return;
    liveStats.lastUpdate = Date.now();
    hosts.forEach(loadHostRows);
}
window.refreshLiveStats = refreshLiveStats;

function toggleAutoRefresh() {
    liveStats.autoRefresh = !liveStats.autoRefresh;
    const timer = document.getElementById('refresh-timer');
    if (timer) {
        timer.classList.toggle('btn-error', !liveStats.autoRefresh);
        timer.classList.toggle('btn-outline', liveStats.autoRefresh);
    }
    if (liveStats.autoRefresh) {
        // Re-enabling: trigger immediate refresh
        refreshLiveStats();
    } else {
        // Disabling: ensure update checks run for current data
        const hosts = getLiveStatsHosts();
        hosts.forEach(host => checkUpdatesForHost(host));
    }
}
window.toggleAutoRefresh = toggleAutoRefresh;

function initLiveStats() {
    if (!document.getElementById('refresh-timer')) return;

    // Clear previous intervals (important for SPA navigation)
    liveStats.intervals.forEach(clearInterval);
    liveStats.intervals = [];
    liveStats.lastUpdate = Date.now();
    liveStats.dropdownOpen = false;
    liveStats.scrolling = false;
    if (liveStats.scrollTimer) clearTimeout(liveStats.scrollTimer);
    liveStats.scrollTimer = null;
    liveStats.loadingHosts.clear();
    liveStats.updateCheckTimes = new Map();
    liveStats.autoRefresh = true;

    if (!liveStats.eventsBound) {
        liveStats.eventsBound = true;

        // Dropdown pauses refresh
        document.body.addEventListener('click', e => {
            liveStats.dropdownOpen = !!e.target.closest('.dropdown');
        });
        document.body.addEventListener('focusin', e => {
            if (e.target.closest('.dropdown')) liveStats.dropdownOpen = true;
        });
        document.body.addEventListener('focusout', () => {
            setTimeout(() => {
                liveStats.dropdownOpen = !!document.activeElement?.closest('.dropdown');
            }, 150);
        });
        document.body.addEventListener('keydown', e => {
            if (e.key === 'Escape') liveStats.dropdownOpen = false;
        });

        // Pause refresh while scrolling (helps on slow mobile browsers)
        window.addEventListener('scroll', () => {
            liveStats.scrolling = true;
            if (liveStats.scrollTimer) clearTimeout(liveStats.scrollTimer);
            liveStats.scrollTimer = setTimeout(() => {
                liveStats.scrolling = false;
            }, 200);
        }, { passive: true });
    }

    // Auto-refresh every 5 seconds (skip if disabled, loading, or dropdown open)
    liveStats.intervals.push(setInterval(() => {
        if (!liveStats.autoRefresh) return;
        if (liveStats.dropdownOpen || liveStats.scrolling || isLoading()) return;
        refreshLiveStats();
    }, REFRESH_INTERVAL));

    // Timer display (updates every 100ms)
    liveStats.intervals.push(setInterval(() => {
        const timer = document.getElementById('refresh-timer');
        if (!timer) {
            liveStats.intervals.forEach(clearInterval);
            return;
        }

        const loading = isLoading();
        const paused = liveStats.dropdownOpen || liveStats.scrolling;
        const elapsed = Date.now() - liveStats.lastUpdate;
        window.refreshPaused = paused || loading || !liveStats.autoRefresh;

        // Update refresh timer button
        let text;
        if (!liveStats.autoRefresh) {
            text = 'OFF';
        } else if (paused) {
            text = '❚❚';
        } else {
            const remaining = Math.max(0, REFRESH_INTERVAL - elapsed);
            text = loading ? '↻ …' : `↻ ${Math.ceil(remaining / 1000)}s`;
        }
        if (timer.textContent !== text) {
            timer.textContent = text;
        }

        // Update "last updated" display
        const lastUpdatedEl = document.getElementById('last-updated');
        if (lastUpdatedEl) {
            const secs = Math.floor(elapsed / 1000);
            const updatedText = secs < 5 ? 'Updated just now' : `Updated ${secs}s ago`;
            if (lastUpdatedEl.textContent !== updatedText) {
                lastUpdatedEl.textContent = updatedText;
            }
        }
    }, 100));

    updateSortIndicators();
    refreshLiveStats();
}

function scheduleRowUpdate() {
    // Sort and filter immediately to prevent flicker
    doSort();
    filterTable();
}

// ============================================================================
// STACKS BY HOST FILTER
// ============================================================================

function sbhFilter() {
    const query = (document.getElementById('sbh-filter')?.value || '').toLowerCase();
    const hostFilter = document.getElementById('sbh-host-select')?.value || '';

    document.querySelectorAll('.sbh-group').forEach(group => {
        if (hostFilter && group.dataset.h !== hostFilter) {
            group.hidden = true;
            return;
        }

        let visibleCount = 0;
        group.querySelectorAll('li[data-s]').forEach(li => {
            const show = !query || li.dataset.s.includes(query);
            li.hidden = !show;
            if (show) visibleCount++;
        });
        group.hidden = visibleCount === 0;
    });
}
window.sbhFilter = sbhFilter;
