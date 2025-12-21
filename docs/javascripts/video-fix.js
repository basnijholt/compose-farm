// Fix Safari video autoplay on first page load
document.querySelectorAll('video[autoplay]').forEach(v => { v.load(); v.play().catch(() => {}); });
