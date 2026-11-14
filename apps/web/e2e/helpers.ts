import { expect, type BrowserContext, type Page } from '@playwright/test';

/** I-G19: the property id lives in config and tests, never in app code. */
export const PID = process.env.E2E_PROPERTY_ID ?? 'onXQLXhfyQv';
export const FIXTURE_MODE = !process.env.E2E_BASE_URL;

export async function openHouse(page: Page): Promise<void> {
  await page.goto(`/house/${PID}`);
  await expect(page.getByTestId('house')).toBeVisible();
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
