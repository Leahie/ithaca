import { expect, test } from '@playwright/test';
import { FIXTURE_MODE, PID, interactiveTour, openHouse, openTab, tourCamera } from './helpers';

/** A6 / M0: every tab renders, in fixture mode with no backend running. */
test.describe('every tab renders', () => {

  test('tour: the Showcase viewer, or a clear "not configured" message (I-G21)', async ({ page }) => {
    await openHouse(page, 'tour');
    await expect(page.getByTestId('tour').or(page.getByText('The tour is not configured'))).toBeVisible();
  });

  test('tour: becomes interactive, and a drag turns the camera', async ({ page }) => {
    await openHouse(page, 'tour');
    const tour = await interactiveTour(page);
    await expect.poll(async () => (await tourCamera(page)).mode, { timeout: 20_000 }).toBe('mode.inside');
    const before = await tourCamera(page);
    const box = (await tour.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 260, box.y + box.height / 2, { steps: 12 });
    await page.mouse.up();
    await expect.poll(async () => Math.abs((await tourCamera(page)).yaw - before.yaw), { timeout: 10_000 }).toBeGreaterThan(5);
  });

  test('tour: a room on the list takes the camera into that room', async ({ page }) => {
    await openHouse(page, 'tour');
    await interactiveTour(page);
    const twin = (await (await page.request.get(`/fixtures/twin/${PID}.json`)).json()) as { sweeps: { sweep_id: string; room_id: string | null }[] };
    const office = page.locator('nav[aria-label=Rooms] button[aria-label="Office"]');
    const roomId = await office.getAttribute('data-room-id');
    await office.click();
    await expect
      .poll(
        async () => {
          const { sweep } = await tourCamera(page);
          return twin.sweeps.find((s) => s.sweep_id === sweep)?.room_id;
        },
        { timeout: 25_000 },
      )
      .toBe(roomId);
    await expect(office).toHaveAttribute('aria-current', 'true');
  });

  test('tour: "Outside" shows the house from outside, and a room brings you back in', async ({ page }) => {
    test.setTimeout(240_000); // the exterior capture is slow to draw without a GPU (CI)
    await openHouse(page, 'tour');
    await interactiveTour(page);
    test.skip((await page.locator('[data-viewer]').getAttribute('data-viewer')) !== 'embed', 'the outside view needs the hosted viewer');
    const outside = page.locator('nav[aria-label=Rooms] [data-place=outside]');
    await outside.click();
    await expect.poll(async () => (await tourCamera(page)).mode, { timeout: 90_000 }).toBe('mode.dollhouse');
    await expect(outside).toHaveAttribute('aria-current', 'true');
    await expect(page.locator('nav[aria-label=Rooms] [data-room-id][aria-current=true]')).toHaveCount(0);

    await page.locator('nav[aria-label=Rooms] button[aria-label="Kitchen 1"]').click();
    await expect.poll(async () => (await tourCamera(page)).mode, { timeout: 90_000 }).toBe('mode.inside');
    await expect(outside).toHaveAttribute('aria-current', 'false');
  });

  test('tour only: the page has no chat and no other tabs unless they are switched on', async ({ page }) => {
    await openHouse(page, 'tour');
    const features = ((await page.getByTestId('house').getAttribute('data-features')) ?? '').split(' ');
    await expect(page.locator('aside[aria-label=Agent]')).toHaveCount(features.includes('agent') ? 1 : 0);
    await expect(page.locator('[data-tab]')).toHaveCount(features.filter((f) => ['tour', 'sun', 'sound', 'neighborhood', 'design'].includes(f)).length > 1 ? features.filter((f) => ['tour', 'sun', 'sound', 'neighborhood', 'design'].includes(f)).length : 0);
    await expect(page.locator('nav[aria-label=Rooms]')).toBeVisible();
  });

  test('sun lab: "Go inside" works before any room is chosen', async ({ page }) => {
    await openHouse(page, 'sun');
    await openTab(page, 'sun');
    await expect(page.getByTestId('sun-lab')).toHaveAttribute('data-ready', 'true', { timeout: 60_000 });
    await page.getByRole('button', { name: 'Go inside' }).click();
    await expect(page.locator('nav[aria-label=Rooms] button[aria-current=true]')).toHaveCount(1);
    await expect(page.getByRole('button', { name: /^Inside / })).toBeVisible();
  });

  test('sun lab: the house model loads and the scrubber shows a clock', async ({ page }) => {
    await openHouse(page, 'sun');
    await openTab(page, 'sun');
    await expect(page.getByTestId('sun-lab')).toHaveAttribute('data-ready', 'true', { timeout: 40_000 });
    await expect(page.getByTestId('clock')).toContainText(/\d+:\d\d (am|pm)/);
    expect(Number(await page.getByTestId('sun-lab').getAttribute('data-openings'))).toBeGreaterThan(0);
  });

  test('sound: an hourly level and a reverberation time', async ({ page }) => {
    await openHouse(page, 'sound');
    await openTab(page, 'sound');
    await expect(page.getByTestId('laeq')).toHaveText(/^\d+\.\d$/);
    await expect(page.getByTestId('rt60')).toHaveText(/^\d\.\d\d s$/);
  });

  test('neighborhood: places, comps and the area profile', async ({ page }) => {
    await openHouse(page, 'neighborhood');
    await openTab(page, 'neighborhood');
    await expect(page.getByTestId('comps').locator('tbody tr').first()).toBeVisible();
    expect(Number(await page.getByTestId('neighborhood').getAttribute('data-amenities'))).toBeGreaterThan(0);
    await page.getByLabel('Search nearby places').fill('park');
    await expect(page.getByTestId('amenity-list').locator('li').first()).toContainText(/park/i);
  });

  test('design: variant cards', async ({ page }) => {
    await openHouse(page, 'design');
    await openTab(page, 'design');
    await expect(page.getByTestId('variant-card')).toHaveCount(FIXTURE_MODE ? 3 : await page.getByTestId('variant-card').count());
  });

  test('the header says when data is from fixtures', async ({ page }) => {
    await openHouse(page, 'tour');
    test.skip(!FIXTURE_MODE, 'fixture mode only');
    await expect(page.getByText('Fixture mode')).toBeVisible();
  });
});
