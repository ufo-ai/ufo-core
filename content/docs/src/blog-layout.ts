import { defineRouteMiddleware } from "@astrojs/starlight/route-data";

export const onRequest = defineRouteMiddleware((context) => {
  const path = context.url.pathname;
  if (path === "/blog" || path.startsWith("/blog/")) {
    context.locals.starlightRoute.hasSidebar = false;
  }
  if (path === "/blog" || path === "/blog/") {
    context.locals.starlightRoute.toc = undefined;
  }
});
