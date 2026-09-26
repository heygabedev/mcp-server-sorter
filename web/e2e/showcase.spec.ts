import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('search, compare, save, export and evaluate without external requests', async ({ page }) => {
  const external: string[] = [];
  await page.route('**/*', (route) => {
    const host = new URL(route.request().url()).hostname;
    if (!['127.0.0.1', 'localhost'].includes(host)) {
      external.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'GitHub', exact: true })).toBeVisible();
  await page.getByLabel('Search servers').fill('github');
  await page.getByRole('button', { name: 'Find servers' }).click();
  await expect(page.locator('.server-card')).toHaveCount(1);
  await page.getByLabel('Select GitHub').check();
  await page.getByRole('button', { name: 'Save selection' }).click();
  await page.getByLabel('Collection name').fill('Browser test toolkit');
  await page.getByRole('button', { name: 'Save collection', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('pinned server');
  await page.getByRole('button', { name: 'Collections', exact: true }).click();
  await expect(page.getByText('Browser test toolkit').first()).toBeVisible();
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export selection' }).first().click();
  expect((await downloaded).suggestedFilename()).toContain('selection-');
  await page.getByRole('button', { name: 'Evaluations', exact: true }).click();
  await page.getByRole('button', { name: 'Run evaluation', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Golden dataset report' })).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(90);
  await page.getByLabel('Baseline evaluation').selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Compare baseline', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('regression gate: passed');
  await page.getByRole('button', { name: 'Operations', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Durable jobs' })).toBeVisible();
  await page.getByRole('button', { name: 'Refresh catalog', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Refresh catalog', exact: true })).toBeEnabled();
  await expect(page.getByText('catalog-refresh', { exact: true }).first()).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  expect(external).toEqual([]);
});

test('accessible catalog, keyboard details and mobile layout', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'GitHub', exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: 'test-results/showcase-desktop.png' });
  await page.getByRole('button', { name: 'GitHub', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.getByLabel('Search servers')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: 'test-results/showcase-mobile.png' });
});
