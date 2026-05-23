/**
 * Client-side API helper.
 *
 * SMT is served under a basePath ("/smt") when it lives at the same domain
 * as the Order Sheet (ordersheet.flendergroup.com/smt/*). Next.js auto-prefixes
 * page routes and `<Link>` URLs, but it does NOT auto-prefix raw fetch() calls.
 * This helper centralises that prefix so every API request goes to the right
 * place whether basePath is empty (subdomain deploy) or set ("/smt").
 *
 * The value MUST match `basePath` in next.config.mjs.
 */
export const BASE_PATH = '/smt';

/** Prepend the basePath to an API path. */
export function apiPath(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${BASE_PATH}${p}`;
}

/** Thin wrapper around fetch() that auto-prepends the basePath. */
export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiPath(path), init);
}
