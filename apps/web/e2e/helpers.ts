import { expect, test, type BrowserContext, type Page } from '@playwright/test';

/** I-G19: the property id lives in config and tests, never in app code. */
export const PID = process.env.E2E_PROPERTY_ID ?? 'onXQLXhfyQv';
export const FIXTURE_MODE = !process.env.E2E_BASE_URL;

export type Feature = 'tour' | 'sun' | 'sound' | 'neighborhood' | 'design' | 'agent' | 'phone' | 'me';

/**
 * Open the house page. `needs` names the features a test depends on; the test is skipped when
 * the server under test has them switched off (NEXT_PUBLIC_FEATURES; the default is the tour alone).
 */
export async function openHouse(page: Page, ...needs: Feature[]): Promise<void> {
  await page.goto(`/house/${PID}`);
  const house = page.getByTestId('house');
  await expect(house).toBeVisible();
  const on = ((await house.getAttribute('data-features')) ?? '').split(' ');
  const missing = needs.filter((f) => !on.includes(f));
  test.skip(missing.length > 0, `needs NEXT_PUBLIC_FEATURES to include: ${missing.join(', ')}`);
}

/** The Showcase SDK of the Tour tab, for reading the camera in tests. */
export const tourCamera = (page: Page) =>
  page.evaluate(
    () =>
      new Promise<{ mode: string; sweep: string; yaw: number }>((resolve) => {
        type Pose = { mode: string; sweep: string; rotation: { y: number } };
        const sdk = window.__ITHACA__!.tourSdk as { Camera: { pose: { subscribe(cb: (p: Pose) => void): { cancel(): void } } } };
        const sub = sdk.Camera.pose.subscribe((p) => {
          resolve({ mode: p.mode, sweep: p.sweep, yaw: p.rotation.y });
          queueMicrotask(() => sub.cancel());
        });
      }),
  );

declare global {
  interface Window {
    __ITHACA__?: Record<string, unknown>;
  }
}

export async function openTab(page: Page, mode: 'tour' | 'sun' | 'sound' | 'neighborhood' | 'design'): Promise<void> {
  await page.locator(`[data-tab=${mode}]`).click();
  await expect(page.getByTestId('house')).toHaveAttribute('data-mode', mode);
}

export async function ask(page: Page, text: string): Promise<void> {
  await page.getByLabel('Message the agent').fill(text);
  await page.getByLabel('Message the agent').press('Enter');
}

/** Wait until the agent has finished streaming its latest reply, and return it. */
export async function agentReply(page: Page): Promise<string> {
  const last = page.getByTestId('agent-message').last();
  await expect(last).toHaveAttribute('data-streaming', 'false');
  return (await last.textContent()) ?? '';
}

/** Pair a phone: read the link behind the QR code on the desktop and open it in a second page. */
export async function pairPhone(desktop: Page, context: BrowserContext): Promise<Page> {
  const pairButton = desktop.getByRole('button', { name: 'Pair a phone' });
  await pairButton.click();
  const link = await desktop.getByTestId('pair-link').getAttribute('href');
  expect(link).toContain('/remote/');
  await pairButton.click(); // close the popover
  const phone = await context.newPage();
  await phone.setViewportSize({ width: 390, height: 844 });
  await phone.goto(link!);
  await expect(phone.getByTestId('remote')).toHaveAttribute('data-status', 'open');
  return phone;
}

export const sunAzimuth = async (page: Page) => Number(await page.getByTestId('sun-lab').getAttribute('data-sun-azimuth'));

/**
 * The Tour's Showcase viewer, once it is interactive. Skips the test when the Tour is not
 * configured (no SDK key); fails it when the viewer is there but never starts.
 */
export async function interactiveTour(page: Page) {
  const tour = page.getByTestId('tour');
  const notConfigured = page.getByText('The tour is not configured');
  await expect(tour.or(notConfigured)).toBeVisible();
  test.skip(await notConfigured.isVisible(), 'needs NEXT_PUBLIC_MATTERPORT_SDK_KEY');
  await expect(tour).toHaveAttribute('data-sdk-ready', 'true', { timeout: 60_000 });
  return tour;
}
