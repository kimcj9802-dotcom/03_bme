// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  timeout: 90_000,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    headless: true,
    viewport: { width: 1280, height: 800 },
    baseURL: 'http://127.0.0.1:8000',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
});
