// Optional real-browser regression: PLAYWRIGHT_MODULE may point to an existing installation.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assets = new URL('../kitt/remote/static/', import.meta.url);

test('agent workspace: resizing, streaming, accessible tabs and mobile panels', async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.EventSource = class extends EventTarget {
        constructor() { super(); window.testSource = this; }
        close() {}
      };
    });
    await page.route('http://kitt.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (['/', '/app.js', '/app.css'].includes(path)) {
        await route.fulfill({body: await readFile(new URL(path === '/' ? 'index.html' : path.slice(1), assets)),
          contentType: path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html',
          headers: {'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'"}});
        return;
      }
      const payload = path === '/api/me' ? {csrf: 'fixture'}
        : path === '/api/status' ? {workspace_root: '/workspace/kitt', runtime: {status: 'ready'}}
        : path === '/api/sessions' ? {sessions: [{id: 'test', title: 'Revisão do reverse proxy', status: 'ready'}]}
        : path === '/api/sessions/test' ? {conversation: {title: 'Revisão do reverse proxy'}, messages: Array.from({length: 20}, (_, i) => ({role: i % 2 ? 'assistant' : 'user', content: `Mensagem ${i}: revisar o protocolo de ferramentas.\nPreservar permissões e identidade da chamada.`, created_at: 1}))}
        : {};
      await route.fulfill({json: payload});
    });
    await page.goto('http://kitt.test/');
    await page.waitForFunction(() => window.testSource);
    const handle = page.locator('[data-resize="sidebar"]');
    await handle.focus(); await page.keyboard.press('ArrowRight');
    assert.equal(await handle.getAttribute('aria-valuenow'), '260');
    const box = await handle.boundingBox();
    await page.mouse.move(box.x + 2, box.y + 50); await page.mouse.down();
    await page.mouse.move(box.x + 42, box.y + 50); await page.mouse.up();
    assert.equal(await handle.getAttribute('aria-valuenow'), '300');
    const initialWidth = (await page.locator('.conversation-column').boundingBox()).width;
    await page.locator('#navToggle').click();
    assert.equal(await page.locator('#sidebar').isVisible(), false);
    assert.ok((await page.locator('.conversation-column').boundingBox()).width > initialWidth + 290);
    await page.locator('#navToggle').click();
    await page.locator('#inspectorToggle').click();
    assert.equal(await page.locator('#inspector').isVisible(), false);
    assert.ok((await page.locator('.conversation-column').boundingBox()).width > initialWidth + 330);
    await page.locator('#inspectorToggle').click();
    await page.reload(); await page.waitForFunction(() => window.testSource);
    assert.equal(await handle.getAttribute('aria-valuenow'), '300');
    await page.locator('#tab-events').focus(); await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#tab-output').getAttribute('aria-selected'), 'true');
    await page.locator('#conversation').evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
    await page.evaluate(() => {
      for (let i = 1; i <= 50; i++) window.testSource.dispatchEvent(new MessageEvent('kitt', {data: JSON.stringify({sequence_id: i, event_type: 'TextDelta', payload: {delta: 'x'}})}));
    });
    await page.waitForFunction(() => document.querySelector('.message:last-child .message-body')?.textContent === 'x'.repeat(50));
    assert.equal(await page.locator('#conversation').evaluate(el => el.scrollTop), 0);
    await page.locator('#latestButton').click();
    await page.waitForFunction(() => document.getElementById('conversation').scrollTop > 0);
    await page.evaluate(() => {
      const events = [
        ['TurnCompleted', {}], ['TurnStarted', {turn_id: 'turn2'}],
        ['TextDelta', {delta: 'Exemplo seguro:\n```html\n<img src=x onerror="window.injected=true">\n```'}],
        ['TurnCompleted', {}], ['ToolStarted', {tool_name: 'read_file', args: {path: 'README.md'}}]
      ];
      events.forEach(([event_type, payload], i) => window.testSource.dispatchEvent(new MessageEvent('kitt', {data: JSON.stringify({sequence_id: 51 + i, event_type, payload})})));
    });
    await page.locator('.code-block').waitFor();
    assert.equal(await page.locator('.code-block img').count(), 0);
    assert.match(await page.locator('.code-block code').textContent(), /<img/);
    assert.equal(await page.locator('details.event-card').getAttribute('open'), null);
    await page.screenshot({path: '/tmp/kitt-agent-web-desktop.png'});
    for (const width of [1024, 768, 390, 320]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
    }
    await page.locator('#navToggle').click();
    assert.equal(await page.locator('#sidebar').isVisible(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#sidebar').isVisible(), false);
    await page.locator('#inspectorToggle').click();
    assert.equal(await page.locator('#inspector').isVisible(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#inspector').isVisible(), false);
    await page.screenshot({path: '/tmp/kitt-agent-web-mobile.png'});
    await page.setViewportSize({width: 1440, height: 900});
    await page.waitForFunction(() => document.querySelector('[data-resize="sidebar"]').getAttribute('aria-valuenow') === '300');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
