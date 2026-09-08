const PORTAL_PAGE = /^\/surface\/web\/?([?#]|$)/;
const PORTAL_STATIC = /^\/surface\/web\/static(\/|$)/;

/** A page navigation stays here so the module under edit is what loads: the fleet reads the built tree,
 *  which is gitignored and absent in the loop this server exists for. */
export const devLocalPath = (method: string, url: string): string | undefined => {
  if (PORTAL_STATIC.test(url)) return url;
  return method === "GET" && PORTAL_PAGE.test(url) ? "/index.html" : undefined;
};
