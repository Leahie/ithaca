import { expect, test } from '@playwright/test';

// A 1x1 PNG, as a stand-in product image.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

/** /me: inventory, upload flow, memory (PLAN.md section 7). */
test.describe('/me', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/me');
    // Items come from the client: once one is on screen the page is hydrated and loaded.
    await expect(page.getByTestId('inventory-item').first()).toBeVisible();
  });

  test('uploading a product image adds an item with a 3D model (M2)', async ({ page }) => {
    const before = await page.getByTestId('inventory-item').count();
    await page.getByTestId('upload-input').setInputFiles({ name: 'sofa.png', mimeType: 'image/png', buffer: PNG });
    await expect(page.getByTestId('jobs').locator('li')).toHaveAttribute('data-status', 'done', { timeout: 30_000 });
    await expect(page.getByTestId('inventory-item')).toHaveCount(before + 1);
    await page.getByTestId('inventory-item').last().click();
    await expect(page.getByTestId('item-preview').locator('canvas')).toBeVisible();
  });

  test('an item shows its model, its dimensions and the segmentation mask', async ({ page }) => {
    await page.getByTestId('inventory-item').first().click();
    const detail = page.getByTestId('item-detail');
    await expect(detail.getByTestId('item-preview').locator('canvas')).toBeVisible();
    await expect(detail.getByText('Width')).toBeVisible();
    await expect(detail.getByTestId('mask-overlay')).toBeVisible();
    await detail.getByRole('switch', { name: 'Show what was segmented' }).click();
    await expect(detail.getByTestId('mask-overlay')).toHaveCount(0);
  });

  test('deleting an item asks first, then removes it', async ({ page }) => {
    const before = await page.getByTestId('inventory-item').count();
    await page.getByTestId('inventory-item').first().click();
    await page.getByRole('button', { name: 'Remove from inventory' }).click();
    await expect(page.getByTestId('inventory-item')).toHaveCount(before); // nothing happens until confirmed
    await page.getByRole('button', { name: 'Delete for good' }).click();
    await expect(page.getByTestId('inventory-item')).toHaveCount(before - 1);
  });

  test('memory files can be read, edited and deleted (privacy)', async ({ page }) => {
    await page.getByRole('radio', { name: 'Memory' }).click();
    await expect(page.getByTestId('memory-file').first()).toBeVisible();
    await page.locator('[data-testid=memory-file][data-path="preferences.md"]').click();
    await page.getByRole('radio', { name: 'Edit' }).click();
    const editor = page.getByTestId('memory-editor');
    await editor.fill((await editor.inputValue()) + '\n- Prefers a reading chair by a window.\n');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.reload();
    await page.getByRole('radio', { name: 'Memory' }).click();
    await page.locator('[data-testid=memory-file][data-path="preferences.md"]').click();
    await expect(page.getByTestId('memory')).toContainText('Prefers a reading chair by a window.');

    const count = await page.getByTestId('memory-file').count();
    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    await page.getByRole('button', { name: 'Delete this memory' }).click();
    await expect(page.getByTestId('memory-file')).toHaveCount(count - 1);
    await expect(page.locator('[data-testid=memory-file][data-path="preferences.md"]')).toHaveCount(0);
  });

  test('history lists each home with its notes and layout decisions', async ({ page }) => {
    await page.getByRole('radio', { name: 'History' }).click();
    const home = page.getByTestId('property-history').first();
    await expect(home).toContainText('Layout decisions');
    await expect(home.locator('li[data-status]').first()).toBeVisible();
  });
});
