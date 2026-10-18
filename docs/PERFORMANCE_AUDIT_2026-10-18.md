# CarpologijaBH — performance audit, 18 October 2026

## Scope and limits

Reviewed the shared template, main navigation/search/carousel JavaScript, mobile search, presence tracking, public online visitors, product gallery, analytics initialization, CSS animation/effect rules, image templates, search querysets, compression and caching settings. Existing uncommitted removal of staff live notifications was retained.

No checkout, WMS, order-processing logic, migrations, schema, analytics event definitions or visual design were changed. No git push or deployment was performed.

The supplied Chrome measurements are INP 46 ms, CLS 0, Django TTFB about 377 ms, Meta 271.5 ms, Google 145.2 ms and first-party JavaScript 168.1 ms in the selected interval. These timing totals and an active Animations track do not establish the cause of a visible scroll hitch. A Chrome trace with long-task call stacks, rendering events and frame timings is still needed to attribute that symptom. Browser visual/profiling verification was not available in this environment; earlier browser runtime initialization failed.

## Confirmed findings and applied changes

### 1. Duplicate leave requests

`visitor-presence.js` previously called `sendBeacon()` and then unconditionally called `fetch()` for the same leave event. If the beacon was rejected it could also attempt a second beacon. Successful beacons now return immediately; a rejected/unsupported beacon uses one keepalive fetch fallback. The same payload and endpoint are retained.

### 2. Overlapping and hidden-tab heartbeat work

The 25-second heartbeat had no in-flight guard, so a slow response could overlap the next interval. `pageshow` called both `startHeartbeat()` and `sendHeartbeat()`, although starting a stopped timer already sends a heartbeat. A delayed initial callback could start work in a hidden tab.

Added an in-flight guard and visibility check and removed the redundant `pageshow` send. The interval, server payload, visitor semantics and visibility resume behavior remain in place. This bounds heartbeat concurrency to one per tab, not one across all users or tabs.

### 3. Public-online polling

The optional public-online widget polled every 20 seconds even in hidden tabs and allowed timer/click requests to overlap. It now skips hidden-tab requests, permits only one in-flight request and refreshes when the tab becomes visible. The optional setting, display and interval are retained.

### 4. Desktop autocomplete race and timer cleanup

Desktop autocomplete already used a 200/280 ms debounce and AbortController. However cancellation began only when the next debounced request started, so an older request/error/loading timer could update the UI during that delay. Repeating the same query could also defeat the value-only stale-response check.

Added a generation guard, immediate cancellation on new input, debounce cancellation on close and unconditional loading-timer cleanup in `finally`. Mobile search already has a 220 ms debounce, generation counter and immediate cancellation; it was left unchanged.

### 5. Scroll-time search positioning

An open desktop suggestion panel read `getBoundingClientRect()` and wrote positioning styles directly from each scroll/resize event. Those updates now coalesce through one pending animation frame and do not schedule work when suggestions are hidden. This reduces redundant layout work; it is not proof that this handler caused the reported stutter.

### 6. Unused page-level script download

`almost-cart.js` was downloaded on most public pages and then immediately returned unless the path was a product detail page. Its template inclusion is now limited to the `product_detail` route with the previous superuser exclusion retained. Its tracking behavior on product pages was not changed.

## Other reviewed areas

### Analytics

The shared page has one GA4 configuration (`G-5GVT50WMXB`) and one Google Ads configuration (`AW-18301637370`) using the same asynchronously injected gtag loader. These two configuration IDs are not evidence of duplicate GA4 initialization. No separate GTM container bootstrap was found in the shared template; the `googletagmanager.com` request here loads gtag.

The shared Meta bootstrap guards `window.fbq`, initializes once, and loads through an idle callback with a timeout. `site_prep.html` has its own Pixel bootstrap but is a separate document, not a second include in the normal layout. Purchase deduplication and browser/server event IDs exist. Runtime injection by extensions or external configuration cannot be ruled out solely by source inspection.

The supplied Meta/Google main-thread totals justify examining long-task stacks in a fresh Chrome trace, but do not justify removing or delaying conversion events further. Analytics was left intact.

### Animations, CSS and forced layout

Found explicit `offsetWidth` reads used to restart navigation, carousel wrap and cart feedback transitions. These are intentional synchronous layout points, mainly on interactions/wrap boundaries, not evidence of continuous scroll-time layout thrashing. They were not removed blindly.

Found continuous badge/promotion animations, transform-based carousels and blur/backdrop effects for overlays. Some are disabled by later product/theme rules, so counting source declarations overstates the number of active animations. Product artwork has explicit dimensions; CSS changes should be validated via computed styles and a recording before removing effects. Required animation and design rules were preserved.

### Search and database

`_prefetch_product_cards()` selects brand/category relations and prefetches variations and active promotions. Autocomplete uses a bounded candidate pool, SQL relevance ordering and Python reranking. No search ranking or stock/business logic was changed.

Local Django test-client measurements (development database, same process; not production TTFB):

| Request | First request | Second request | Queries first/second | SQL time first/second |
|---|---:|---:|---:|---:|
| `/` | 328 ms | 26 ms | 54 / 23 | 14 / 1 ms |
| `/api/pretraga/?q=fox&limit=4` | 13 ms | 10 ms | 10 / 10 | 4 / 3 ms |
| One available product detail | 37 ms | 25 ms | 23 / 23 | 5 / 4 ms |

A separate autocomplete scaling check returned 1, 4 and 8 results with 8, 9 and 9 queries respectively. It did not show linear per-result query growth for that sample. No identical SQL statement repeated more than twice in the first sample. This does not rule out N+1 on other products, promotions or branches. Remaining queries include middleware/session/tracking/context work; that scope was not changed.

### Images, compression and Cloudflare

`product_image.html` supplies `srcset`, `sizes`, width/height, async decoding and lazy loading by default. Explicit eager images get high fetch priority. Product-detail main images are eager. Responsive thumbnail generation and prefetched variation relations are already present. No broad image lazy-loading change was made, since deferring an LCP image can regress performance.

WhiteNoise uses compressed manifest static storage with long static-cache lifetime. Media storage configuration sets `public, max-age=31536000, immutable`; actual headers and invalidation for individual media objects still need a CDN check. These settings alone do not prove every image is a Cloudflare cache hit.

A read-only production HEAD check on `https://carpologijabh.ba/` returned HTTP 200, `Content-Encoding: gzip`, `Vary: Cookie, Accept-Encoding` and `cf-cache-status: DYNAMIC`. Session/CSRF-bearing HTML must not be blanket cached at Cloudflare. Actual static/media HIT rates were not measured in this pass.

### `/uzivo-obavijesti/?since=11278`

The workspace no longer contains its URL registration, view, frontend root, JS or CSS, following the preceding requested removal. Locally it cannot resolve. Previously the removed staff script used a 5-second interval; the supplied Network observation may therefore refer to an older deployment or already-open tab.

A read-only production HEAD request still returned HTTP 302 to `/prijava/?next=...` for this path on 18 October. That is consistent with a still-protected endpoint in production, although it does not prove polling is active for an authenticated user. The production behavior differs from the current workspace. No deployment was attempted. After the normal deployment, close/reload old tabs and verify that no initiator requests this URL.

## Files changed by this audit

- `EcommerceApp/static/js/main.js` — search cancellation/generation handling and frame-coalesced suggestion positioning.
- `EcommerceApp/static/js/main.min.js` — regenerated from the changed source.
- `EcommerceApp/static/js/visitor-presence.js` — single-flight visible-tab heartbeat and one leave transport.
- `EcommerceApp/static/js/public-online-visitors.js` — single-flight visible-tab polling.
- `EcommerceApp/template/base.html` — conditional almost-cart inclusion and changed script cache versions.
- `scripts/test_frontend_lifecycle.cjs` — executable regression tests for request concurrency and stale autocomplete responses.
- This report.

## Validation and repeatable measurement

Automated checks: Node syntax checks, `node scripts/test_frontend_lifecycle.cjs`, `python manage.py check`, `git diff --check`, and Django test-client render checks for home, search, cart and product detail.

The Node regression harness executes the actual scripts/functions with controlled pending requests and lifecycle events. It tests heartbeat overlap, hidden-tab suppression, accepted beacon versus fetch fallback, public-poll overlap and stale autocomplete response suppression. It does not replace a real-browser integration test.

After deploying through the usual user-controlled workflow:

1. Use the direct canonical HTTPS URL. In an incognito window record homepage load, scrolling, search typing/deleting and opening a product. Repeat at least three times with the same viewport/throttling/cache settings.
2. In Network verify one leave transport per navigation, no overlapping heartbeats, a 25-second visible-tab heartbeat, a 20-second optional public-online interval, and no `/uzivo-obavijesti/` requests. Hide the tab and confirm polling stops; show it and confirm it resumes.
3. Type quickly, repeat the same query, delete text and close search while a response is pending. Confirm only the latest query is displayed and loading UI does not reappear after close.
4. Inspect main-thread long tasks and their initiating stack. Compare analytics time separately from rendering, layout and paint. Inspect the Animations track together with frame drops, not alone.
5. Check product variations, image thumbnails/zoom, menus, wishlist, add-to-cart and back/forward navigation. Confirm INP/CLS and event delivery remain correct. No conversion-semantic changes are expected.
6. Check real image response sizes, dimensions, `Content-Encoding` and cache headers in Network. Compare warm and cold CDN requests separately.

No new production INP, CLS, LCP or TTFB improvement is claimed without a fresh browser measurement.
