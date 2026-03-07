import { defineConfig, devices } from '@playwright/test'

const playwrightHost = process.env.PLAYWRIGHT_HOST ?? '127.0.0.1'
const playwrightPort = process.env.PLAYWRIGHT_PORT ?? '4173'
const playwrightBaseUrl = `http://${playwrightHost}:${playwrightPort}`

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: playwrightBaseUrl,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npm run dev -- --host ${playwrightHost} --port ${playwrightPort}`,
    env: {
      ...process.env,
      VITE_DEV_MODE: 'true',
    },
    url: playwrightBaseUrl,
    reuseExistingServer: false,
  },
})
