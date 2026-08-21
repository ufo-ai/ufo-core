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
  const visuallyHidden = (element, style, box) =>
    element.closest('[aria-hidden="true"]') !== null ||
    (style.position === 'absolute' &&
      style.overflow === 'hidden' &&
      box.width <= 2 &&
      box.height <= 2 &&
      (style.clip !== 'auto' || style.clipPath !== 'none'));
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
    if (box.width === 0 || box.height === 0) continue;
    const style = getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (visuallyHidden(element, style, box)) continue;
    if (parseFloat(style.opacity) < 0.15) continue;
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

  const wider = [];
  const clipped = [];
  for (const element of document.querySelectorAll('*')) {
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    const style = getComputedStyle(element);
    if (visuallyHidden(element, style, box)) continue;
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
    text: (document.body.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 12000),
    controls,
  });
}

async function interactionAudit(browser, url) {
  const source = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
  });
  const index = await source.newPage();
  await index.goto(url, { waitUntil: 'load' });
  await index.waitForTimeout(700);
  const controls = await index.evaluate(controlCandidates);
  await source.close();

  const successes = [];
  const problems = [];
  for (const control of controls.slice(0, 12)) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      colorScheme: 'light',
    });
    const page = await context.newPage();
    page.on('console', (message) => {
      if (message.type() === 'error') problems.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
    try {
      await page.goto(url, { waitUntil: 'load' });
      await page.waitForTimeout(300);
      const target = page.locator(control.selector);
      const before = await page.evaluate(visibleState);
      if (control.tag === 'select') {
        const values = await target.locator('option').evaluateAll((options) =>
          options.map((option) => option.value)
        );
        const current = await target.inputValue();
        const next = values.find((value) => value !== current);
        if (next !== undefined) await target.selectOption(next);
      } else {
        await target.click({ timeout: 2000 });
      }
      await page.waitForTimeout(300);
      const after = await page.evaluate(visibleState);
      if (before !== after) successes.push(control);
    } catch (error) {
      if (!String(error).includes('Timeout')) problems.push(`interaction: ${String(error)}`);
    } finally {
      await context.close();
    }
  }
  return {
    controls,
    successes,
    console: Array.from(new Set(problems)).slice(0, 8),
  };
}

async function interactiveDocument(page) {
  return page.evaluate(async () => {
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
      style.textContent = await (await fetch(resource)).text();
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
  const views = [];
  for (const view of VIEWS) {
    const context = await browser.newContext({
      viewport: { width: view.width, height: view.height },
      colorScheme: view.scheme,
    });
    const page = await context.newPage();
    const problems = [];
    page.on('console', (message) => {
      if (message.type() === 'error') problems.push(`console: ${message.text()}`);
    });
    page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForTimeout(700);
    const measured = await page.evaluate(measure, AA_FLOOR);
    const shot = view.shoot ? shots[view.scheme] : '';
    if (shot) await page.screenshot({ path: shot });
    if (view.scheme === 'light' && view.width === 1440) {
      fs.writeFileSync(interactivePath, await interactiveDocument(page));
      const styles = await page.evaluate(() =>
        Array.from(document.styleSheets).flatMap((sheet) => {
          try {
            return Array.from(sheet.cssRules).map((rule) => rule.cssText);
          } catch {
            return [];
          }
        }).join('\n')
      );
      await page.addStyleTag({ content: styles });
      await page.evaluate(() => {
        document.querySelectorAll('script, link[rel="stylesheet"]').forEach((node) => node.remove());
      });
      fs.writeFileSync(staticPath, await page.content());
    }
    views.push({ scheme: view.scheme, width: view.width, shot, console: problems, ...measured });
    await context.close();
  }
  const interaction = await interactionAudit(browser, url);
  await browser.close();
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
})();
