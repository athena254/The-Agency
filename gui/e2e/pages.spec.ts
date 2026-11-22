/*
  Page-level honesty: unlocked read-only surfaces vs. deliberately locked
  platform surfaces, and the agents directory.
*/

import { expect, test } from '@playwright/test'
import { gotoMC } from './helpers.js'

test('read-only registry surfaces declare their gates', async ({ page }) => {
  await gotoMC(page, '/skills')
  await expect(page.locator('#main-content').getByRole('heading', { name: 'Skills' })).toBeVisible()
  await expect(page.getByText(/Read-only registry view/)).toBeVisible()

  await gotoMC(page, '/evidence')
  await expect(page.getByText(/Risk scoring is the legacy heuristic engine/)).toBeVisible()

  await gotoMC(page, '/memories')
  await expect(page.getByText(/Read-only listing of what is stored/)).toBeVisible()
})

test('Lattice shows real ballot governance; Forge stays honestly locked', async ({ page }) => {
  await gotoMC(page, '/lattice')
  // Slice D replaced the locked panel with grant-gated ballot governance; the
  // honesty contract is now "real ballots only, never signs, no key material".
  await expect(
    page.getByText('Governance: open proposals, affected parties, evidence, and real human ballots.'),
  ).toBeVisible()
  await expect(page.getByText('Real ballots only.')).toBeVisible()
  await expect(page.getByText(/explicit human attestation \(decision D-06\)/)).toBeVisible()

  await gotoMC(page, '/forge')
  await expect(page.getByText('Native coding actions wait for P2/P3')).toBeVisible()
})

test('agents directory renders the roster table', async ({ page }) => {
  await gotoMC(page, '/agents')
  await expect(page.getByRole('heading', { name: 'Registered agents' })).toBeVisible()
  await expect(page.getByText(/owner-scoped|Loading agents|No agents registered/).first()).toBeVisible()
})

test('settings shows the runtime section with real health rows', async ({ page }) => {
  await gotoMC(page, '/settings')
  await expect(page.getByRole('heading', { name: 'Runtime' })).toBeVisible()
  await expect(page.getByText('Owner pilot bearer', { exact: true })).toBeVisible()
  // Honest version display: either a real version or 'not reported' — never
  // a hardcoded fake.
  await expect(page.getByText(/^(not reported|[0-9]+\.[0-9]+\.[0-9]+.*)$/)).toBeVisible()
})
