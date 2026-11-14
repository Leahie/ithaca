import { defineConfig, devices } from '@playwright/test';

/**
 * E2E harness (Stream A). Two modes (A6):
 *   fixture mode (default): starts the web app with NEXT_PUBLIC_USE_FIXTURES=1, no backend.
 *   full-stack mode: E2E_BASE_URL=http://localhost:3000 pnpm e2e  (web + api + worker already up)
 *
 * PLAYWRIGHT_CHROMIUM_PATH points at a system Chromium when the bundled browser is not installed.
 */
const PORT = Number(process.env.E2E_PORT ?? 3210);
const baseURL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;
const fullStack = !!process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  metadata: { mode: fullStack ? 'full-stack' : 'fixture' },
  use: {
    baseURL,
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
      // Software WebGL, so the 3D views render on machines without a GPU (CI).
      args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
    },
  },
  projects: [{ name: fullStack ? 'full-stack' : 'fixture', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: fullStack
    ? undefined
    : {
        command: `pnpm exec next dev -p ${PORT}`,
        url: `${baseURL}/fixtures/twin/onXQLXhfyQv.json`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        env: {
          NEXT_PUBLIC_USE_FIXTURES: '1',
          NEXT_PUBLIC_DEFAULT_PROPERTY_ID: 'onXQLXhfyQv',
          // The Tour needs the network and a key; without one it shows "not configured" (I-G21).
          NEXT_PUBLIC_MATTERPORT_SDK_KEY: process.env.NEXT_PUBLIC_MATTERPORT_SDK_KEY ?? '',
        },
      },
});
