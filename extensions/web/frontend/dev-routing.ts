import path from "node:path";

import type { Plugin } from "vite";

const PORTAL_PAGE = /^\/surface\/web\/?([?#]|$)/;
const PORTAL_STATIC = /^\/surface\/web\/static(\/|$)/;

/** A page navigation stays here so the module under edit is what loads: the fleet reads the built tree,
 *  which is gitignored and absent in the loop this server exists for. */
export const devLocalPath = (method: string, url: string): string | undefined => {
  if (PORTAL_STATIC.test(url)) return url;
  return method === "GET" && PORTAL_PAGE.test(url) ? "/index.html" : undefined;
};

const RELATIVE_ATTRIBUTE = /((?:src|href)=")(\.\.\/[^"]+)(")/g;

/** A page's own relative paths that climb out of the root — an app entry in its extension, the brand
 *  marks — in vite's `/@fs/` form while serving, under the base vite then prefixes: a browser resolves
 *  them against the page URL, outside the server's URL space, and 404s. The build inlines them. */
export const outsideRootPaths = (): Plugin => ({
  name: "outside-root-paths",
  apply: "serve",
  transformIndexHtml: {
    order: "pre",
    handler(html, { filename }) {
      return html.replace(
        RELATIVE_ATTRIBUTE,
        (_match, open: string, relative: string, close: string) =>
          `${open}/@fs${path.resolve(path.dirname(filename), relative)}${close}`,
      );
    },
  },
});
