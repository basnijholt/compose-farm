// Fix Safari video autoplay issues on first page load
(function() {
  function initVideos() {
    document.querySelectorAll('video[autoplay]').forEach(function(v) {
      v.load();
      v.play().catch(function() {});
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVideos);
  } else {
    initVideos();
  }
})();
