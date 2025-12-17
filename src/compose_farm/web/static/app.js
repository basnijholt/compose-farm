/**
 * Compose Farm Web UI JavaScript
 */

// Store active terminals
const terminals = {};

/**
 * Initialize a terminal and connect to WebSocket for streaming
 * @param {string} elementId - ID of the container element
 * @param {string} taskId - Task ID to connect to
 */
function initTerminal(elementId, taskId) {
    const container = document.getElementById(elementId);
    if (!container) {
        console.error('Terminal container not found:', elementId);
        return;
    }

    // Clear existing terminal
    container.innerHTML = '';

    // Create terminal
    const term = new Terminal({
        convertEol: true,
        theme: {
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
        },
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

    // Handle window resize
    window.addEventListener('resize', () => fitAddon.fit());

    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/terminal/${taskId}`);

    ws.onopen = () => {
        term.write('\x1b[2m[Connected]\x1b[0m\r\n');
    };

    ws.onmessage = (event) => {
        term.write(event.data);
    };

    ws.onclose = () => {
        // Terminal already shows [Done] or [Failed] from server
    };

    ws.onerror = (error) => {
        term.write('\x1b[31m[WebSocket Error]\x1b[0m\r\n');
        console.error('WebSocket error:', error);
    };

    // Store reference
    terminals[taskId] = { term, ws, fitAddon };

    return { term, ws };
}

// Export for global use
window.initTerminal = initTerminal;
