/**
 * Cloudflare Worker: static assets + a same-origin `/api/*` proxy to the pod.
 *
 * The proxy exists because of one thing an `<audio>` element cannot do: send a
 * custom header. `api.ts` adds `ngrok-skip-browser-warning` to every `fetch`,
 * so JSON calls sail past ngrok's free-tier interstitial — but media elements
 * carry no such header, so the audio request came back as a 2.8 KB HTML warning
 * page with `content-type: text/html`, and Safari failed it as NotSupportedError.
 *
 * ngrok also sets a cookie once you click through that warning, which is why
 * this looked like a phone-only bug: a desktop that had visited the tunnel sent
 * the cookie on the cross-site audio request, while Safari's Intelligent
 * Tracking Prevention blocks third-party cookies by default and never did.
 * Chasing it as "mobile Safari being fussy about audio" is a dead end — the
 * element never received audio at all.
 *
 * Proxying makes the API same-origin with the page, so the header is injected
 * server-side where an element's limitations do not apply. It also removes CORS
 * from the picture: no cross-origin request means no preflight, and
 * VCS_CORS_ORIGINS stops being load-bearing for this deployment.
 */

/** Hop-by-hop headers that must not be forwarded to the origin. */
const STRIPPED = ['host', 'connection', 'keep-alive', 'transfer-encoding', 'upgrade'];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (!url.pathname.startsWith('/api/')) {
      return env.ASSETS.fetch(request);
    }

    if (!env.BACKEND_ORIGIN) {
      return new Response(
        JSON.stringify({
          type: 'about:blank',
          title: 'Proxy misconfigured',
          status: 503,
          detail: 'BACKEND_ORIGIN is not set on the Worker.',
          code: 'PROXY_NOT_CONFIGURED',
        }),
        { status: 503, headers: { 'content-type': 'application/problem+json' } },
      );
    }

    const target = new URL(url.pathname + url.search, env.BACKEND_ORIGIN);

    const headers = new Headers(request.headers);
    headers.set('ngrok-skip-browser-warning', 'true');
    for (const h of STRIPPED) headers.delete(h);

    // `redirect: 'manual'` keeps the backend's own 3xx intact instead of the
    // Worker silently following it and hiding the real response.
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: 'manual',
    });

    // Rebuild the response so headers are mutable. Range/206 and
    // Content-Disposition pass through untouched, which is what keeps seeking
    // and the download button working.
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: upstream.headers,
    });
  },
};
