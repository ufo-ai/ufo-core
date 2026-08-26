// The measured half of ufo-app-bench, run inside the sandbox against the served page: rendered text
// contrast per element in both colour schemes, document width and leaf clipping at the desktop and
// the narrow width, console errors, and one screenshot per scheme for the vision judge.
//
// The script reports measurements, not verdicts. Every text leaf whose ratio is under the strictest
// AA floor is reported with its own size and weight, and the grader recomputes which threshold the
// element owed — so the numbers decide the case and this file cannot soften it. A page whose text
// all clears 4.5:1 reports no text entry at all.
//
// Usage: node app-audit.cjs <url> <report.json> <light.png> <dark.png> <interactive.html> <static.html> <design-url>
//        node app-audit.cjs --design <application-design.svg>
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

const AA_FLOOR = 4.5;
const DESIGN_ALPHA_FLOOR = 0.15;
const DESIGN_REGION_MAX = 6;
const DESIGN_VIEWPORT = { width: 1280, height: 800 };
const DESIGN_ELEMENT_MAX = 4096;
const DESIGN_PROPERTY_MAX = 96;
const DESIGN_DECLARATION_MAX = DESIGN_ELEMENT_MAX * DESIGN_PROPERTY_MAX;
const DESIGN_CLONE_BYTE_MAX = 2 * 1024 * 1024;
const DESIGN_OUTPUT_BYTE_MAX = 2048;
const DESIGN_ANIMATED_POINT_MAX = 4096;
const DESIGN_ANIMATED_ATTRIBUTE_BYTE_MAX = 128 * 1024;
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
  { scheme: 'light', width: 390, height: 844, shoot: false },
  { scheme: 'dark', width: 390, height: 844, shoot: false },
];

function measure(floor) {
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
    if (!visible(element, box)) continue;
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
    const key = `${selector}|${style.fontSize}|${style.fontWeight}|${rounded}`;
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
    });
  }

  const paintedAboveFold = (node, rect) => {
    const left = Math.max(0, rect.left);
    const right = Math.min(window.innerWidth, rect.right);
    const top = Math.max(0, rect.top);
    const bottom = Math.min(window.innerHeight, rect.bottom);
    if (left >= right || top >= bottom) return false;
    const points = [0.2, 0.5, 0.8];
    return points.some((part) => {
      const x = left + (right - left) * part;
      const y = top + (bottom - top) / 2;
      const range = document.caretRangeFromPoint(x, y);
      return (range && range.startContainer === node) ||
        document.elementsFromPoint(x, y).some(
          (element) => element === node.parentElement || node.parentElement.contains(element)
        );
    });
  };

  const rawRenderedText = document.body.innerText.slice(0, 40000);
  const renderedText = rawRenderedText.replace(/\s+/g, ' ').trim();
  const renderedParts = rawRenderedText.split(/\n+/)
    .map((part) => part.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const foldedRenderedText = renderedText.toLocaleLowerCase();
  const visibleRanges = [];
  let renderedCursor = 0;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const element = node.parentElement;
    if (!element || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(element.tagName)) continue;
    const words = node.data.replace(/\s+/g, ' ').trim();
    if (!words) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects());
    const box = range.getBoundingClientRect();
    if (!visible(element, box)) continue;
    const start = foldedRenderedText.indexOf(words.toLocaleLowerCase(), renderedCursor);
    if (start < 0) continue;
    const end = start + words.length;
    renderedCursor = end;
    if (rects.some((rect) => paintedAboveFold(node, rect))) {
      visibleRanges.push([start, end]);
    }
  }
  let aboveFoldText = '';
  let priorEnd = 0;
  for (const [start, end] of visibleRanges) {
    if (aboveFoldText && start > priorEnd) aboveFoldText += ' ';
    aboveFoldText += renderedText.slice(start, end);
    priorEnd = end;
  }
  aboveFoldText = aboveFoldText.replace(/\s+/g, ' ').trim().slice(0, 40000);

  const pageWidth = Math.max(document.documentElement.scrollWidth, window.innerWidth);
  const pageHeight = Math.max(document.documentElement.scrollHeight, window.innerHeight);
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

  const wider = [];
  const clipped = [];
  for (const element of document.querySelectorAll('*')) {
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    const style = getComputedStyle(element);
    if (!visible(element, box)) continue;
    const name =
      element.tagName.toLowerCase() +
      (element.className ? '.' + String(element.className).trim().split(/\s+/)[0] : '');
    if (box.right > window.innerWidth + 1 || box.left < -1) wider.push(name);
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
    textChecked: checked,
    textUnderFloor: underFloor,
    text,
    renderedText,
    renderedParts,
    aboveFoldText,
    regions,
    pastViewport: wider.slice(0, 12),
    clipped: clipped.slice(0, 8),
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
  await frame.waitForSelector('#root > *', { timeout: 15000 });
  return frame;
}

async function renderedDesignRegions(page) {
  return page.evaluate(async ({
    alphaFloor,
    animatedAttributeByteMax,
    animatedPointMax,
    cloneByteMax,
    declarationMax,
    elementMax,
    outputByteMax,
    presentationProperties,
    propertyMax,
    regionMax,
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
    const names = Array.from(root.querySelectorAll('[data-app-region]'))
      .map((element) => (element.getAttribute('data-app-region') || '').trim())
      .filter(Boolean)
      .slice(0, regionMax);
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
        aboveFold: true,
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
    viewport: DESIGN_VIEWPORT,
  });
}

async function designRegionAudit(browser, designUrl) {
  const context = await browser.newContext({
    viewport: DESIGN_VIEWPORT,
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
  });
  await context.route(/^https?:/, async (route) => {
    if (route.request().url() === designUrl) await route.continue();
    else await route.abort();
  });
  const page = await context.newPage();
  try {
    const response = await page.goto(designUrl, {
      waitUntil: 'load',
    });
    if (!response || !response.ok()) return [];
    return await renderedDesignRegions(page);
  } finally {
    await context.close();
  }
}

async function designOnly(svgPath) {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: DESIGN_VIEWPORT,
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
  });
  let blockedRequests = 0;
  await context.route(/^https?:/, (route) => {
    blockedRequests += 1;
    return route.abort();
  });
  const page = await context.newPage();
  try {
    const source = fs.readFileSync(svgPath).toString('base64');
    await page.goto(`data:image/svg+xml;base64,${source}`, { waitUntil: 'load' });
    const regions = await renderedDesignRegions(page);
    if (blockedRequests) throw new Error('application design must not contain active or external content');
    process.stdout.write(JSON.stringify(regions));
  } finally {
    await context.close();
    await browser.close();
  }
}

async function interactionAudit(browser, url) {
  const source = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
  });
  const index = await source.newPage();
  await index.goto(url, { waitUntil: 'load' });
  const indexFrame = await applicationFrame(index);
  await index.waitForTimeout(700);
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
      const frame = await applicationFrame(page);
      await page.waitForTimeout(300);
      let before = '';
      for (let index = 0; index < path.length; index += 1) {
        const step = path[index];
        const target = frame.locator(step.selector);
        if (index === path.length - 1) before = await frame.evaluate(visibleState);
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
        await page.waitForTimeout(index === path.length - 1 ? 300 : 100);
      }
      const after = await frame.evaluate(visibleState);
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
        const reloadedFrame = await applicationFrame(page);
        await page.waitForTimeout(300);
        reloadStates.push({ control: control.name, ...JSON.parse(
          await reloadedFrame.evaluate(visibleState)
        ) });
      }
    } catch (error) {
      if (!String(error).includes('Timeout')) problems.push(`interaction: ${String(error)}`);
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

(async () => {
  if (process.argv[2] === '--design') {
    if (!process.argv[3] || process.argv.length !== 4) {
      console.error('usage: node app-audit.cjs --design <application-design.svg>');
      process.exit(2);
    }
    await designOnly(process.argv[3]);
    return;
  }
  const [url, reportPath, lightShot, darkShot, interactivePath, staticPath, designUrl] = process.argv.slice(2);
  if (!url || !reportPath || !lightShot || !darkShot || !interactivePath || !staticPath || !designUrl) {
    console.error(
      'usage: node app-audit.cjs <url> <report.json> <light.png> <dark.png> <interactive.html> <static.html> <design-url>'
    );
    process.exit(2);
  }
  const applicationUrl = new URL(url);
  const acceptedDesignUrl = new URL(designUrl);
  if (
    acceptedDesignUrl.origin !== applicationUrl.origin ||
    acceptedDesignUrl.pathname !== '/accepted-design.svg' ||
    acceptedDesignUrl.search ||
    acceptedDesignUrl.hash
  ) {
    throw new Error('accepted application design URL is invalid');
  }
  const shots = { light: lightShot, dark: darkShot };
  const browser = await chromium.launch();
  try {
    const designRegions = await designRegionAudit(browser, acceptedDesignUrl.href);
    const views = await Promise.all(VIEWS.map(async (view) => {
      const context = await browser.newContext({
        viewport: { width: view.width, height: view.height },
        colorScheme: view.scheme,
      });
      try {
    const page = await context.newPage();
    const problems = [];
    page.on('console', (message) => {
      if (message.type() === 'error' && problems.length < 8) {
        problems.push(`console: ${message.text()}`.slice(0, 500));
      }
    });
    page.on('pageerror', (error) => {
      if (problems.length < 8) problems.push(`pageerror: ${error.message}`.slice(0, 500));
    });
    await page.goto(url, { waitUntil: 'load' });
    const frame = await applicationFrame(page);
    await page.waitForTimeout(700);
    const measured = await frame.evaluate(measure, AA_FLOOR);
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
    const report = { url, floor: AA_FLOOR, designRegions, views, interaction };
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
    await browser.close();
  }
})();
