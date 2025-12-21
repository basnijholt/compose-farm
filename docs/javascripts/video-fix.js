// Fix Safari video autoplay issues on first page load
// Safari sometimes fails to initialize videos properly until a refresh
(function() {
  function initVideos() {
    document.querySelectorAll('video[autoplay]').forEach(function(video) {
      // Force Safari to reload and play the video
      video.load();
      video.play().catch(function() {
        // Autoplay was prevented, ignore
      });
    });
  }

  // Run on initial page load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVideos);
  } else {
    initVideos();
  }

  // Also run after instant navigation (MkDocs Material / Zensical)
  if (typeof document$ !== 'undefined') {
    document$.subscribe(initVideos);
  }
})();
