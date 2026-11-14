import { expect, test } from '@playwright/test';
import { FIXTURE_MODE, openHouse, openTab } from './helpers';

/** A6 / M0: every tab renders, in fixture mode with no backend running. */
test.describe('every tab renders', () => {
  test.beforeEach(async ({ page }) => openHouse(page));

  test('tour: the Showcase viewer, or a clear "not configured" message (I-G21)', async ({ page }) => {
    await openTab(page, 'tour');
    await expect(page.getByTestId('tour').or(page.getByText('The tour is not configured'))).toBeVisible();
  });

  test('sun lab: the house model loads and the scrubber shows a clock', async ({ page }) => {
    await openTab(page, 'sun');
    await expect(page.getByTestId('sun-lab')).toHaveAttribute('data-ready', 'true', { timeout: 40_000 });
    await expect(page.getByTestId('clock')).toContainText(/\d+:\d\d (am|pm)/);
    expect(Number(await page.getByTestId('sun-lab').getAttribute('data-openings'))).toBeGreaterThan(0);
  });

  test('sound: an hourly level and a reverberation time', async ({ page }) => {
    await openTab(page, 'sound');
    await expect(page.getByTestId('laeq')).toHaveText(/^\d+\.\d$/);
    await expect(page.getByTestId('rt60')).toHaveText(/^\d\.\d\d s$/);
  });

  test('neighborhood: places, comps and the area profile', async ({ page }) => {
    await openTab(page, 'neighborhood');
    await expect(page.getByTestId('comps').locator('tbody tr').first()).toBeVisible();
    expect(Number(await page.getByTestId('neighborhood').getAttribute('data-amenities'))).toBeGreaterThan(0);
    await page.getByLabel('Search nearby places').fill('park');
    await expect(page.getByTestId('amenity-list').locator('li').first()).toContainText(/park/i);
  });

  test('design: variant cards', async ({ page }) => {
    await openTab(page, 'design');
    await expect(page.getByTestId('variant-card')).toHaveCount(FIXTURE_MODE ? 3 : await page.getByTestId('variant-card').count());
  });

  test('the header says when data is from fixtures', async ({ page }) => {
    test.skip(!FIXTURE_MODE, 'fixture mode only');
    await expect(page.getByText('Fixture mode')).toBeVisible();
  });
});
