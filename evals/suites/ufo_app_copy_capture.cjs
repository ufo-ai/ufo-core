const { chromium } = (() => {
  try {
    return require('playwright');
  } catch {
    return require('/usr/local/lib/node_modules/playwright');
  }
})();
const fs = require('fs');

(async () => {
  const [url, staticPath] = process.argv.slice(2);
  if (!url || !staticPath) {
    console.error('usage: node app-copy-capture.cjs <url> <static.html>');
    process.exit(2);
  }
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForTimeout(700);
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
