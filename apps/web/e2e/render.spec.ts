import { expect, test } from '@playwright/test';
import { PID } from './helpers';

/** Render page contract (WORKSTREAMS.md section 2.6) and determinism (A9). */
test.describe('/render/layout/{id}', () => {
  test.use({ viewport: { width: 800, height: 600 } });

  async function firstLayoutId(request: import('@playwright/test').APIRequestContext): Promise<string> {
    const fixture = await request.get(`/fixtures/layouts/${PID}.json`);
    test.skip(!fixture.ok(), 'needs layout fixtures');
    return ((await fixture.json()) as { layout_id: string }[])[0]!.layout_id;
  }

  test('sets window.__RENDER_READY__ within 20 s and draws the room', async ({ page, request }) => {
    await page.goto(`/render/layout/${await firstLayoutId(request)}`);
    await page.waitForFunction(() => window.__RENDER_READY__ === true, null, { timeout: 20_000 });
    await expect(page.getByTestId('render').locator('canvas')).toBeVisible();
    await expect(page.locator('header, nav, aside')).toHaveCount(0); // no app chrome in a thumbnail
  });

  test('the same layout gives the same PNG (A9)', async ({ page, request }) => {
    const id = await firstLayoutId(request);
    const capture = async () => {
      await page.goto(`/render/layout/${id}`);
      await page.waitForFunction(() => window.__RENDER_READY__ === true, null, { timeout: 20_000 });
      return page.screenshot();
    };
    const a = await capture();
    const b = await capture();
    expect(a.equals(b)).toBe(true);
  });

  test('an unknown layout reports an error instead of hanging', async ({ page }) => {
    await page.goto('/render/layout/00000000-0000-4000-8000-000000000000');
    await expect(page.getByTestId('render-error')).toBeVisible();
    expect(await page.evaluate(() => window.__RENDER_ERROR__)).toBeTruthy();
    expect(await page.evaluate(() => window.__RENDER_READY__)).not.toBe(true);
  });
});

declare global {
  interface Window {
    __RENDER_READY__?: boolean;
    __RENDER_ERROR__?: string;
  }
}
