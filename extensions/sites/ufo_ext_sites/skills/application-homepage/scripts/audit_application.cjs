// The measured half of ufo-app-bench, run inside the sandbox against the served page: rendered text
// contrast per element in both colour schemes, document width and leaf clipping at the desktop and
// the narrow width, console errors, and one screenshot per scheme for the vision judge.
//
// The script reports measurements, not verdicts. Every text leaf whose ratio is under the strictest
// AA floor is reported with its size, weight and exact Kit quiet-label slot. The grader applies the
// body, large-text or Kit quiet-label floor from that evidence. A page whose text all clears 4.5:1
// reports no text entry at all.
//
// Usage: node app-audit.cjs <application-root> <report.json> <light.png> <dark.png> <interactive.html> <static.html> <lane-width> [<application-design.svg>]
//        node app-audit.cjs --design <lane-width> <application-design.svg> [preview.png]
// The design is measured from the file itself, so the design a deploy is held to is the design
// sitting beside the source it hosts. A project with no design beside it is audited as a page.
// The lane width is always passed and never defaulted here: `application_audit.py` holds the one
// number, and a fallback in this file would be a second copy that drifts silently.
// The sandbox image installs playwright globally under /usr/local and exports NODE_PATH so the bare
// name resolves; a carrier that starts the sandbox without that env leaves it unresolvable, so fall
// back to the path the image installs into.
const { chromium } = (() => {
  try {
    return require('playwright');
  } catch {
    return require('/usr/local/lib/node_modules/playwright');
  }
})();
const fs = require('fs');
const http = require('http');
const path = require('path');

const AA_FLOOR = 4.5;
const DESIGN_ALPHA_FLOOR = 0.15;
const DESIGN_REGION_MAX = 6;
const DESIGN_VISIBLE_TEXT_MAX_CHARS = 72;
const DESIGN_INITIAL_HEIGHT = 844;
const DESIGN_INITIAL_FOLD = 844;
const DESIGN_NATIVE_DIMENSION_MAX = 4096;
const DESIGN_DRAWING_ELEMENTS = 'circle,ellipse,image,line,path,polygon,polyline,rect,text,use';
const DESIGN_ELEMENT_MAX = 4096;
const DESIGN_PROPERTY_MAX = 96;
const DESIGN_DECLARATION_MAX = DESIGN_ELEMENT_MAX * DESIGN_PROPERTY_MAX;
const DESIGN_CLONE_BYTE_MAX = 2 * 1024 * 1024;
const DESIGN_OUTPUT_BYTE_MAX = 4096;
const DESIGN_ANIMATED_POINT_MAX = 4096;
const DESIGN_ANIMATED_ATTRIBUTE_BYTE_MAX = 128 * 1024;
const DESIGN_INTERNAL_OVERLAP_MAX = 4;
const DESIGN_INTERNAL_OVERLAP_SLOP = 1;
const APPLICATION_LIFECYCLE_TIMEOUT_MS = 15000;
const APPLICATION_INTERACTION_TIMEOUT_MS = 300;
const APPLICATION_LIFECYCLE_DIAGNOSTIC_SUFFIX = '.lifecycle.json';
const APPLICATION_LIFECYCLE_PROBLEM_MAX = 4;
// The gate reads this file under a byte cap, and one console line of CJK or Cyrillic is three
// bytes a character. Four lines measured in characters pass that cap, the read fails, and the
// refusal the audit already wrote reaches the builder as nothing at all.
const APPLICATION_LIFECYCLE_PROBLEM_BYTES = 600;

function boundedProblem(text) {
  const bytes = Buffer.from(text, 'utf8');
  if (bytes.length <= APPLICATION_LIFECYCLE_PROBLEM_BYTES) return text;
  return bytes
    .subarray(0, APPLICATION_LIFECYCLE_PROBLEM_BYTES)
    .toString('utf8')
    .replace(/\uFFFD+$/, '');
}
const DESIGN_FAULT_EXIT = 4;
const SVG_PRESENTATION_PROPERTIES = new Set(
  ('alignment-baseline baseline-shift clip-path clip-rule color color-interpolation ' +
    'color-interpolation-filters color-rendering cursor cx cy d direction display ' +
    'dominant-baseline fill fill-opacity fill-rule filter flood-color flood-opacity ' +
    'font-family font-size font-size-adjust font-stretch font-style font-variant font-weight ' +
    'glyph-orientation-horizontal glyph-orientation-vertical image-rendering letter-spacing ' +
    'lighting-color marker marker-end marker-mid marker-start mask opacity overflow paint-order ' +
    'pointer-events r rx ry shape-rendering stop-color stop-opacity stroke stroke-dasharray ' +
    'stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity ' +
    'stroke-width text-anchor text-decoration text-rendering transform transform-origin ' +
    'unicode-bidi vector-effect visibility word-spacing writing-mode x y width height').split(' ')
);
const VIEWS = [
  { scheme: 'light', width: 1440, height: 900, shoot: true },
  { scheme: 'dark', width: 1440, height: 900, shoot: true },
  { scheme: 'light', width: 360, height: 844, shoot: false },
  { scheme: 'dark', width: 360, height: 844, shoot: false },
];
const MIME_TYPES = new Map([
  ['.avif', 'image/avif'],
  ['.eot', 'application/vnd.ms-fontobject'],
  ['.css', 'text/css; charset=utf-8'],
  ['.gif', 'image/gif'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.jpeg', 'image/jpeg'],
  ['.jpg', 'image/jpeg'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.otf', 'font/otf'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.ttf', 'font/ttf'],
  ['.txt', 'text/plain; charset=utf-8'],
  ['.wasm', 'application/wasm'],
  ['.webp', 'image/webp'],
  ['.webmanifest', 'application/manifest+json'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
  ['.xml', 'application/xml'],
]);
const APPLICATION_RESOURCE_TYPES = new Set([
  'stylesheet',
  'script',
  'font',
  'image',
  'fetch',
  'xhr',
  'manifest',
]);

function contained(root, target) {
  return target === root || target.startsWith(root + path.sep);
}

async function validatedApplicationRoot(input) {
  if (!path.isAbsolute(input)) throw new Error('application root must be absolute');
  const root = await fs.promises.realpath(input);
  const rootStat = await fs.promises.stat(root);
  if (!rootStat.isDirectory()) throw new Error('application root must be a directory');
  const previewInput = path.join(root, 'preview.html');
  const distInput = path.join(root, 'dist');
  const [previewInputStat, distInputStat] = await Promise.all([
    fs.promises.lstat(previewInput),
    fs.promises.lstat(distInput),
  ]);
  if (previewInputStat.isSymbolicLink() || !previewInputStat.isFile()) {
    throw new Error('application preview must be a regular file');
  }
  if (distInputStat.isSymbolicLink() || !distInputStat.isDirectory()) {
    throw new Error('application dist must be a directory');
  }
  const preview = await fs.promises.realpath(previewInput);
  const dist = await fs.promises.realpath(distInput);
  if (!contained(root, preview) || !contained(root, dist)) {
    throw new Error('application root inputs must stay inside the application root');
  }
  const [previewStat, distStat] = await Promise.all([
    fs.promises.stat(preview),
    fs.promises.stat(dist),
  ]);
  if (!previewStat.isFile()) throw new Error('application preview must be a regular file');
  if (!distStat.isDirectory()) throw new Error('application dist must be a directory');
  return { root, preview, dist };
}

function applicationRequestPaths(requestUrl, application) {
  const raw = (requestUrl || '/').split('?', 1)[0];
  let pathname;
  try {
    pathname = decodeURIComponent(raw);
  } catch {
    return null;
  }
  if (!pathname.startsWith('/') || pathname.startsWith('//') || pathname.includes('\\') ||
      pathname.includes('\0')) return null;
  if (pathname === '/') return [application.preview];
  const segments = pathname.split('/');
  if (segments.slice(1).some((segment) => !segment || segment === '.' || segment === '..')) {
    return null;
  }
  const projectFile = path.join(application.root, ...segments.slice(1));
  if (!pathname.startsWith('/assets/')) return [projectFile];
  return [projectFile, path.join(application.dist, 'assets', ...segments.slice(2))];
}

async function serveApplicationFile(request, response, application) {
  if (!['GET', 'HEAD'].includes(request.method || '')) {
    response.writeHead(405, { Allow: 'GET, HEAD' }).end();
    return;
  }
  const candidates = applicationRequestPaths(request.url, application);
  if (!candidates) {
    response.writeHead(404).end();
    return;
  }
  let target = null;
  for (const candidate of candidates) {
    let candidateStat;
    try {
      candidateStat = await fs.promises.lstat(candidate);
    } catch (error) {
      if (error.code === 'ENOENT') continue;
      throw error;
    }
    if (candidateStat.isSymbolicLink() || !candidateStat.isFile()) break;
    const resolved = await fs.promises.realpath(candidate);
    if (!contained(application.root, resolved)) break;
    target = resolved;
    break;
  }
  if (!target) {
    response.writeHead(404).end();
    return;
  }
  const content = await fs.promises.readFile(target);
  response.writeHead(200, {
    'Content-Length': content.length,
    'Content-Type': MIME_TYPES.get(path.extname(target).toLowerCase()) || 'application/octet-stream',
  });
  response.end(request.method === 'HEAD' ? undefined : content);
}

async function startApplicationServer(application) {
  const sockets = new Set();
  const server = http.createServer((request, response) => {
    void serveApplicationFile(request, response, application).catch(() => {
      if (!response.headersSent) response.writeHead(500);
      response.end();
    });
  });
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject);
      resolve();
    });
  });
  const address = server.address();
  if (!address || typeof address === 'string' || address.family !== 'IPv4') {
    await new Promise((resolve) => server.close(resolve));
    throw new Error('application audit server did not bind IPv4');
  }
  return { server, sockets, url: `http://127.0.0.1:${address.port}/preview.html` };
}

async function closeApplicationServer(server, sockets) {
  const closed = new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  });
  for (const socket of sockets) socket.destroy();
  await closed;
}

function trackApplicationResources(page, applicationUrl) {
  const origin = new URL(applicationUrl).origin;
  const problems = [];
  const checkedManifests = new Set();
  const record = (request, reason) => {
    let resource;
    try {
      resource = new URL(request.url());
    } catch {
      return;
    }
    if (resource.origin !== origin) return;
    const type = request.resourceType();
    if (!APPLICATION_RESOURCE_TYPES.has(type) && type !== 'other') return;
    problems.push({ type, url: resource.href, reason });
  };
  page.on('response', (response) => {
    if (response.status() >= 400) {
      record(response.request(), `returned ${response.status()}`);
    }
  });
  page.on('requestfailed', (request) => {
    record(request, request.failure()?.errorText || 'network error');
  });
  return async () => {
    const references = (await Promise.all(page.frames().map(async (frame) => {
      try {
        return await frame.locator('link[href]').evaluateAll((links) => links.map((link) => ({
          href: link.href,
          rel: Array.from(link.relList),
        })));
      } catch {
        return [];
      }
    }))).flat();
    const explicitIcons = new Set(references
      .filter(({ rel }) => rel.some((value) => value.toLowerCase() === 'icon'))
      .map(({ href }) => href));
    const explicitManifests = references
      .filter(({ rel }) => rel.some((value) => value.toLowerCase() === 'manifest'))
      .map(({ href }) => href);
    for (const href of explicitManifests) {
      let resource;
      try {
        resource = new URL(href);
      } catch {
        continue;
      }
      if (resource.origin !== origin || checkedManifests.has(resource.href)) continue;
      checkedManifests.add(resource.href);
      try {
        const response = await page.context().request.get(resource.href);
        const status = response.status();
        await response.dispose();
        if (status >= 400) {
          problems.push({ type: 'manifest', url: resource.href, reason: `returned ${status}` });
        }
      } catch (error) {
        problems.push({
          type: 'manifest',
          url: resource.href,
          reason: error instanceof Error ? error.message : 'network error',
        });
      }
    }
    return Array.from(new Map(problems
      .filter(({ type, url }) => APPLICATION_RESOURCE_TYPES.has(type) || explicitIcons.has(url))
      .map((problem) => [`${problem.type}|${problem.url}|${problem.reason}`, problem])
    ).values());
  };
}

async function assertApplicationResources(resourceProblems) {
  const problems = await resourceProblems();
  if (!problems.length) return;
  const first = problems[0];
  throw new Error(`application resource failed: ${first.type} ${first.url} ${first.reason}`);
}

async function closeApplicationAudit(browser, server, sockets) {
  try {
    if (browser) await browser.close();
  } finally {
    await closeApplicationServer(server, sockets);
  }
}

async function measure(floor) {
  const TEXT_FRAGMENT_TOUCH_PX = 1;
  const KIT_QUIET_TEXT_SLOTS = new Set(['badge', 'chart-caption', 'stat-label']);
  // A computed colour carries whatever syntax the engine chose: a color-mix() token resolves to
  // `color(srgb …)`, which no rgb() pattern reads. The browser paints the value into one pixel and
  // the pixel is the answer. An unpaintable value leaves both probe fills in place, so it reads as
  // no colour rather than as black.
  const swatch = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const parse = (value) => {
    swatch.fillStyle = '#000000';
    swatch.fillStyle = value;
    const painted = swatch.fillStyle;
    swatch.fillStyle = '#ffffff';
    swatch.fillStyle = value;
    if (swatch.fillStyle !== painted) return null;
    swatch.globalCompositeOperation = 'copy';
    swatch.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = swatch.getImageData(0, 0, 1, 1).data;
    return { r, g, b, a: a / 255 };
  };
  const over = (front, back) => ({
    r: front.r * front.a + back.r * (1 - front.a),
    g: front.g * front.a + back.g * (1 - front.a),
    b: front.b * front.a + back.b * (1 - front.a),
    a: 1,
  });
  const luminance = (colour) => {
    const channel = (value) => {
      const scaled = value / 255;
      return scaled <= 0.03928 ? scaled / 12.92 : Math.pow((scaled + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * channel(colour.r) + 0.7152 * channel(colour.g) + 0.0722 * channel(colour.b);
  };
  const ratio = (one, other) => {
    const first = luminance(one);
    const second = luminance(other);
    return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
  };
  const visuallyHidden = (style, box) =>
    (style.position === 'absolute' &&
      style.overflow === 'hidden' &&
      box.width <= 2 &&
      box.height <= 2 &&
      (style.clip !== 'auto' || style.clipPath !== 'none'));
  const visible = (element, box) => {
    if (box.width === 0 || box.height === 0 || element.closest('[aria-hidden="true"]')) return false;
    const closedDetails = element.closest('details:not([open])');
    if (closedDetails) {
      const summary = element.closest('summary');
      if (!summary || summary.parentElement !== closedDetails) return false;
    }
    for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      const ancestorBox = ancestor.getBoundingClientRect();
      if (style.visibility === 'hidden' || style.display === 'none') return false;
      if (style.contentVisibility === 'hidden') return false;
      if (parseFloat(style.opacity) < 0.15) return false;
      if (visuallyHidden(style, ancestorBox)) return false;
    }
    return true;
  };
  const paintHasAlpha = (value) => {
    if (!value || value.trim().toLowerCase() === 'none') return false;
    const colour = parse(value);
    return colour ? colour.a > 0 : true;
  };
  const textPaints = (element, style) => {
    if (element instanceof SVGTextContentElement) {
      const fill = paintHasAlpha(style.fill) && parseFloat(style.fillOpacity) > 0;
      const stroke = paintHasAlpha(style.stroke) && parseFloat(style.strokeOpacity) > 0 &&
        parseFloat(style.strokeWidth) > 0;
      return fill || stroke;
    }
    const clippedBackground = (style.backgroundClip || style.webkitBackgroundClip || '')
      .split(',').some((clip) => clip.trim() === 'text') &&
      (paintHasAlpha(style.backgroundColor) ||
        (style.backgroundImage && style.backgroundImage !== 'none'));
    const fill = paintHasAlpha(style.webkitTextFillColor || style.color);
    const stroke = paintHasAlpha(style.webkitTextStrokeColor) &&
      parseFloat(style.webkitTextStrokeWidth) > 0;
    return clippedBackground || fill || stroke;
  };
  const intersectionRects = (elements, rootMargin) => new Promise((resolve) => {
    const targets = Array.from(new Set(elements));
    const intersections = new Map();
    if (!targets.length) {
      resolve(intersections);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) intersections.set(entry.target, entry.intersectionRect);
      if (intersections.size !== targets.length) return;
      observer.disconnect();
      resolve(intersections);
    }, { rootMargin });
    targets.forEach((element) => observer.observe(element));
  });
  const overlaps = (first, second) =>
    first.width > 0 && first.height > 0 && second.width > 0 && second.height > 0 &&
    Math.max(first.left, second.left) < Math.min(first.right, second.right) &&
    Math.max(first.top, second.top) < Math.min(first.bottom, second.bottom);
  const paintedAtPoint = (node, rect, bounds) => {
    const left = Math.max(bounds.left, rect.left);
    const right = Math.min(bounds.right, rect.right);
    const top = Math.max(bounds.top, rect.top);
    const bottom = Math.min(bounds.bottom, rect.bottom);
    if (left >= right || top >= bottom) return false;
    const parent = node.parentElement;
    return [0.2, 0.5, 0.8].some((part) => {
      const x = left + (right - left) * part;
      const y = top + (bottom - top) / 2;
      const caret = document.caretRangeFromPoint(x, y);
      if (caret && (caret.startContainer === node || parent.contains(caret.startContainer))) {
        return true;
      }
      const hits = document.elementsFromPoint(x, y);
      const parentIndex = hits.findIndex((hit) => hit === parent || parent.contains(hit));
      if (parentIndex < 0) return false;
      return hits.slice(0, parentIndex).every((hit) => {
        const style = getComputedStyle(hit);
        const background = parse(style.backgroundColor);
        return parseFloat(style.opacity) < 1 ||
          ((!background || background.a < 0.999) && style.backgroundImage === 'none');
      });
    });
  };
  // The first opaque backdrop behind the element: every translucent background on the way up is
  // composited, so a label on a tinted chip is measured against what the eye actually sees.
  const backdrop = (element) => {
    let node = element;
    let stack = null;
    while (node && node.nodeType === 1) {
      const behind = parse(getComputedStyle(node).backgroundColor);
      if (behind && behind.a > 0) {
        stack = stack ? over(stack, behind) : behind;
        if (stack.a >= 0.999) return stack;
      }
      node = node.parentElement;
    }
    const root = parse(getComputedStyle(document.documentElement).backgroundColor);
    const base = root && root.a > 0 ? root : { r: 255, g: 255, b: 255, a: 1 };
    return stack ? over(stack, base) : base;
  };

  const text = [];
  const seen = new Set();
  let checked = 0;
  let underFloor = 0;
  for (const element of document.querySelectorAll('body *')) {
    if (element.children.length) continue;
    const words = (element.textContent || '').trim();
    if (!words) continue;
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    if (!visible(element, box) || !textPaints(element, style)) continue;
    const foreground = parse(style.color);
    if (!foreground) continue;
    const behind = backdrop(element);
    const painted = foreground.a < 1 ? over(foreground, behind) : foreground;
    const measured = ratio(painted, behind);
    checked += 1;
    if (measured >= floor) continue;
    underFloor += 1;
    const rounded = Math.round(measured * 100) / 100;
    const selector =
      element.tagName.toLowerCase() +
      (element.className ? '.' + String(element.className).trim().split(/\s+/)[0] : '');
    const slotted = element.closest('[data-slot]');
    const slot = slotted && KIT_QUIET_TEXT_SLOTS.has(slotted.dataset.slot) ?
      slotted.dataset.slot : null;
    const key = `${selector}|${style.fontSize}|${style.fontWeight}|${rounded}|${slot || ''}`;
    // One entry per distinct selector, size, weight and ratio: the grader fails the view on any one
    // of them, so a repeated row's every cell would add bytes and no verdict.
    if (seen.has(key)) continue;
    seen.add(key);
    text.push({
      text: words.slice(0, 48),
      selector,
      px: parseFloat(style.fontSize),
      weight: parseInt(style.fontWeight, 10) || 400,
      ratio: rounded,
      colour: style.color,
      background: `rgb(${Math.round(behind.r)}, ${Math.round(behind.g)}, ${Math.round(behind.b)})`,
      slot,
    });
  }

  const textNodes = [];
  const textBoundary = (element) => {
    for (let boundary = element; boundary && boundary !== document.body;
      boundary = boundary.parentElement) {
      if (!['inline', 'contents'].includes(getComputedStyle(boundary).display)) return boundary;
    }
    return document.body;
  };
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const element = node.parentElement;
    if (!element || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(element.tagName)) continue;
    const value = node.data.replace(/\s+/g, ' ');
    if (!value) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects());
    const box = range.getBoundingClientRect();
    const style = getComputedStyle(element);
    if (!visible(element, box) || !textPaints(element, style)) continue;
    textNodes.push({ node, element, value, rects, boundary: textBoundary(element) });
  }
  const pageWidth = Math.max(document.documentElement.scrollWidth, window.innerWidth);
  const pageHeight = Math.max(document.documentElement.scrollHeight, window.innerHeight);
  const documentMargin = [
    Math.max(0, window.scrollY),
    Math.max(0, pageWidth - window.scrollX - window.innerWidth),
    Math.max(0, pageHeight - window.scrollY - window.innerHeight),
    Math.max(0, window.scrollX),
  ].map((value) => `${value}px`).join(' ');
  const elements = textNodes.map(({ element }) => element);
  const documentIntersections = await intersectionRects(elements, documentMargin);
  const viewport = {
    left: 0,
    right: window.innerWidth,
    top: 0,
    bottom: window.innerHeight,
    width: window.innerWidth,
    height: window.innerHeight,
  };
  const renderedNodes = textNodes.flatMap(({ node, element, value, rects, boundary }) => {
    const intersection = documentIntersections.get(element);
    if (!intersection) return [];
    const paintedRects = rects.filter((rect) =>
      overlaps(rect, intersection) &&
      (!overlaps(rect, viewport) || paintedAtPoint(node, rect, viewport))
    );
    return paintedRects.length ? [{ node, element, value, rects: paintedRects, boundary }] : [];
  });
  const joinPaintedText = (fragments) => {
    let output = '';
    let previous = null;
    for (const fragment of fragments) {
      if (previous && output && !/\s$/.test(output) && !/^\s/.test(fragment.value)) {
        const before = previous.rects[previous.rects.length - 1];
        const after = fragment.rects[0];
        const sameLine = Math.max(before.top, after.top) < Math.min(before.bottom, after.bottom);
        const horizontalGap = Math.max(
          before.left - after.right,
          after.left - before.right,
          0
        );
        if (previous.boundary !== fragment.boundary || !sameLine ||
            horizontalGap > TEXT_FRAGMENT_TOUCH_PX) output += ' ';
      }
      output += fragment.value;
      previous = fragment;
    }
    return output.replace(/\s+/g, ' ').trim().slice(0, 40000);
  };
  const renderedParts = renderedNodes.map(({ value }) => value.trim()).filter(Boolean);
  const renderedText = joinPaintedText(renderedNodes);
  const aboveFoldText = joinPaintedText(renderedNodes.flatMap((fragment) => {
    const paintedRects = fragment.rects.filter(
      (rect) => paintedAtPoint(fragment.node, rect, viewport)
    );
    return paintedRects.length ? [{ ...fragment, rects: paintedRects }] : [];
  }));

  const regions = Array.from(document.querySelectorAll('[data-app-region]')).flatMap((element) => {
    const name = (element.getAttribute('data-app-region') || '').trim().slice(0, 80);
    const box = element.getBoundingClientRect();
    if (!name || !visible(element, box)) return [];
    const left = Math.max(0, Math.min(1, box.left / pageWidth));
    const right = Math.max(0, Math.min(1, box.right / pageWidth));
    const top = Math.max(0, Math.min(1, (box.top + window.scrollY) / pageHeight));
    const bottom = Math.max(0, Math.min(1, (box.bottom + window.scrollY) / pageHeight));
    if (left >= right || top >= bottom) return [];
    return [{
      name,
      left,
      top,
      width: right - left,
      height: bottom - top,
      aboveFold: box.bottom > 0 && box.top < window.innerHeight,
    }];
  }).slice(0, 20);

  const overlapLabel = (element, textValue = '') => {
    const slot = element.getAttribute('data-slot');
    const role = element.getAttribute('role');
    const name = element.tagName.toLowerCase() +
      (slot ? `[data-slot=${slot}]` : role ? `[role=${role}]` : '');
    const text = (textValue || element.getAttribute('aria-label') || element.textContent || '')
      .trim().replace(/\s+/g, ' ').slice(0, 64);
    return (text ? `${name} "${text}"` : name).slice(0, 160);
  };
  const intentionalComposition = (element) => element.closest(
    '[data-slot="avatar-stack"], [data-slot="attachment"], [data-slot="chart"], ' +
    '[data-slot="meter"], [data-slot="segmented"], [data-slot="switch"], ' +
    '[data-slot="checkbox"], [data-slot="lightbox-stage"], [data-slot="reveal"]'
  );
  const overlayComposition = (element) => element.closest(
    '[data-slot="dialog-content"], [data-slot="dropdown-menu-content"], ' +
    '[data-slot="select-content"], [data-slot="sheet-content"], [data-slot="toast"], ' +
    '[data-slot="toast-stand"], [data-slot="lightbox-stage"]'
  );
  const controlComposition = (element) => element.closest(
    'button, a[href], input, select, textarea, [role="button"], [role="tab"], ' +
    '[role="checkbox"], [role="switch"]'
  );
  const overlapCandidates = renderedNodes.flatMap(({ element, value, rects }) => {
    if (controlComposition(element)) return [];
    return rects.map((rect) => ({
      element,
      box: rect,
      label: overlapLabel(element, value),
      intentional: intentionalComposition(element),
      overlay: overlayComposition(element),
      control: null,
    }));
  });
  for (const element of document.querySelectorAll(
    'button, a[href], input, select, textarea, [role="button"], [role="tab"], ' +
    '[role="checkbox"], [role="switch"], img, canvas, video, svg, [data-app-region], ' +
    '[data-slot="card"], [data-slot="item"], [data-slot="stat"], ' +
    '[data-slot="table-container"]'
  )) {
    const box = element.getBoundingClientRect();
    if (!visible(element, box)) continue;
    overlapCandidates.push({
      element,
      box,
      label: overlapLabel(element),
      intentional: intentionalComposition(element),
      overlay: overlayComposition(element),
      control: controlComposition(element),
    });
  }
  const accidentalOverlaps = [];
  for (let firstIndex = 0; firstIndex < overlapCandidates.length; firstIndex += 1) {
    const first = overlapCandidates[firstIndex];
    for (let secondIndex = firstIndex + 1;
      secondIndex < overlapCandidates.length; secondIndex += 1) {
      const second = overlapCandidates[secondIndex];
      if (first.element === second.element || first.element.contains(second.element) ||
          second.element.contains(first.element)) continue;
      if (first.control && first.control === second.control) continue;
      if (first.intentional && first.intentional === second.intentional) continue;
      if (first.overlay !== second.overlay && (first.overlay || second.overlay)) continue;
      const width = Math.min(first.box.right, second.box.right) -
        Math.max(first.box.left, second.box.left);
      const height = Math.min(first.box.bottom, second.box.bottom) -
        Math.max(first.box.top, second.box.top);
      if (width <= 1 || height <= 1) continue;
      accidentalOverlaps.push({
        first: first.label,
        second: second.label,
        width: Math.round(width * 10) / 10,
        height: Math.round(height * 10) / 10,
      });
      if (accidentalOverlaps.length === 8) break;
    }
    if (accidentalOverlaps.length === 8) break;
  }

  const wider = [];
  const overhangs = [];
  const overhangKeys = new Set();
  const addOverhang = (key, evidence) => {
    if (overhangs.length === 8 || overhangKeys.has(key)) return;
    overhangKeys.add(key);
    overhangs.push(evidence.slice(0, 200));
  };
  const containerSelector = [
    'main', 'section', 'article', 'aside', 'nav', '[data-app-region]',
    '[data-slot="card"]', '[data-slot="card-content"]', '[data-slot="item"]',
    '[data-slot="stat"]', '[data-slot="table-container"]',
  ].join(', ');
  const clipped = [];
  for (const element of document.querySelectorAll('*')) {
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    const style = getComputedStyle(element);
    if (!visible(element, box)) continue;
    const name =
      element.tagName.toLowerCase() +
      (element.className ? '.' + String(element.className).trim().split(/\s+/)[0] : '');
    const viewportLeft = Math.max(0, -box.left);
    const viewportRight = Math.max(0, box.right - window.innerWidth);
    if (viewportLeft > 1 || viewportRight > 1) {
      wider.push(name);
      const edge = viewportLeft > viewportRight ? 'left' : 'right';
      const pixels = Math.round(Math.max(viewportLeft, viewportRight) * 10) / 10;
      addOverhang(`viewport|${name}|${edge}`, `${name} extends ${pixels}px past viewport ${edge}`);
    }
    const container = element.parentElement?.closest(containerSelector);
    const containerOverflowX = container ? getComputedStyle(container).overflowX : null;
    if (container && !intentionalComposition(element) && !overlayComposition(element) &&
        style.position !== 'fixed' && !['auto', 'scroll'].includes(containerOverflowX)) {
      const containerBox = container.getBoundingClientRect();
      const containerLeft = Math.max(0, containerBox.left - box.left);
      const containerRight = Math.max(0, box.right - containerBox.right);
      if (containerLeft > 1 || containerRight > 1) {
        const edge = containerLeft > containerRight ? 'left' : 'right';
        const pixels = Math.round(Math.max(containerLeft, containerRight) * 10) / 10;
        const containerName = overlapLabel(container);
        addOverhang(
          `container|${name}|${containerName}|${edge}`,
          `${name} extends ${pixels}px past ${containerName} ${edge}`
        );
      }
    }
    if (element.children.length) continue;
    if (
      element.scrollWidth > element.clientWidth + 2 &&
      style.overflow !== 'visible'
    ) {
      clipped.push(name + ': ' + (element.textContent || '').trim().slice(0, 40));
    }
  }
  return {
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    documentHeight: document.documentElement.scrollHeight,
    viewportHeight: window.innerHeight,
    pageHeight,
    textChecked: checked,
    textUnderFloor: underFloor,
    text,
    renderedText,
    renderedParts,
    aboveFoldText,
    regions,
    pastViewport: wider.slice(0, 12),
    clipped: clipped.slice(0, 8),
    overhangs,
    overlaps: accidentalOverlaps,
  };
}

function controlCandidates() {
  const visible = (element) => {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return box.width > 0 && box.height > 0 && style.display !== 'none' &&
      style.visibility !== 'hidden' && !element.disabled;
  };
  const selector = (element) => {
    const parts = [];
    for (let node = element; node && node !== document.body; node = node.parentElement) {
      const tag = node.tagName.toLowerCase();
      const siblings = Array.from(node.parentElement.children).filter(
        (candidate) => candidate.tagName === node.tagName
      );
      parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(node) + 1})`);
    }
    return `body > ${parts.join(' > ')}`;
  };
  const name = (element) => {
    const labelled = (element.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent || '').join(' ');
    const labels = Array.from(element.labels || []).map((label) => label.textContent || '').join(' ');
    const ownText = ['BUTTON', 'SUMMARY', 'A'].includes(element.tagName) ||
      element.hasAttribute('role') ? element.textContent : '';
    return (element.getAttribute('aria-label') || labelled || labels ||
      element.getAttribute('title') || ownText || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  };
  return Array.from(document.querySelectorAll(
    'button, summary, select, input[type="checkbox"], input[type="radio"], ' +
      '[role="button"], [role="tab"], [role="checkbox"], [role="switch"], a[href^="#"]'
  )).filter((element) => {
    const label = name(element);
    return visible(element) && label && !/dark|light|theme|colour|color|mode/i.test(label);
  }).map((element) => ({
    selector: selector(element),
    name: name(element),
    tag: element.tagName.toLowerCase(),
    type: element.getAttribute('type') || '',
  }));
}

function visibleState() {
  const rawText = (document.body.innerText || '').trim().slice(0, 12000);
  const parts = rawText.split(/\n+/).map((part) => part.trim().replace(/\s+/g, ' ')).filter(Boolean);
  const controls = Array.from(document.querySelectorAll(
    'button, summary, select, input, [role="button"], [role="tab"], [role="checkbox"], ' +
      '[role="switch"], dialog'
  )).filter((element) => {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return box.width > 0 && box.height > 0 && style.display !== 'none' &&
      style.visibility !== 'hidden';
  }).map((element) => ({
    tag: element.tagName.toLowerCase(),
    text: (element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120),
    checked: 'checked' in element ? element.checked : null,
    value: element.tagName === 'SELECT' ? element.value : null,
    expanded: element.getAttribute('aria-expanded'),
    pressed: element.getAttribute('aria-pressed'),
    selected: element.getAttribute('aria-selected'),
    open: element.hasAttribute('open'),
  }));
  return JSON.stringify({
    text: parts.join(' '),
    parts,
    controls,
  });
}

async function applicationFrame(page) {
  const element = await page.$('iframe[name="ufo-app"]');
  if (!element) return page;
  const frame = await element.contentFrame();
  if (!frame) throw new Error('application frame did not load');
  return frame;
}

class ApplicationLifecycleError extends Error {
  constructor(reason, snapshot = null, problems = []) {
    super(reason);
    this.snapshot = snapshot;
    this.problems = problems;
  }
}

function lifecycleStable(snapshot) {
  return snapshot.mounted && snapshot.blockingWork === 0 && snapshot.state === 'idle';
}

function sameLifecycle(first, second) {
  return first.generation === second.generation && first.epoch === second.epoch &&
    first.revision === second.revision;
}

async function lifecycleEvaluate(frame, operation, argument, deadline, snapshot = null) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) {
    throw new ApplicationLifecycleError('application lifecycle did not become ready', snapshot);
  }
  let timer;
  try {
    return await Promise.race([
      frame.evaluate(operation, argument),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new ApplicationLifecycleError(
          'application lifecycle did not become ready', snapshot
        )), remaining);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function lifecycleProbe(frame, deadline, snapshot) {
  return lifecycleEvaluate(frame, async () => {
    const controller = window.__ufoApplicationLifecycle;
    if (!controller || typeof controller.snapshot !== 'function' ||
        typeof controller.afterPaint !== 'function' ||
        typeof controller.beginObservation !== 'function' ||
        typeof controller.endObservation !== 'function') return null;
    const before = controller.snapshot();
    await controller.afterPaint();
    return { before, after: controller.snapshot() };
  }, undefined, deadline, snapshot);
}

async function waitForApplicationReadyUntil(frame, deadline) {
  let last = null;
  while (Date.now() < deadline) {
    const probe = await lifecycleProbe(frame, deadline, last);
    if (!probe) throw new ApplicationLifecycleError('application lifecycle signal is missing');
    last = probe.after;
    if (lifecycleStable(probe.before) && lifecycleStable(probe.after) &&
        sameLifecycle(probe.before, probe.after)) return probe.after;
  }
  throw new ApplicationLifecycleError('application lifecycle did not become ready', last);
}

// A page that never mounted threw on its way there, and the throw is already in `problems` —
// the console and pageerror listeners the caller attached. Without it the refusal names the
// symptom and the builder redraws a page whose one broken line it was never shown.
async function waitForApplicationReady(
  frame, timeoutMs = APPLICATION_LIFECYCLE_TIMEOUT_MS, problems = []
) {
  try {
    return await waitForApplicationReadyUntil(frame, Date.now() + timeoutMs);
  } catch (error) {
    if (!(error instanceof ApplicationLifecycleError)) throw error;
    throw new ApplicationLifecycleError(
      error.message,
      error.snapshot,
      problems.slice(0, APPLICATION_LIFECYCLE_PROBLEM_MAX).map(boundedProblem)
    );
  }
}

async function measureApplication(
  frame,
  floor,
  timeoutMs = APPLICATION_LIFECYCLE_TIMEOUT_MS
) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    const ready = await waitForApplicationReadyUntil(frame, deadline);
    const measured = await lifecycleEvaluate(frame, measure, floor, deadline, ready);
    last = await lifecycleEvaluate(
      frame,
      () => window.__ufoApplicationLifecycle.snapshot(),
      undefined,
      deadline,
      ready
    );
    if (sameLifecycle(ready, last) && lifecycleStable(last)) return measured;
  }
  throw new ApplicationLifecycleError('application changed during measurement', last);
}

async function beginApplicationObservation(
  frame,
  timeoutMs = APPLICATION_LIFECYCLE_TIMEOUT_MS
) {
  return lifecycleEvaluate(
    frame,
    () => window.__ufoApplicationLifecycle.beginObservation(),
    undefined,
    Date.now() + timeoutMs
  );
}

async function endApplicationObservation(
  frame,
  epoch,
  timeoutMs = APPLICATION_LIFECYCLE_TIMEOUT_MS
) {
  const deadline = Date.now() + timeoutMs;
  await lifecycleEvaluate(
    frame,
    (heldEpoch) => window.__ufoApplicationLifecycle.endObservation(heldEpoch),
    epoch,
    deadline
  );
  return waitForApplicationReadyUntil(frame, deadline);
}

async function waitForApplicationInteraction(
  frame,
  before,
  timeoutMs = APPLICATION_INTERACTION_TIMEOUT_MS
) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    let probe;
    try {
      probe = await lifecycleProbe(frame, deadline, last);
    } catch (error) {
      if (error instanceof ApplicationLifecycleError && last && lifecycleStable(last)) return null;
      throw error;
    }
    if (!probe) throw new ApplicationLifecycleError('application lifecycle signal is missing');
    last = probe.after;
    if (!lifecycleStable(probe.before) || !lifecycleStable(probe.after) ||
        !sameLifecycle(probe.before, probe.after)) continue;
    const after = await lifecycleEvaluate(frame, visibleState, undefined, deadline, last);
    last = await lifecycleEvaluate(
      frame,
      () => window.__ufoApplicationLifecycle.snapshot(),
      undefined,
      deadline,
      last
    );
    if (lifecycleStable(last) && sameLifecycle(probe.after, last) && before !== after) return after;
  }
  if (last && lifecycleStable(last)) return null;
  throw new ApplicationLifecycleError('application lifecycle did not become ready', last);
}

async function renderedDesignRegions(page, viewport) {
  return page.evaluate(async ({
    alphaFloor,
    animatedAttributeByteMax,
    animatedPointMax,
    cloneByteMax,
    declarationMax,
    drawingElements,
    elementMax,
    initialFold,
    internalOverlapMax,
    internalOverlapSlop,
    outputByteMax,
    presentationProperties,
    propertyMax,
    regionMax,
    visibleTextMaxChars,
    viewport,
  }) => {
    const root = document.querySelector('svg');
    if (!root) return [];
    if (typeof root.pauseAnimations === 'function') root.pauseAnimations();
    if (typeof root.setCurrentTime === 'function') root.setCurrentTime(0);
    document.getAnimations().forEach((animation) => {
      animation.pause();
      animation.currentTime = 0;
    });
    await document.fonts.ready;
    const sourceElements = [root, ...root.querySelectorAll('*')];
    if (sourceElements.length > elementMax) {
      throw new Error('application design has too many SVG elements');
    }
    const sourceIds = new Set();
    for (const element of sourceElements) {
      const id = element.getAttribute?.('id')?.trim();
      if (!id) continue;
      if (sourceIds.has(id)) throw new Error('application design SVG ids must be unique');
      sourceIds.add(id);
    }
    async function visibleRegionText(regions, alphaFloor, visibleTextMaxChars) {
      const paintHasAlpha = (value) => {
        const paint = (value || '').trim().toLowerCase();
        if (!paint || paint === 'none' || paint === 'transparent') return false;
        const slashAlpha = paint.match(
          /^rgba?\([^/]+\/\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?%?)\s*\)$/
        );
        const commaAlpha = paint.match(
          /^rgba\(\s*[^,]+,\s*[^,]+,\s*[^,]+,\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?%?)\s*\)$/
        );
        const alpha = slashAlpha?.[1] || commaAlpha?.[1];
        if (!alpha) return true;
        const normalized = alpha.endsWith('%') ? parseFloat(alpha) / 100 : parseFloat(alpha);
        return !Number.isFinite(normalized) || normalized > 0;
      };
      const textPaints = (element, style) => {
        if (element instanceof SVGTextContentElement) {
          const fill = paintHasAlpha(style.fill) && parseFloat(style.fillOpacity) > 0;
          const stroke = paintHasAlpha(style.stroke) && parseFloat(style.strokeOpacity) > 0 &&
            parseFloat(style.strokeWidth) > 0;
          return fill || stroke;
        }
        const clippedBackground = (style.backgroundClip || style.webkitBackgroundClip || '')
          .split(',').some((clip) => clip.trim() === 'text') &&
          (paintHasAlpha(style.backgroundColor) ||
            (style.backgroundImage && style.backgroundImage !== 'none'));
        const fill = paintHasAlpha(style.webkitTextFillColor || style.color);
        const stroke = paintHasAlpha(style.webkitTextStrokeColor) &&
          parseFloat(style.webkitTextStrokeWidth) > 0;
        return clippedBackground || fill || stroke;
      };
      const visuallyHidden = (style, box) =>
        style.position === 'absolute' && style.overflow === 'hidden' &&
        box.width <= 2 && box.height <= 2 &&
        (style.clip !== 'auto' || style.clipPath !== 'none');
      const visible = (element, box) => {
        if (box.width === 0 || box.height === 0 || element.closest('[aria-hidden="true"]')) {
          return false;
        }
        const closedDetails = element.closest('details:not([open])');
        if (closedDetails) {
          const summary = element.closest('summary');
          if (!summary || summary.parentElement !== closedDetails) return false;
        }
        for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
          const style = getComputedStyle(ancestor);
          const ancestorBox = ancestor.getBoundingClientRect();
          if (style.display === 'none' || style.visibility === 'hidden') return false;
          if (style.contentVisibility === 'hidden') return false;
          if (parseFloat(style.opacity) < alphaFloor) return false;
          if (visuallyHidden(style, ancestorBox)) return false;
        }
        return true;
      };
      const nodes = [];
      for (const region of regions) {
        const walker = document.createTreeWalker(region, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const words = node.data.replace(/\s+/g, ' ').trim();
          const element = node.parentElement;
          if (!words || !element || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(element.tagName)) {
            continue;
          }
          const range = document.createRange();
          range.selectNodeContents(node);
          const rects = Array.from(range.getClientRects());
          const box = range.getBoundingClientRect();
          const style = getComputedStyle(element);
          if (!visible(element, box) || !textPaints(element, style)) continue;
          nodes.push({ region, node, element, words, rects });
        }
      }
      if (!nodes.length) return regions.map(() => '');
      const pageWidth = Math.max(document.documentElement.scrollWidth, window.innerWidth);
      const pageHeight = Math.max(document.documentElement.scrollHeight, window.innerHeight);
      const rootMargin = [
        Math.max(0, window.scrollY),
        Math.max(0, pageWidth - window.scrollX - window.innerWidth),
        Math.max(0, pageHeight - window.scrollY - window.innerHeight),
        Math.max(0, window.scrollX),
      ].map((value) => `${value}px`).join(' ');
      const intersections = await new Promise((resolve) => {
        const targets = Array.from(new Set(nodes.map(({ element }) => element)));
        const measured = new Map();
        const observer = new IntersectionObserver((entries) => {
          for (const entry of entries) measured.set(entry.target, entry.intersectionRect);
          if (measured.size !== targets.length) return;
          observer.disconnect();
          resolve(measured);
        }, { rootMargin });
        targets.forEach((element) => observer.observe(element));
      });
      const overlaps = (first, second) =>
        first.width > 0 && first.height > 0 && second.width > 0 && second.height > 0 &&
        Math.max(first.left, second.left) < Math.min(first.right, second.right) &&
        Math.max(first.top, second.top) < Math.min(first.bottom, second.bottom);
      const paintedAtPoint = (node, rect, bounds) => {
        const left = Math.max(bounds.left, rect.left);
        const right = Math.min(bounds.right, rect.right);
        const top = Math.max(bounds.top, rect.top);
        const bottom = Math.min(bounds.bottom, rect.bottom);
        if (left >= right || top >= bottom) return false;
        const parent = node.parentElement;
        return [0.2, 0.5, 0.8].some((part) => {
          const x = left + (right - left) * part;
          const y = top + (bottom - top) / 2;
          const caret = document.caretRangeFromPoint(x, y);
          if (caret && (caret.startContainer === node || parent.contains(caret.startContainer))) {
            return true;
          }
          const hits = document.elementsFromPoint(x, y);
          const parentIndex = hits.findIndex((hit) => hit === parent || parent.contains(hit));
          if (parentIndex < 0) return false;
          return hits.slice(0, parentIndex).every((hit) => {
            const style = getComputedStyle(hit);
            const background = paintHasAlpha(style.backgroundColor) ? style.backgroundColor : null;
            const alpha = background?.match(
              /(?:rgba?\([^/]+\/\s*|rgba\(\s*[^,]+,\s*[^,]+,\s*[^,]+,\s*)([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?%?)/
            )?.[1];
            const backgroundAlpha = alpha
              ? (alpha.endsWith('%') ? parseFloat(alpha) / 100 : parseFloat(alpha))
              : (background ? 1 : 0);
            return parseFloat(style.opacity) < 1 ||
              (backgroundAlpha < 0.999 && style.backgroundImage === 'none');
          });
        });
      };
      const viewport = {
        left: 0,
        right: window.innerWidth,
        top: 0,
        bottom: window.innerHeight,
        width: window.innerWidth,
        height: window.innerHeight,
      };
      const parts = new Map(regions.map((region) => [region, []]));
      for (const { region, node, element, words, rects } of nodes) {
        const intersection = intersections.get(element);
        if (intersection && rects.some((rect) =>
          overlaps(rect, intersection) &&
          (!overlaps(rect, viewport) || paintedAtPoint(node, rect, viewport))
        )) {
          parts.get(region).push(words);
        }
      }
      return regions.map((region) =>
        Array.from(parts.get(region).join(' ').replace(/\s+/g, ' ').trim())
          .slice(0, visibleTextMaxChars).join('')
      );
    }
    const evidence = [];
    const drawings = [];
    for (const element of root.querySelectorAll(drawingElements)) {
      if (element.closest('defs,symbol,clipPath,mask,marker,pattern')) continue;
      let visible = true;
      for (let current = element; current && current !== root.parentElement;
        current = current.parentElement) {
        const style = getComputedStyle(current);
        if ((style.clipPath || 'none') !== 'none' || (style.maskImage || 'none') !== 'none' ||
            (style.filter || 'none') !== 'none') {
          throw new Error(
            'application design native bounds do not support clip, mask, or filter effects'
          );
        }
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
            parseFloat(style.opacity) === 0) {
          visible = false;
          break;
        }
      }
      if (!visible) continue;
      const style = getComputedStyle(element);
      const fillVisible = style.fill !== 'none' && parseFloat(style.fillOpacity) > 0;
      const strokeVisible = style.stroke !== 'none' && parseFloat(style.strokeOpacity) > 0 &&
        parseFloat(style.strokeWidth) > 0;
      if (!fillVisible && !strokeVisible && element.localName !== 'image') continue;
      const box = element.getBBox();
      const matrix = element.getCTM();
      if (!matrix || (box.width === 0 && box.height === 0)) continue;
      const points = [
        new DOMPoint(box.x, box.y),
        new DOMPoint(box.x + box.width, box.y),
        new DOMPoint(box.x, box.y + box.height),
        new DOMPoint(box.x + box.width, box.y + box.height),
      ].map((point) => point.matrixTransform(matrix));
      const bounds = {
        left: Math.min(...points.map((point) => point.x)),
        top: Math.min(...points.map((point) => point.y)),
        right: Math.max(...points.map((point) => point.x)),
        bottom: Math.max(...points.map((point) => point.y)),
      };
      const overrun = [
        ['left', Math.max(0, -bounds.left)],
        ['top', Math.max(0, -bounds.top)],
        ['right', Math.max(0, bounds.right - viewport.width)],
        ['bottom', Math.max(0, bounds.bottom - viewport.height)],
      ];
      const region = element.closest('[data-app-region]')?.getAttribute('data-app-region') || '-';
      const excerpt = element.localName === 'text'
        ? ` text=${JSON.stringify((element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40))}`
        : '';
      drawings.push({ element, bounds, region, excerpt });
      for (const [edge, amount] of overrun) {
        if (amount <= 0.01 || evidence.length >= 4) continue;
        evidence.push(
          `region=${region} tag=${element.localName}${excerpt} edge=${edge} ` +
          `overflow=${Math.ceil(amount - 0.001)}px`
        );
      }
    }
    if (evidence.length) {
      throw new Error(`application design extends outside its viewBox: ${evidence.join('; ')}`);
    }

    const intentionalComposition = (element) => element.closest(
      '[data-slot="avatar-stack"], [data-slot="chart"], [data-slot="meter"], ' +
      '[data-slot="segmented"], [data-slot="switch"], [data-slot="checkbox"]'
    );
    const overlapSize = (first, second) => ({
      width: Math.min(first.right, second.right) - Math.max(first.left, second.left),
      height: Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top),
    });
    const collisionEvidence = [];
    const texts = drawings.filter(({ element }) => element.localName === 'text');
    const rectangles = drawings.filter(({ element }) => element.localName === 'rect');
    for (let firstIndex = 0; firstIndex < texts.length; firstIndex += 1) {
      const first = texts[firstIndex];
      for (let secondIndex = firstIndex + 1; secondIndex < texts.length; secondIndex += 1) {
        const second = texts[secondIndex];
        if (first.region !== second.region) continue;
        const intentional = intentionalComposition(first.element);
        if (intentional && intentional === intentionalComposition(second.element)) continue;
        const overlap = overlapSize(first.bounds, second.bounds);
        if (overlap.width <= internalOverlapSlop || overlap.height <= internalOverlapSlop) continue;
        const width = Math.round(overlap.width * 10) / 10;
        const height = Math.round(overlap.height * 10) / 10;
        collisionEvidence.push(
          `region=${first.region}${first.excerpt} overlaps${second.excerpt} by ${width}x${height}px`
        );
        if (collisionEvidence.length === internalOverlapMax) break;
      }
      if (collisionEvidence.length === internalOverlapMax) break;
    }
    for (const text of texts) {
      if (collisionEvidence.length === internalOverlapMax) break;
      if (intentionalComposition(text.element)) continue;
      for (const rectangle of rectangles) {
        if (text.region !== rectangle.region) continue;
        const overlap = overlapSize(text.bounds, rectangle.bounds);
        if (overlap.width <= internalOverlapSlop || overlap.height <= internalOverlapSlop) continue;
        const contained = text.bounds.left >= rectangle.bounds.left - internalOverlapSlop &&
          text.bounds.right <= rectangle.bounds.right + internalOverlapSlop &&
          text.bounds.top >= rectangle.bounds.top - internalOverlapSlop &&
          text.bounds.bottom <= rectangle.bounds.bottom + internalOverlapSlop;
        if (contained) continue;
        const width = Math.round(overlap.width * 10) / 10;
        const height = Math.round(overlap.height * 10) / 10;
        collisionEvidence.push(
          `region=${text.region}${text.excerpt} crosses tag=rect by ${width}x${height}px`
        );
        if (collisionEvidence.length === internalOverlapMax) break;
      }
    }
    if (collisionEvidence.length) {
      throw new Error(
        `application design has accidental internal overlap: ${collisionEvidence.join('; ')}`
      );
    }
    const namedRegions = Array.from(root.querySelectorAll('[data-app-region]'))
      .map((element) => ({
        element,
        name: (element.getAttribute('data-app-region') || '').trim(),
      }))
      .filter(({ name }) => Boolean(name))
      .slice(0, regionMax);
    const names = namedRegions.map(({ name }) => name);
    const visibleTexts = await visibleRegionText(
      namedRegions.map(({ element }) => element),
      alphaFloor,
      visibleTextMaxChars
    );
    const visibleTextByName = new Map(
      namedRegions.map(({ name }, index) => [name, visibleTexts[index]])
    );
    const presentation = new Set(presentationProperties);
    const serializer = new XMLSerializer();
    const encoder = new TextEncoder();
    const source = root.outerHTML;
    const sourceShape = () => JSON.stringify([root, ...root.querySelectorAll('*')].map(
      (element) => [
        element.tagName,
        Array.from(element.attributes).map((attribute) => [attribute.name, attribute.value]),
      ]
    ));
    const shape = sourceShape();
    const originals = [root, ...root.querySelectorAll('*')];
    if (originals.length > elementMax) {
      throw new Error('application design has too many SVG elements');
    }

    const ruleProperties = new Set();
    const collectRuleProperties = (rules) => {
      for (const rule of rules) {
        if (rule.style) {
          for (let index = 0; index < rule.style.length; index += 1) {
            ruleProperties.add(rule.style[index]);
          }
        }
        if (rule.cssRules) collectRuleProperties(rule.cssRules);
      }
    };
    for (const element of root.querySelectorAll('style,link[rel="stylesheet"]')) {
      if (element.sheet) collectRuleProperties(element.sheet.cssRules);
    }
    if (ruleProperties.delete('all')) {
      for (const property of getComputedStyle(root)) ruleProperties.add(property);
    }
    if (ruleProperties.size > propertyMax) {
      throw new Error('application design uses too many style properties');
    }

    const animatedProperties = new Map();
    for (const animation of root.querySelectorAll(
      'animate, animateMotion, animateTransform, set'
    )) {
      const target = animation.targetElement || animation.parentElement;
      if (!target) continue;
      const property = ['animateMotion', 'animateTransform'].includes(animation.localName)
        ? 'transform'
        : (animation.getAttribute('attributeName') || '').trim();
      if (!property) continue;
      if (!animatedProperties.has(target)) animatedProperties.set(target, new Set());
      animatedProperties.get(target).add(property);
    }
    const animatedAttributeValue = (element, property) => {
      let value = null;
      if (property === 'points' && element.animatedPoints) {
        if (element.animatedPoints.numberOfItems > animatedPointMax) {
          throw new Error('application design animation is too large');
        }
        const points = [];
        for (let index = 0; index < element.animatedPoints.numberOfItems; index += 1) {
          const point = element.animatedPoints.getItem(index);
          if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) {
            throw new Error('application design animation is invalid');
          }
          points.push(`${point.x},${point.y}`);
        }
        value = points.join(' ');
      } else if (property === 'viewBox' && element.viewBox?.animVal) {
        const box = element.viewBox.animVal;
        const values = [box.x, box.y, box.width, box.height];
        if (!values.every(Number.isFinite)) {
          throw new Error('application design animation is invalid');
        }
        value = values.join(' ');
      } else {
        const animated = element[property]?.animVal;
        if (typeof animated === 'string' || typeof animated === 'number') value = String(animated);
        else if (typeof animated?.valueAsString === 'string') value = animated.valueAsString;
        else if (typeof animated?.value === 'number' && Number.isFinite(animated.value)) {
          value = String(animated.value);
        }
      }
      if (value === null) return null;
      if (encoder.encode(value).length > animatedAttributeByteMax) {
        throw new Error('application design animation is too large');
      }
      return value;
    };

    const clone = root.cloneNode(true);
    const clones = [clone, ...clone.querySelectorAll('*')];
    let declarations = 0;
    let cloneBytes = encoder.encode(serializer.serializeToString(clone)).length;
    for (let index = 0; index < originals.length; index += 1) {
      const original = originals[index];
      const target = clones[index];
      const properties = new Set(ruleProperties);
      const inline = document.createElementNS('http://www.w3.org/1999/xhtml', 'span').style;
      inline.cssText = original.getAttribute('style') || '';
      for (let property = 0; property < inline.length; property += 1) {
        properties.add(inline[property]);
      }
      for (const attribute of original.attributes) {
        if (original.style && presentation.has(attribute.name)) properties.add(attribute.name);
      }
      for (const property of animatedProperties.get(original) || []) properties.add(property);
      if (properties.size > propertyMax) {
        throw new Error('application design uses too many style properties');
      }
      const computed = getComputedStyle(original);
      const frozen = document.createElementNS('http://www.w3.org/1999/xhtml', 'span').style;
      for (const property of properties) {
        const value = computed.getPropertyValue(property);
        if (value) {
          frozen.setProperty(property, value, 'important');
          declarations += 1;
          if (declarations > declarationMax) {
            throw new Error('application design style is too large');
          }
        } else if ((animatedProperties.get(original) || new Set()).has(property)) {
          const animated = animatedAttributeValue(original, property);
          if (animated === null) throw new Error('application design animation is unsupported');
          target.setAttribute(property, animated);
        }
      }
      const frozenBytes = encoder.encode(frozen.cssText).length;
      if (cloneBytes + frozenBytes * 6 + 32 > cloneByteMax) {
        throw new Error('application design rendered form is too large');
      }
      cloneBytes += frozenBytes * 6 + 32;
      if (frozen.cssText) target.setAttribute('style', frozen.cssText);
      else target.removeAttribute('style');
    }
    clone.querySelectorAll('style,link').forEach((element) => element.remove());
    clone.querySelectorAll('animate, animateMotion, animateTransform, set')
      .forEach((element) => element.remove());
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('width', String(viewport.width));
    clone.setAttribute('height', String(viewport.height));
    clone.setAttribute(
      'style',
      `${clone.getAttribute('style') || ''};background:transparent!important`
    );
    if (encoder.encode(serializer.serializeToString(clone)).length > cloneByteMax) {
      throw new Error('application design rendered form is too large');
    }

    const definitionTags = new Set([
      'clippath', 'defs', 'filter', 'lineargradient', 'marker', 'mask', 'metadata',
      'pattern', 'radialgradient', 'symbol', 'title',
    ]);
    const isolate = (name) => {
      const isolated = clone.cloneNode(true);
      const elements = [isolated, ...isolated.querySelectorAll('*')];
      const retained = new Set([isolated]);
      const retainTree = (element) => {
        for (let current = element; current; current = current.parentElement) retained.add(current);
        retained.add(element);
        element.querySelectorAll('*').forEach((descendant) => retained.add(descendant));
      };
      for (const element of elements) {
        if (definitionTags.has(element.localName.toLowerCase())) retainTree(element);
      }
      const target = name
        ? elements.find((element) =>
          (element.getAttribute?.('data-app-region') || '').trim() === name
        )
        : null;
      if (target) retainTree(target);
      const ids = new Map();
      for (const element of elements) {
        const id = element.getAttribute?.('id')?.trim();
        if (!id) continue;
        if (ids.has(id)) throw new Error('application design SVG ids must be unique');
        ids.set(id, element);
      }
      const references = (element) => {
        const found = new Set();
        for (const attribute of Array.from(element.attributes || [])) {
          const value = attribute.value.trim();
          if (attribute.localName === 'href' && value.startsWith('#')) found.add(value.slice(1));
          for (const match of value.matchAll(
            /url\(\s*(['"]?)#([A-Za-z_][\w:.-]*)\1\s*\)/g
          )) found.add(match[2]);
        }
        return found;
      };
      const visitedReferences = new Set();
      let retainedCount = -1;
      let referenceDefinitions = null;
      while (retainedCount !== retained.size) {
        retainedCount = retained.size;
        for (const element of Array.from(retained)) {
          for (const id of references(element)) {
            if (visitedReferences.has(id)) continue;
            visitedReferences.add(id);
            if (visitedReferences.size > elementMax) {
              throw new Error('application design has too many SVG references');
            }
            const reference = ids.get(id);
            if (!reference) continue;
            if (!retained.has(reference)) {
              referenceDefinitions ||= isolated.querySelector('defs');
              if (!referenceDefinitions) {
                referenceDefinitions = document.createElementNS(
                  'http://www.w3.org/2000/svg',
                  'defs'
                );
                isolated.prepend(referenceDefinitions);
                retained.add(referenceDefinitions);
              }
              referenceDefinitions.append(reference);
            }
            retainTree(reference);
          }
        }
      }
      for (const element of elements) {
        if (retained.has(element)) continue;
        element.setAttribute(
          'style',
          `${element.getAttribute('style') || ''};display:none!important`
        );
      }
      return isolated;
    };
    const canvas = document.createElementNS('http://www.w3.org/1999/xhtml', 'canvas');
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    const render = async (isolated) => {
      const serialized = serializer.serializeToString(isolated);
      if (encoder.encode(serialized).length > cloneByteMax) {
        throw new Error('application design rendered form is too large');
      }
      const objectUrl = URL.createObjectURL(new Blob(
        [serialized],
        { type: 'image/svg+xml' }
      ));
      const image = new Image();
      try {
        await new Promise((resolve, reject) => {
          image.addEventListener('load', resolve, { once: true });
          image.addEventListener('error', () => reject(new Error('design region did not render')), {
            once: true,
          });
          image.src = objectUrl;
        });
        context.clearRect(0, 0, viewport.width, viewport.height);
        context.drawImage(image, 0, 0, viewport.width, viewport.height);
        return context.getImageData(0, 0, viewport.width, viewport.height).data;
      } finally {
        image.src = '';
        URL.revokeObjectURL(objectUrl);
      }
    };
    const baseline = await render(isolate(null));
    const regions = [];
    for (const name of names) {
      const pixels = await render(isolate(name));
      let left = viewport.width;
      let right = -1;
      let top = viewport.height;
      let bottom = -1;
      for (let pixel = 0; pixel < viewport.width * viewport.height; pixel += 1) {
        const offset = pixel * 4;
        if (pixels[offset + 3] / 255 < alphaFloor) continue;
        if (
          pixels[offset] === baseline[offset] &&
          pixels[offset + 1] === baseline[offset + 1] &&
          pixels[offset + 2] === baseline[offset + 2] &&
          pixels[offset + 3] === baseline[offset + 3]
        ) continue;
        const x = pixel % viewport.width;
        const y = Math.floor(pixel / viewport.width);
        left = Math.min(left, x);
        right = Math.max(right, x);
        top = Math.min(top, y);
        bottom = Math.max(bottom, y);
      }
      if (right < left || bottom < top) continue;
      regions.push({
        name,
        left: left / viewport.width,
        top: top / viewport.height,
        width: (right + 1 - left) / viewport.width,
        height: (bottom + 1 - top) / viewport.height,
        aboveFold: top < Math.min(initialFold, viewport.height),
        visibleText: visibleTextByName.get(name) || '',
      });
    }
    if (root.outerHTML !== source || sourceShape() !== shape) {
      throw new Error('application design source changed during validation');
    }
    if (encoder.encode(JSON.stringify(regions)).length > outputByteMax) {
      throw new Error('application design measurement is too large');
    }
    return regions;
  }, {
    alphaFloor: DESIGN_ALPHA_FLOOR,
    animatedAttributeByteMax: DESIGN_ANIMATED_ATTRIBUTE_BYTE_MAX,
    animatedPointMax: DESIGN_ANIMATED_POINT_MAX,
    cloneByteMax: DESIGN_CLONE_BYTE_MAX,
    declarationMax: DESIGN_DECLARATION_MAX,
    elementMax: DESIGN_ELEMENT_MAX,
    outputByteMax: DESIGN_OUTPUT_BYTE_MAX,
    presentationProperties: Array.from(SVG_PRESENTATION_PROPERTIES),
    propertyMax: DESIGN_PROPERTY_MAX,
    regionMax: DESIGN_REGION_MAX,
    visibleTextMaxChars: DESIGN_VISIBLE_TEXT_MAX_CHARS,
    drawingElements: DESIGN_DRAWING_ELEMENTS,
    initialFold: DESIGN_INITIAL_FOLD,
    internalOverlapMax: DESIGN_INTERNAL_OVERLAP_MAX,
    internalOverlapSlop: DESIGN_INTERNAL_OVERLAP_SLOP,
    viewport,
  });
}

async function nativeDesignGeometry(page, laneWidth) {
  const geometry = await page.evaluate(({ initialFold, maximum, width }) => {
    const root = document.documentElement;
    const box = root.viewBox?.baseVal;
    const values = box ? [box.x, box.y, box.width, box.height] : [];
    if (values.length !== 4 || !values.every(Number.isFinite) || box.x !== 0 || box.y !== 0 ||
        box.width !== width || !Number.isInteger(box.height) || box.height < initialFold ||
        box.height > maximum || root.getAttribute('width') !== String(width) ||
        root.getAttribute('height') !== String(box.height)) {
      throw new Error(
        `application design must use viewBox="0 0 ${width} H", width="${width}", and a ` +
        'matching integer height H from 844 through 4096'
      );
    }
    return { width: box.width, height: box.height };
  }, {
    initialFold: DESIGN_INITIAL_FOLD,
    maximum: DESIGN_NATIVE_DIMENSION_MAX,
    width: laneWidth,
  });
  await page.setViewportSize(geometry);
  return geometry;
}

async function measuredDesign(browser, laneWidth, svgInput, previewPath) {
  if (!path.isAbsolute(svgInput)) throw new Error('application design path must be absolute');
  const svgPath = fs.realpathSync(svgInput);
  const context = await browser.newContext({
    viewport: { width: laneWidth, height: DESIGN_INITIAL_HEIGHT },
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
  });
  let blockedRequests = 0;
  await context.route(/^https?:/, (route) => {
    blockedRequests += 1;
    return route.abort();
  });
  try {
    const page = await context.newPage();
    const source = fs.readFileSync(svgPath).toString('base64');
    await page.goto(`data:image/svg+xml;base64,${source}`, { waitUntil: 'load' });
    const geometry = await nativeDesignGeometry(page, laneWidth);
    const regions = await renderedDesignRegions(page, geometry);
    if (blockedRequests) throw new Error('application design must not contain active or external content');
    if (previewPath) await page.screenshot({ path: previewPath });
    return { height: geometry.height, regions };
  } finally {
    await context.close();
  }
}

async function designOnly(laneWidth, svgPath, previewPath) {
  const browser = await chromium.launch();
  try {
    const { regions } = await measuredDesign(browser, laneWidth, svgPath, previewPath);
    process.stdout.write(JSON.stringify(regions));
  } finally {
    await browser.close();
  }
}

async function interactionAudit(browser, url) {
  const source = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
  });
  const index = await source.newPage();
  const indexResourceProblems = trackApplicationResources(index, url);
  await index.goto(url, { waitUntil: 'load' });
  await assertApplicationResources(indexResourceProblems);
  const indexFrame = await applicationFrame(index);
  await waitForApplicationReady(indexFrame);
  await indexFrame.evaluate(() => document.fonts.ready);
  await assertApplicationResources(indexResourceProblems);
  const controls = await indexFrame.evaluate(controlCandidates);
  const initial = JSON.parse(await indexFrame.evaluate(visibleState));
  await source.close();

  const successes = [];
  const problems = [];
  const states = [initial.parts];
  const calls = [];
  const navigations = [];
  const reloadStates = [];
  const paths = controls.map((control) => [control]);
  const known = new Set(controls.map((control) => control.name + '|' + control.selector));
  let attempts = 0;
  while (paths.length && attempts++ < 12) {
    const path = paths.shift();
    const control = path[path.length - 1];
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      colorScheme: 'light',
    });
    const page = await context.newPage();
    const resourceProblems = trackApplicationResources(page, url);
    page.on('console', (message) => {
      if (message.type() === 'error' && problems.length < 8) {
        problems.push(`console: ${message.text()}`.slice(0, 500));
      }
    });
    page.on('pageerror', (error) => {
      if (problems.length < 8) problems.push(`pageerror: ${error.message}`.slice(0, 500));
    });
    try {
      await page.goto(url, { waitUntil: 'load' });
      await assertApplicationResources(resourceProblems);
      const frame = await applicationFrame(page);
      await waitForApplicationReady(frame, APPLICATION_LIFECYCLE_TIMEOUT_MS, problems);
      await frame.evaluate(() => document.fonts.ready);
      await assertApplicationResources(resourceProblems);
      let before = '';
      let after = '';
      for (let index = 0; index < path.length; index += 1) {
        const step = path[index];
        const target = frame.locator(step.selector);
        const stepBefore = await frame.evaluate(visibleState);
        if (index === path.length - 1) before = stepBefore;
        const epoch = await beginApplicationObservation(frame);
        if (step.tag === 'select') {
          const values = await target.locator('option').evaluateAll((options) =>
            options.map((option) => option.value)
          );
          const current = await target.inputValue();
          const next = values.find((value) => value !== current);
          if (next !== undefined) await target.selectOption(next);
        } else {
          await target.click({ timeout: 2000 });
        }
        await endApplicationObservation(frame, epoch, APPLICATION_INTERACTION_TIMEOUT_MS);
        const stepAfter = await waitForApplicationInteraction(frame, stepBefore);
        await frame.evaluate(() => document.fonts.ready);
        await assertApplicationResources(resourceProblems);
        if (index === path.length - 1) after = stepAfter || stepBefore;
      }
      const controlCalls = await page.evaluate(() => window.__ufoCalls || []);
      const controlNavigations = await page.evaluate(() => window.__ufoNavigations || []);
      calls.push(...controlCalls);
      navigations.push(...controlNavigations.map((to) => ({ control: control.name, to })));
      if (before !== after) {
        successes.push(control);
        states.push(JSON.parse(after).parts);
      }
      if (path.length < 2) {
        const discovered = await frame.evaluate(controlCandidates);
        for (const child of discovered.reverse()) {
          const key = child.name + '|' + child.selector;
          if (known.has(key)) continue;
          known.add(key);
          controls.push(child);
          paths.unshift([...path, child]);
        }
      }
      if (controlCalls.some((call) =>
        call.method === 'POST' && call.path === 'objects/eval_app_action'
      )) {
        await page.reload({ waitUntil: 'load' });
        await assertApplicationResources(resourceProblems);
        const reloadedFrame = await applicationFrame(page);
        await waitForApplicationReady(reloadedFrame);
        await reloadedFrame.evaluate(() => document.fonts.ready);
        await assertApplicationResources(resourceProblems);
        reloadStates.push({ control: control.name, ...JSON.parse(
          await reloadedFrame.evaluate(visibleState)
        ) });
      }
    } catch (error) {
      if (!(error instanceof ApplicationLifecycleError) && !String(error).includes('Timeout')) {
        problems.push(`interaction: ${String(error)}`);
      }
    } finally {
      await context.close();
    }
  }
  return {
    controls,
    successes,
    states: Array.from(new Map(states.map((state) => [JSON.stringify(state), state])).values()),
    calls,
    navigations,
    reloadStates,
    console: Array.from(new Set(problems)).slice(0, 8),
  };
}

async function interactiveDocument(frame) {
  return frame.evaluate(async () => {
    const source = await (await fetch(location.href)).text();
    const documentCopy = new DOMParser().parseFromString(source, 'text/html');
    const asDataUrl = async (resource) => {
      const blob = await (await fetch(resource)).blob();
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener('load', () => resolve(reader.result), { once: true });
        reader.addEventListener('error', () => reject(reader.error), { once: true });
        reader.readAsDataURL(blob);
      });
    };
    const inlineCssResources = async (css, baseUrl) => {
      const replacements = new Map();
      for (const match of css.matchAll(/url\(\s*(?:"([^"]+)"|'([^']+)'|([^)'"\s][^)]*?))\s*\)/g)) {
        const value = (match[1] || match[2] || match[3]).trim();
        if (value.startsWith('data:') || value.startsWith('blob:') || value.startsWith('#')) continue;
        const resource = new URL(value, baseUrl);
        if (resource.origin !== location.origin) continue;
        replacements.set(match[0], `url("${await asDataUrl(resource)}")`);
      }
      for (const [source, replacement] of replacements) css = css.split(source).join(replacement);
      return css;
    };
    for (const script of documentCopy.querySelectorAll('script[src]')) {
      const resource = new URL(script.getAttribute('src'), location.href);
      if (resource.protocol === 'data:') continue;
      if (resource.origin !== location.origin) {
        script.remove();
        continue;
      }
      script.setAttribute('src', await asDataUrl(resource));
    }
    for (const link of documentCopy.querySelectorAll('link[rel="stylesheet"][href]')) {
      const resource = new URL(link.getAttribute('href'), location.href);
      if (resource.origin !== location.origin) {
        link.remove();
        continue;
      }
      const style = documentCopy.createElement('style');
      const css = await (await fetch(resource)).text();
      style.textContent = await inlineCssResources(css, resource.href);
      link.replaceWith(style);
    }
    for (const image of documentCopy.querySelectorAll('img[src]')) {
      const resource = new URL(image.getAttribute('src'), location.href);
      if (resource.protocol === 'data:') continue;
      if (resource.origin !== location.origin) {
        image.removeAttribute('src');
        continue;
      }
      image.setAttribute('src', await asDataUrl(resource));
    }
    return `<!doctype html>\n${documentCopy.documentElement.outerHTML}`;
  });
}

async function main() {
  if (process.argv[2] === '--design') {
    const laneWidth = Number(process.argv[3]);
    if (!Number.isInteger(laneWidth) || laneWidth <= 0 || !process.argv[4] ||
        process.argv.length < 5 || process.argv.length > 6) {
      console.error(
        'usage: node app-audit.cjs --design <lane-width> <application-design.svg> [preview.png]'
      );
      process.exit(2);
    }
    await designOnly(laneWidth, process.argv[4], process.argv[5]);
    return;
  }
  const [
    root, reportPath, lightShot, darkShot, interactivePath, staticPath, lane, designPath,
  ] = process.argv.slice(2);
  const laneWidth = Number(lane);
  if (!root || !reportPath || !lightShot || !darkShot || !interactivePath || !staticPath ||
      !Number.isInteger(laneWidth) || laneWidth <= 0 ||
      process.argv.length < 9 || process.argv.length > 10) {
    console.error(
      'usage: node app-audit.cjs <application-root> <report.json> <light.png> <dark.png> <interactive.html> <static.html> <lane-width> [<application-design.svg>]'
    );
    process.exit(2);
  }
  const application = await validatedApplicationRoot(root);
  const { server, sockets, url } = await startApplicationServer(application);
  const shots = { light: lightShot, dark: darkShot };
  let browser = null;
  try {
    browser = await chromium.launch();
    // A design fault is a repair, not a broken run, and only the exit code can say which: leaving
    // the gate to match on the shape of a message would make prose the contract.
    let design = { height: DESIGN_INITIAL_FOLD, regions: [] };
    if (designPath) {
      try {
        design = await measuredDesign(browser, laneWidth, designPath, '');
      } catch (error) {
        console.error(error && error.message ? error.message : String(error));
        await closeApplicationAudit(browser, server, sockets);
        process.exit(DESIGN_FAULT_EXIT);
      }
    }
    const { height: designHeight, regions: designRegions } = design;
    const views = await Promise.all(VIEWS.map(async (view) => {
      const context = await browser.newContext({
        viewport: { width: view.width, height: view.height },
        colorScheme: view.scheme,
      });
      try {
    const page = await context.newPage();
    const problems = [];
    const resourceProblems = trackApplicationResources(page, url);
    page.on('console', (message) => {
      if (message.type() === 'error' && problems.length < 8) {
        problems.push(`console: ${message.text()}`.slice(0, 500));
      }
    });
    page.on('pageerror', (error) => {
      if (problems.length < 8) problems.push(`pageerror: ${error.message}`.slice(0, 500));
    });
    await page.goto(url, { waitUntil: 'load' });
    await assertApplicationResources(resourceProblems);
    const frame = await applicationFrame(page);
    await waitForApplicationReady(frame, APPLICATION_LIFECYCLE_TIMEOUT_MS, problems);
    await frame.evaluate(() => document.fonts.ready);
    await assertApplicationResources(resourceProblems);
    const measured = await measureApplication(frame, AA_FLOOR);
    const shot = view.shoot ? shots[view.scheme] : '';
    if (shot) await page.screenshot({ path: shot });
    if (view.scheme === 'light' && view.width === 1440) {
      fs.writeFileSync(interactivePath, await interactiveDocument(frame));
      const styles = await frame.evaluate(async () => {
        const asDataUrl = async (resource) => {
          const blob = await (await fetch(resource)).blob();
          return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.addEventListener('load', () => resolve(reader.result), { once: true });
            reader.addEventListener('error', () => reject(reader.error), { once: true });
            reader.readAsDataURL(blob);
          });
        };
        const inlineCssResources = async (css, baseUrl) => {
          const replacements = new Map();
          for (const match of css.matchAll(/url\(\s*(?:"([^"]+)"|'([^']+)'|([^)'"\s][^)]*?))\s*\)/g)) {
            const value = (match[1] || match[2] || match[3]).trim();
            if (value.startsWith('data:') || value.startsWith('blob:') || value.startsWith('#')) continue;
            const resource = new URL(value, baseUrl);
            if (resource.origin !== location.origin) continue;
            replacements.set(match[0], `url("${await asDataUrl(resource)}")`);
          }
          for (const [source, replacement] of replacements) css = css.split(source).join(replacement);
          return css;
        };
        const sheets = [];
        for (const sheet of document.styleSheets) {
          try {
            const css = Array.from(sheet.cssRules).map((rule) => rule.cssText).join('\n');
            sheets.push(await inlineCssResources(css, sheet.href || location.href));
          } catch {
            continue;
          }
        }
        return sheets.join('\n');
      });
      await frame.addStyleTag({ content: styles });
      await frame.evaluate(() => {
        document.querySelectorAll('script, link[rel="stylesheet"]').forEach((node) => node.remove());
      });
      fs.writeFileSync(staticPath, await frame.content());
    }
        return { scheme: view.scheme, width: view.width, shot, console: problems, ...measured };
      } finally {
        await context.close();
      }
    }));
    const interaction = await interactionAudit(browser, url);
    const report = { url, floor: AA_FLOOR, designHeight, designRegions, views, interaction };
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    for (const view of views) {
      console.log(
        `${view.scheme} ${view.width}: ${view.textUnderFloor} text under ${AA_FLOOR}:1 ` +
          '(3:1 is enough only at 24px, or at 18.66px bold), ' +
          `document ${view.documentWidth}x${view.documentHeight}px in ` +
          `${view.viewportWidth}x${view.viewportHeight}px, ` +
          `${view.clipped.length} clipped, ${view.console.length} console error(s)`
      );
      for (const item of view.text) {
        console.log(`   ${item.ratio}:1 ${item.px}px w${item.weight} ${item.selector} — "${item.text}"`);
      }
    }
    console.log(
      `${interaction.successes.length}/${interaction.controls.length} controls changed visible state`
    );
  } finally {
    await closeApplicationAudit(browser, server, sockets);
  }
}

async function run() {
  const reportPath = process.argv[2] === '--design' ? null : process.argv[3];
  if (reportPath) fs.rmSync(reportPath + APPLICATION_LIFECYCLE_DIAGNOSTIC_SUFFIX, { force: true });
  try {
    await main();
  } catch (error) {
    if (error instanceof ApplicationLifecycleError) {
      if (reportPath) fs.writeFileSync(reportPath + APPLICATION_LIFECYCLE_DIAGNOSTIC_SUFFIX, JSON.stringify({
        code: 'application_lifecycle',
        reason: error.message,
        snapshot: error.snapshot,
        problems: error.problems || [],
      }));
      process.exitCode = 3;
      return;
    }
    throw error;
  }
}

module.exports = {
  ApplicationLifecycleError,
  beginApplicationObservation,
  closeApplicationAudit,
  closeApplicationServer,
  endApplicationObservation,
  interactionAudit,
  measure,
  measureApplication,
  measuredDesign,
  assertApplicationResources,
  startApplicationServer,
  trackApplicationResources,
  validatedApplicationRoot,
  waitForApplicationReady,
};

if (require.main === module) void run();
