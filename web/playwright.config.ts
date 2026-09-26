import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'uv run mcp-sorter serve',
      cwd: '..',
      url: 'http://127.0.0.1:8000/health/live',
      reuseExistingServer: !process.env.CI,
      env: { SORTER_DATA_DIR: '.data/e2e', SORTER_TRUSTED_ORIGINS: '["http://127.0.0.1:5173"]' },
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
    },
  ],
});
