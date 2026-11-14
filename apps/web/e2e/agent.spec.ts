import { expect, test } from '@playwright/test';
import { agentReply, ask, openHouse, openTab, sunAzimuth } from './helpers';

/** The agent drives the site (M1, M3, M5). In fixture mode the agent is the scripted one. */
test.describe('agent dock', () => {
  test.beforeEach(async ({ page }) => openHouse(page));

  test('"take me to the kitchen" moves the viewer and shows a chip (M1, A8)', async ({ page }) => {
    await ask(page, 'Take me to the kitchen');
    await expect(page.getByTestId('agent-action').first()).toContainText(/kitchen/i);
    await expect(page.locator('nav[aria-label=Rooms] button[aria-current=true]')).toContainText(/kitchen/i);
    expect(await agentReply(page)).toMatch(/kitchen/i);
  });

  test('"show me December morning" opens the Sun Lab and sets the time (M3)', async ({ page }) => {
    await ask(page, 'Show me December morning');
    await expect(page.getByTestId('house')).toHaveAttribute('data-mode', 'sun');
    await expect(page.getByTestId('clock')).toHaveText('9:00 am');
    await expect(page.getByLabel('Date')).toHaveValue(/-12-21$/);
    await expect(page.locator('[data-testid=agent-action][data-tool=viewer_set_time]')).toBeVisible();
  });

  test('a sun question about a room reports the number the Sun Lab shows (A3)', async ({ page }) => {
    await ask(page, 'How sunny is the living room 3 in December?');
    const reply = await agentReply(page);
    const hours = /about ([\d.]+) hours/.exec(reply)?.[1];
    expect(hours).toBeTruthy();
    await expect(page.getByTestId('sun-hours')).toHaveText(Number(hours).toFixed(1));
  });

  test('a noise question gives the same number as the chart (M5)', async ({ page }) => {
    await ask(page, 'How loud is bedroom 4 with single-pane windows?');
    const reply = await agentReply(page);
    await expect(page.getByTestId('house')).toHaveAttribute('data-mode', 'sound');
    const peak = /peaks at ([\d.]+) dBA around (\d+):00/.exec(reply)!;
    expect(peak).toBeTruthy();
    // Go to the hour the agent named, by the same event a click on the chart sends.
    await page.getByRole('button', { name: new RegExp(`^${Number(peak[2]) % 12 || 12}:00 ${Number(peak[2]) < 12 ? 'am' : 'pm'}:`) }).click();
    await expect(page.getByTestId('laeq')).toHaveText(Number(peak[1]).toFixed(1));
    await expect(page.getByRole('radio', { name: 'Single' })).toHaveAttribute('aria-checked', 'true');
  });

  test('a click and the agent do the same thing (A1)', async ({ page }) => {
    await openTab(page, 'sun');
    await page.getByRole('button', { name: 'Dec 21' }).click();
    await page.getByLabel('Time of day').fill('540');
    const byClick = await sunAzimuth(page);
    await page.getByLabel('Time of day').fill('900');
    expect(await sunAzimuth(page)).not.toBeCloseTo(byClick, 1);
    await ask(page, 'Show me December morning');
    await agentReply(page);
    expect(await sunAzimuth(page)).toBeCloseTo(byClick, 3);
  });
});
