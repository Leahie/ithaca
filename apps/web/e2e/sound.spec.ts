import { expect, test } from '@playwright/test';
import { openHouse, openTab } from './helpers';

/** Sound tab: scenario toggles change the report, and the audio follows the report (A4, M5). */
test.describe('sound', () => {
  test.beforeEach(async ({ page }) => {
    await openHouse(page, 'sound', 'sun');
    await openTab(page, 'sound');
    await expect(page.getByTestId('laeq')).toBeVisible();
  });

  const level = async (page: import('@playwright/test').Page) => Number(await page.getByTestId('laeq').textContent());
  const gain = async (page: import('@playwright/test').Page) => Number(await page.getByTestId('sound').getAttribute('data-gain'));

  test('single-pane windows are louder than triple-pane, and the playback gain follows', async ({ page }) => {
    await page.getByRole('radio', { name: 'Single' }).click();
    await expect(page.getByTestId('laeq-delta')).toContainText('louder than as built');
    const single = { dba: await level(page), gain: await gain(page) };
    await page.getByRole('radio', { name: 'Triple' }).click();
    await expect(page.getByTestId('laeq-delta')).toContainText('quieter than as built');
    const triple = { dba: await level(page), gain: await gain(page) };
    expect(single.dba).toBeGreaterThan(triple.dba);
    // A4: gain is 10^(dB / 20) of the level difference, nothing else.
    expect(20 * Math.log10(single.gain / triple.gain)).toBeCloseTo(single.dba - triple.dba, 1);
  });

  test('picking a room on the room list changes the sound room', async ({ page }) => {
    await page.locator('nav[aria-label=Rooms] button', { hasText: 'Kitchen 1' }).click();
    await expect(page.getByLabel('Room', { exact: true })).toHaveValue(/.+/);
    await expect(page.getByTestId('sound').locator('text=Kitchen 1 at').first()).toBeVisible();
  });

  test('clicking an hour on the chart sets the shared time', async ({ page }) => {
    await page.getByRole('button', { name: /^3:00 am:/ }).click();
    await expect(page.getByTestId('sound')).toContainText('at 3 am');
    await openTab(page, 'sun');
    await expect(page.getByTestId('clock')).toHaveText('3:00 am');
  });

  test('play starts and stops the auralization', async ({ page }) => {
    await page.getByRole('button', { name: 'Play the sound of this room' }).click();
    await expect(page.getByTestId('sound')).toHaveAttribute('data-playing', 'true');
    await page.getByRole('button', { name: 'Stop the sound' }).click();
    await expect(page.getByTestId('sound')).toHaveAttribute('data-playing', 'false');
  });
});
