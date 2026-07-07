/** @type {import('next').NextConfig} */

// When set (Playwright E2E against the real backend), proxy the API path prefixes to this target so
// the browser talks to the API *same-origin* — no CORS. In normal dev/prod this is unset, rewrites
// return [], and the client uses NEXT_PUBLIC_API_URL as before.
const apiProxyTarget = process.env.API_PROXY_TARGET;

const nextConfig = {
  reactStrictMode: true,
  // The grounded competitor-suggest pipeline (web search + Opus) runs ~1-2 min; Next's dev rewrite
  // proxy defaults to a 30s timeout and ECONNRESETs long calls. Raise it so /brands/suggest (and any
  // other slow proxied API call) completes. Milliseconds.
  experimental: { proxyTimeout: 180_000 },
  // The E2E boots two dev servers from this one directory (proxied + plain — see
  // playwright.config.ts); NEXT_DIST_DIR gives each its own build cache so they never clash. Unset
  // in normal dev/prod (and for `next build`), where the default `.next` is used.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  async rewrites() {
    if (!apiProxyTarget) return [];
    // Only the backend's own path prefixes are proxied. They never collide with the dashboard's
    // top-level page routes: the pages live at the root (/overview, /visibility, /pipeline, …),
    // while the API's equivalents live under /brands/{id}/… — so proxying /brands/* is safe.
    //
    // /content/*, /opportunities/*, and /settings/* need a word on collision: the dashboard now
    // *also* has pages at /content, /opportunities, and /settings. This array form of rewrites is
    // applied as `afterFiles` — after the filesystem/page routes — so the exact page routes win,
    // while only the backend's deeper API paths (/content/generate, /content/{id}/approve|publish,
    // /opportunities/{id}/act, /settings/llm-model) fall through to the proxy. (The list
    // `/brands/{id}/opportunities` + `.../opportunities/refresh` are already covered by
    // /brands/:path*.)
    const proxy = (source) => ({ source, destination: `${apiProxyTarget}${source}` });
    return [
      proxy("/auth/:path*"),
      proxy("/brands"),
      proxy("/brands/:path*"),
      proxy("/integrations/:path*"),
      proxy("/lead-capture/:path*"),
      proxy("/content/:path*"),
      proxy("/opportunities/:path*"),
      proxy("/settings/:path*"),
      proxy("/healthz"),
    ];
  },
};

export default nextConfig;
