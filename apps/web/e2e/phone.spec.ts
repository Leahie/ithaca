import { expect, test } from '@playwright/test';
import { agentReply, openHouse, openTab, pairPhone, sunAzimuth } from './helpers';

/** Phone controller (M0, M4, A2). In fixture mode both pages share a local session. */
test.describe('phone controller', () => {
  test('the phone changes the desktop tab (M0)', async ({ page, context }) => {
    await openHouse(page);
    const phone = await pairPhone(page, context);
    await phone.locator('[data-tab=sound]').click();
    await expect(page.getByTestId('house')).toHaveAttribute('data-mode', 'sound');
    await expect(page.getByRole('button', { name: 'Pair a phone' })).toContainText(/connected/i);
  });

  test('scrubbing time on the phone turns the sun in the Sun Lab', async ({ page, context }) => {
    await openHouse(page);
    await openTab(page, 'sun');
    const phone = await pairPhone(page, context);
    await expect(phone.getByTestId('remote')).toHaveAttribute('data-mode', 'sun'); // rebuilt from session.state
    await phone.getByLabel('Time of day').fill('480');
    await expect(page.getByTestId('clock')).toHaveText('8:00 am');
    const morning = await sunAzimuth(page);
    await phone.getByLabel('Time of day').fill('1020');
    await expect(page.getByTestId('clock')).toHaveText('5:00 pm');
    const evening = await sunAzimuth(page);
    expect(evening).toBeGreaterThan(morning + 60); // the sun moved from the east side to the west side
  });

  test('typing to the agent on the phone moves the desktop, and the phone sees the reply', async ({ page, context }) => {
    await openHouse(page);
    const phone = await pairPhone(page, context);
    await phone.getByLabel('Message the agent').fill('take me to the office');
    await phone.getByLabel('Message the agent').press('Enter');
    await expect(page.locator('nav[aria-label=Rooms] button[aria-current=true]')).toHaveAttribute('aria-label', 'Office');
    expect(await agentReply(page)).toMatch(/office/i);
    await expect(phone.getByTestId('remote-feed')).toContainText(/office/i);
  });

  test('a room tapped on the phone is the room on the desktop', async ({ page, context }) => {
    await openHouse(page);
    const phone = await pairPhone(page, context);
    const room = phone.getByTestId('remote-rooms').locator('button').first();
    const label = await room.textContent();
    await room.click();
    await expect(page.locator('nav[aria-label=Rooms] button[aria-current=true]')).toHaveAttribute('aria-label', label!);
  });

  test('swiping to a layout on the phone places it on the desktop', async ({ page, context }) => {
    await openHouse(page);
    await openTab(page, 'design');
    const phone = await pairPhone(page, context);
    const second = phone.getByTestId('variant-swiper').locator('article').nth(1);
    const id = await second.getAttribute('data-layout-id');
    await second.getByRole('button', { name: 'Place in the house' }).click();
    await expect(page.locator(`[data-testid=variant-card][data-layout-id="${id}"]`)).toHaveAttribute('data-selected', 'true');
  });

  test('killing the phone does not break the desktop (A2)', async ({ page, context }) => {
    await openHouse(page);
    const phone = await pairPhone(page, context);
    await phone.locator('[data-tab=sun]').click();
    await expect(page.getByTestId('house')).toHaveAttribute('data-mode', 'sun');
    await phone.close();
    await openTab(page, 'design');
    await openTab(page, 'sun');
    await page.getByLabel('Time of day').fill('720');
    await expect(page.getByTestId('clock')).toHaveText('12:00 pm');
  });

  test('a link without a pairing code explains what to do', async ({ page }) => {
    await page.goto('/remote/some-session');
    await expect(page.getByText('This link has no pairing code')).toBeVisible();
  });
});
