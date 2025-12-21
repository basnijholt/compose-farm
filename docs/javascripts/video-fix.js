// Fix Safari video autoplay issues on first page load
(function() {
  function initVideos() {
    document.querySelectorAll('video[autoplay]').forEach(function(video) {
      video.load();
      video.play().catch(function() {});
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVideos);
  } else {
    initVideos();
  }

  if (typeof document$ !== 'undefined') {
    document$.subscribe(initVideos);
  }
})();
