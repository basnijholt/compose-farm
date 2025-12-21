# Safari Video Autoplay Issue

## Problem

Videos on the documentation site don't show up on the first page load in Safari (both iOS and macOS). After refreshing the page, the videos appear correctly. This happens consistently and only in Safari, not in other browsers like Brave or Chrome.

## What We've Tried

### 1. `#t=0.001` time fragment (PR #82 - merged)
Added `#t=0.001` to video source URLs to force Safari to preload the first frame:
```html
<source src="/assets/quickstart.webm#t=0.001" type="video/webm">
```
**Result:** Did not fix the issue alone.

### 2. JavaScript `video.play()` only
```js
document.querySelectorAll('video[autoplay]').forEach(v => v.play().catch(() => {}));
```
**Result:** Did not work.

### 3. JavaScript `video.load()` only
```js
document.querySelectorAll('video[autoplay]').forEach(v => v.load());
```
**Result:** Did not work.

### 4. JavaScript `video.load()` + `video.play()` (one-liner)
```js
document.querySelectorAll('video[autoplay]').forEach(v => { v.load(); v.play().catch(() => {}); });
```
**Result:** Not yet tested.

### 5. Full JavaScript with DOMContentLoaded handling (WORKED)
```js
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
```
**Result:** This worked! But we want to find a more minimal fix.

## Current State

- The `#t=0.001` fix is already in main (all video sources in docs have it)
- The JavaScript file exists at `docs/javascripts/video-fix.js`
- `zensical.toml` has `extra_javascript = ["javascripts/video-fix.js"]`
- Current JS content is the one-liner with load+play (not yet confirmed working)

## Video Elements

All videos use this format:
```html
<video autoplay loop muted playsinline>
  <source src="/assets/quickstart.webm#t=0.001" type="video/webm">
</video>
```

The videos already have all the correct attributes (`autoplay`, `loop`, `muted`, `playsinline`).

## Files Involved

- `docs/index.md` - 3 videos
- `docs/getting-started.md` - 1 video
- `docs/commands.md` - 3 videos
- `docs/web-ui.md` - 6 videos
- `docs/javascripts/video-fix.js` - JavaScript fix
- `zensical.toml` - extra_javascript config

## Testing

To test locally:
```bash
uv run zensical build && python -m http.server 8765 --bind 0.0.0.0 --directory site
```

Then open in Safari (fresh tab, not a refresh) and check if videos appear.

## Possible Other Approaches Not Yet Tried

1. **Disable instant loading** - Remove `navigation.instant` and `navigation.instant.prefetch` from zensical.toml features
2. **Add GIF fallback** - Add `<img src="/assets/quickstart.gif">` inside video tag as fallback
3. **Add poster attribute** - Use `poster="/assets/quickstart.gif"` on video elements
4. **Add preload="auto"** - Force eager loading with `<video preload="auto">`
5. **CSS background color workaround** - Add `background: #000` to video elements

## References

- [SiteLint - Fixing HTML video autoplay in Safari](https://www.sitelint.com/blog/fixing-html-video-autoplay-blank-poster-first-frame-and-improving-performance-in-safari-and-ios-devices)
- [WordPress Gutenberg Issue #51995](https://github.com/WordPress/gutenberg/issues/51995)
- [MkDocs Material instant loading issues](https://github.com/squidfunk/mkdocs-material/issues/5816)
