/*
  Shell journeys: console hygiene, navigation, 404, and the honesty contract
  on the dashboard (no fabricated activity, real status shapes only).
*/

import { expect, test } from '@playwright/test'
import { gotoMC } from './helpers.js'

test('console stays clean on load and navigation', async ({ page }) => {
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))

  await gotoMC(page)
  // Walk a representative subset of routes.
  for (const path of ['/observability', '/ide', '/agents', '/skills', '/settings']) {
    await gotoMC(page, path)
  }

  expect(pageErrors, 'no uncaught page errors').toEqual([])
  expect(consoleErrors, 'no console errors').toEqual([])
})

test('skip link is the first tab stop', async ({ page }) => {
  await gotoMC(page)
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
})

test('unknown routes render the real 404 page', async ({ page }) => {
  await gotoMC(page, '/definitely-not-a-page')
  await expect(page.getByText('404')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Back to Dashboard' })).toBeVisible()
  await page.getByRole('link', { name: 'Back to Dashboard' }).click()
  await expect(page).toHaveURL(/mission-control$/)
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
})

test('dashboard shows honest live data, never fabricated activity', async ({ page }) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await gotoMC(page)
  await expect(page.getByRole('region', { name: 'Runtime status' })).toBeVisible()

  // The fabricated placeholders that were removed must stay gone.
  await expect(page.getByText('Inception mercury-3')).toHaveCount(0)
  await expect(page.getByText('static placeholder')).toHaveCount(0)

  // Audit events panel exists with an honest empty/unavailable/loading state.
  await expect(page.getByRole('heading', { name: 'Recent audit events' })).toBeVisible()

  // Sidebar runtime footer reports real shapes (checking… / unreachable /
  // healthy), never the old hardcoded version string.
  await expect(page.locator('aside').first()).toContainText('Local prototype')
  expect(consoleErrors).toEqual([])
})
