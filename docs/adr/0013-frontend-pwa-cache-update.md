---
status: accepted
---

# Frontend PWA caching and prompted updates

Admin SPA and KDS H5 use Service Workers to cache only application resources: generated HTML, JavaScript, CSS, fonts, manifests, and icons. Business APIs, WebSocket traffic, uploads, downloads, credentials, and protected images remain network-only. A successful first visit installs the shell; later visits can open from Cache Storage and an unavailable API still fails normally rather than serving stale business data.

The Admin build uses `vite-plugin-pwa` with `generateSW` in prompt mode. Its root-scope worker precaches the complete Admin build and excludes `/kds/`, `/api/`, `/ws/`, documentation, and other non-SPA routes from navigation fallback. Three role manifests select the installed identity by route: Admin at `/`, hygiene staff at `/hygiene`, and recipe readers at `/recipe`. Those manifests are static files (`admin-web/public/pwa/manifests/`) served by FastAPI at `/pwa/manifests/*.webmanifest`; the plugin's own manifest generation is disabled.

KDS uses a separate `/kds/sw.js` scoped to `/kds/`. `scripts/build_kds.sh` invokes `scripts/generate_kds_pwa.py` after the uni-app H5 build to inject the manifest and a cache name derived from `APP_VERSION` plus the complete build fingerprint. Navigation and hashed assets are cache-first; Google Fonts are best-effort stale-while-revalidate. API and WebSocket requests are never cached.

Both clients check for an updated worker only when the application starts. A waiting worker is pre-cached, then shown as a non-blocking “new version ready” prompt. The user explicitly applies the update, which activates the worker and reloads the page. KDS additionally confirms before reloading when persisted print jobs are pending or failed.

New Release Bundles contain both Admin and KDS PWA artifacts, and publish-time validation requires them. Runtime Bundle validators intentionally continue to require only the legacy core paths so an operator can still roll back to a Release created before this ADR.

## Consequences

- HTTPS (or localhost) is required for Service Workers; insecure LAN HTTP falls back to normal browser behavior.
- The first visit remains online-only, and offline support does not extend to business data or write operations.
- Service Worker caches are namespaced and never delete the separate hygiene standard-photo cache.
- Every published frontend change requires rebuilding its manifest/service-worker bundle; shop machines remain Node-free.
