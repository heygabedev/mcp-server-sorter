import { defineConfig, devices } from '@playwright/test';

const apiPort = Number(process.env.SORTER_E2E_API_PORT ?? 18080);
const webPort = Number(process.env.SORTER_E2E_WEB_PORT ?? 15173);
for (const port of [apiPort, webPort]) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid test port');
}
const apiUrl = `http://127.0.0.1:${apiPort}`;
const webUrl = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: webUrl, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `uv run mcp-sorter serve --port ${apiPort}`,
      cwd: '..',
      url: `${apiUrl}/health/live`,
      reuseExistingServer: false,
      env: {
        SORTER_DATA_DIR: `.data/e2e-${apiPort}`,
        SORTER_TRUSTED_ORIGINS: JSON.stringify([webUrl]),
      },
    },
    {
      command: `npm run dev -- --port ${webPort} --strictPort`,
      url: webUrl,
      reuseExistingServer: false,
      env: { SORTER_API_URL: apiUrl },
    },
  ],
});
