/*
  Shared E2E helpers. The prototype app serves the console at
  /mission-control (the legacy chat UI owns `/`), so every journey
  navigates through this helper.
*/

import { expect, type Page } from '@playwright/test'

export const MC = '/mission-control'

export async function gotoMC(page: Page, route = '/'): Promise<void> {
  await page.goto(`${MC}${route === '/' ? '' : route}`)
  // The shell is rendered when the sidebar brand appears.
  await expect(page.locator('aside').first()).toContainText('Mission Control')
}
