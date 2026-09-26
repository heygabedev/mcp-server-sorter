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
  await page.getByLabel('Select GitHub').check();
  await page.getByLabel('Select Slack').check();
  await page.getByRole('button', { name: 'Compare & save' }).click();
  await expect(
    page.getByRole('region', { name: 'Server comparison' }).locator('tbody tr'),
  ).toHaveCount(2);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.keyboard.press('Escape');
  await page.getByLabel('Select GitHub').uncheck();
  await page.getByLabel('Select Slack').uncheck();
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
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: 'test-results/showcase-evaluation.png' });
  await page.getByLabel('Baseline evaluation').selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Compare baseline', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('regression gate: passed');
  await page.getByLabel('Evaluation profile').selectOption('demo-malformed');
  await page.getByRole('button', { name: 'Run evaluation', exact: true }).click();
  await expect(page.getByText('LATEST RESULT · demo-malformed')).toBeVisible();
  await expect(
    page.getByRole('cell', { name: 'invalid-schema', exact: true }).first(),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Operations', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Durable jobs' })).toBeVisible();
  await page.getByRole('button', { name: 'Refresh catalog', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Refresh catalog', exact: true })).toBeEnabled();
  await expect(page.getByText('catalog-refresh', { exact: true }).first()).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole('button', { name: 'Versions', exact: true }).click();
  await page.getByRole('button', { name: 'Add title-focused configuration' }).click();
  await expect(page.getByRole('status')).toContainText('Alternative ranking configuration saved');
  const titleConfiguration = await page
    .getByRole('option', { name: /Titles first/ })
    .getAttribute('value');
  await page.getByLabel('Ranking configuration').selectOption(titleConfiguration!);
  await page.getByRole('button', { name: 'Activate selected versions' }).click();
  await expect(page.getByRole('status')).toContainText('activated together');
  await page.getByRole('button', { name: 'Create verified backup' }).click();
  await expect(page.getByRole('status')).toContainText('Backup created and verified');
  await page.screenshot({ path: 'test-results/showcase-recovery.png' });
  const manifestDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Verify and export manifest' }).click();
  expect((await manifestDownload).suggestedFilename()).toContain('-manifest.json');
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
  await expect(page.getByRole('button', { name: 'GitHub', exact: true })).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.getByLabel('Search servers')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: 'test-results/showcase-mobile.png' });
});
