import { expect, test } from '@playwright/test';
import { agentReply, ask, openHouse, openTab } from './helpers';

/** Layout variants -> choose one -> the furniture is in the house (M2). */
test.describe('design', () => {
  test('"show layouts" gives three variants; choosing one places its items in the Sun Lab', async ({ page }) => {
    await openHouse(page);
    await ask(page, 'Show layouts');
    await agentReply(page);
    await expect(page.getByTestId('house')).toHaveAttribute('data-mode', 'design');
    await expect(page.getByTestId('variant-card')).toHaveCount(3);

    const card = page.getByTestId('variant-card').first();
    await card.getByRole('button', { name: 'Place in the house' }).click();
    await expect(card).toHaveAttribute('data-selected', 'true');

    await card.getByRole('button', { name: 'Sun Lab' }).click();
    await expect(page.getByTestId('sun-lab')).toHaveAttribute('data-ready', 'true', { timeout: 40_000 });
    await expect(page.getByTestId('sun-lab')).toHaveAttribute('data-placed', '3');
  });

  test('the chosen layout is in the Showcase scene', async ({ page }) => {
    await openHouse(page);
    const tour = page.getByTestId('tour');
    test.skip(!(await tour.isVisible().catch(() => false)), 'needs a Showcase SDK key');
    try {
      await expect(tour).toHaveAttribute('data-sdk-ready', 'true', { timeout: 45_000 });
    } catch {
      test.skip(true, 'the Matterport Showcase is not reachable from this machine');
    }
    await openTab(page, 'design');
    await page.getByTestId('variant-card').first().getByRole('button', { name: 'Place in the house' }).click();
    await openTab(page, 'tour');
    await expect.poll(() => page.evaluate(() => (window.__ITHACA__?.tourPlacedItemIds as string[] | undefined)?.length ?? 0), { timeout: 20_000 }).toBe(3);
  });

  test('the choice survives a reload (it is saved, not only shown)', async ({ page }) => {
    await openHouse(page);
    await openTab(page, 'design');
    const card = page.getByTestId('variant-card').nth(1);
    const id = await card.getAttribute('data-layout-id');
    await card.getByRole('button', { name: 'Place in the house' }).click();
    await expect(card).toHaveAttribute('data-selected', 'true');
    await expect(card).toHaveAttribute('data-status', 'chosen'); // saved, not only shown
    await page.reload();
    await openTab(page, 'design');
    await expect(page.locator(`[data-testid=variant-card][data-layout-id="${id}"]`)).toHaveAttribute('data-selected', 'true');
  });

  test('feedback on a layout is saved and sent to the agent', async ({ page }) => {
    await openHouse(page);
    await openTab(page, 'design');
    const card = page.getByTestId('variant-card').first();
    await card.getByLabel('Feedback on layout 1').fill('No armchair by the door');
    await card.getByRole('button', { name: 'Send' }).click();
    await expect(card).toContainText('Your note: “No armchair by the door”');
    await expect(page.locator('aside[aria-label=Agent]')).toContainText('No armchair by the door');
  });
});

declare global {
  interface Window {
    __ITHACA__?: Record<string, unknown>;
  }
}
