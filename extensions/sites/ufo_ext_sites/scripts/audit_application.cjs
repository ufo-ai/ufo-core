// The measured half of ufo-app-bench, run inside the sandbox against the served page: rendered text
// contrast per element in both colour schemes, document width and leaf clipping at the desktop and
// the narrow width, console errors, and one screenshot per scheme for the vision judge.
//
// The script reports measurements, not verdicts. Every text leaf whose ratio is under the strictest
// AA floor is reported with its own size and weight, and the grader recomputes which threshold the
// element owed — so the numbers decide the case and this file cannot soften it. A page whose text
// all clears 4.5:1 reports no text entry at all.
//
// Usage: node app-audit.cjs <url> <report.json> <light.png> <dark.png> <interactive.html> <static.html>
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
const VIEWS = [
  { scheme: 'light', width: 1440, height: 900, shoot: true },
  { scheme: 'dark', width: 1440, height: 900, shoot: true },
  { scheme: 'light', width: 390, height: 844, shoot: false },
  { scheme: 'dark', width: 390, height: 844, shoot: false },
];

function measure(floor) {
  const parse = (value) => {
    const found = value.match(/rgba?\(([^)]+)\)/);
    if (!found) return null;
    const parts = found[1].split(',').map((part) => parseFloat(part));
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
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
  const [url, reportPath, lightShot, darkShot, interactivePath, staticPath] = process.argv.slice(2);
  if (!url || !reportPath || !lightShot || !darkShot || !interactivePath || !staticPath) {
    console.error(
      'usage: node app-audit.cjs <url> <report.json> <light.png> <dark.png> <interactive.html> <static.html>'
    );
    process.exit(2);
  }
  const shots = { light: lightShot, dark: darkShot };
  const browser = await chromium.launch();
  try {
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
    const report = { url, floor: AA_FLOOR, views, interaction };
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
