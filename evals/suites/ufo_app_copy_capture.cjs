const { chromium } = (() => {
  try {
    return require('playwright');
  } catch {
    return require('/usr/local/lib/node_modules/playwright');
  }
})();
const fs = require('fs');

(async () => {
  const [url, staticPath, reportPath] = process.argv.slice(2);
  if (!url || !staticPath || !reportPath) {
    console.error('usage: node app-copy-capture.cjs <url> <static.html> <audit.json>');
    process.exit(2);
  }
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForTimeout(700);
  const rendered = await page.evaluate(() => {
    const raw = document.body.innerText.slice(0, 40000);
    return {
      renderedText: raw.replace(/\s+/g, ' ').trim(),
      renderedParts: raw.split(/\n+/)
        .map((part) => part.replace(/\s+/g, ' ').trim()).filter(Boolean),
    };
  });
  fs.writeFileSync(reportPath, JSON.stringify({
    views: [{ scheme: 'light', width: 1440, ...rendered }],
  }, null, 2));
  const styles = await page.evaluate(() =>
    Array.from(document.styleSheets)
      .flatMap((sheet) => {
        try {
          return Array.from(sheet.cssRules).map((rule) => rule.cssText);
        } catch {
          return [];
        }
      })
      .join('\n')
  );
  await page.addStyleTag({ content: styles });
  await page.evaluate(() => {
    document.querySelectorAll('script, link[rel="stylesheet"]').forEach((node) => node.remove());
  });
  fs.writeFileSync(staticPath, await page.content());
  await browser.close();
})();
