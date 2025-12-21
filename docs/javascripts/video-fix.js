// Fix Safari video autoplay issues
(function() {
  function initVideos() {
    document.querySelectorAll('video[autoplay]').forEach(function(video) {
      video.load();
      video.play().catch(function() {});
    });
  }

  // For initial page load (needed for Chrome)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVideos);
  } else {
    initVideos();
  }

  // For MkDocs instant navigation (needed for Safari)
  if (typeof document$ !== 'undefined') {
    document$.subscribe(initVideos);
  }
})();
