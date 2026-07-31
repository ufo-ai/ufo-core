const PORTAL_PAGE = /^\/surface\/web\/?([?#]|$)/;
const PORTAL_STATIC = /^\/surface\/web\/static(\/|$)/;

/**
 * Where the dev server sends one request: a path it serves itself, or undefined to proxy it to the
 * running fleet. A page navigation stays here so the module under edit is what loads — the fleet
 * reads the built tree, which is gitignored and absent in the loop this server exists for — and so
 * does the static path, which is this server's own. The token POST and every read proxy, so signing
 * in works against real data.
 */
export const devLocalPath = (method: string, url: string): string | undefined => {
  if (PORTAL_STATIC.test(url)) return url;
  return method === "GET" && PORTAL_PAGE.test(url) ? "/index.html" : undefined;
};
