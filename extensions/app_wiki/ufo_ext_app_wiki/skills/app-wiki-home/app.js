// Boot: load the portal's app kit off the portal that framed this page, then run app.tsx —
// the page itself. The kit is the app platform (React, the portal's components, the bridge
// transport); app.tsx is this app's own code and the file to edit.
onInit(function (init) {
  var assets = init.portal + "/surface/web/static/assets/";
  var css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = assets + "app-kit.css";
  document.head.appendChild(css);
  var kit = document.createElement("script");
  kit.src = assets + "app-kit.js";
  kit.onload = function () {
    UfoAppKit.run("./app.tsx");
  };
  document.head.appendChild(kit);
});
