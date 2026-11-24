/*
  Page-level honesty: unlocked read-only surfaces vs. deliberately locked
  platform surfaces, and the agents directory.
*/

import { expect, test, type Page } from '@playwright/test'
import { gotoMC } from './helpers.js'

const OWNER_TOKEN = process.env.AGENCY_OWNER_TOKEN ?? 'e2e-owner-token'

async function gotoForgeWithOwnerToken(page: Page): Promise<void> {
  await gotoMC(page, '/settings')
  await page.getByLabel('Owner pilot bearer token').fill(OWNER_TOKEN)
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Token set for this session')).toBeVisible()
  const openNav = page.getByRole('button', { name: 'Open navigation' })
  if (await openNav.isVisible()) await openNav.click()
  await page.getByRole('link', { name: 'Forge' }).click()
  await expect(page).toHaveURL(/\/forge$/)
}

test('read-only registry surfaces declare their gates', async ({ page }) => {
  await gotoMC(page, '/skills')
  await expect(page.locator('#main-content').getByRole('heading', { name: 'Skills' })).toBeVisible()
  await expect(page.getByText(/Read-only registry view/)).toBeVisible()

  await gotoMC(page, '/evidence')
  await expect(page.getByText(/Risk scoring is the legacy heuristic engine/)).toBeVisible()

  await gotoMC(page, '/memories')
  await expect(page.getByText(/Read-only listing of what is stored/)).toBeVisible()
})

test('Lattice shows real ballot governance; Forge is an owner workspace', async ({ page }) => {
  await gotoMC(page, '/lattice')
  // Slice D replaced the locked panel with grant-gated ballot governance; the
  // honesty contract is now "real ballots only, never signs, no key material".
  await expect(
    page.getByText('Governance: open proposals, affected parties, evidence, and real human ballots.'),
  ).toBeVisible()
  await expect(page.getByText('Real ballots only.')).toBeVisible()
  await expect(page.getByText(/explicit human attestation \(decision D-06\)/)).toBeVisible()

  await gotoForgeWithOwnerToken(page)
  await expect(
    page.getByText('dispatch runs an external agent; no ship/merge/release from this UI').first(),
  ).toBeVisible()
  await expect(page.getByRole('region', { name: 'Forge status summary' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Forge metrics' })).toBeVisible()
  const caveats = page.getByRole('region', { name: 'Caveats' })
  await expect(caveats).toBeVisible()
  await expect(caveats.locator('li, p').first()).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recent Forge jobs' })).toBeVisible()

  const dispatch = page.getByRole('region', { name: 'Forge dispatch' })
  await expect(dispatch).toBeVisible()
  const dispatchButton = dispatch.getByRole('button', { name: 'Dispatch next queued job' })
  await expect(dispatchButton).toBeVisible()
  await expect(page.getByRole('region', { name: 'Last dispatch' })).toBeVisible()
  if (await dispatchButton.isDisabled()) {
    await expect(
      dispatch
        .getByText(/unavailable in this runtime|owner token required|already running/i)
        .first(),
    ).toBeVisible()
  }

  const submitButton = page.getByRole('button', { name: 'Queue Forge job' })
  await expect(page.getByRole('region', { name: 'Submit Forge job' })).toBeVisible()
  await expect(page.getByLabel('Request ID')).toHaveValue(/^[0-9a-f-]{36}$/)
  await expect(submitButton).toBeDisabled()
  await page.getByLabel('Title', { exact: true }).fill('E2E bounded job')
  await page.getByLabel('Task', { exact: true }).fill('Inspect the bounded fixture.')
  await expect(submitButton).toBeEnabled()
  await page.getByLabel(/^Acceptance \(comma-separated/).fill('one, two')
  await page.getByLabel(/^Test paths \(comma-separated/).fill('tests/test_fixture.py')
  await expect(page.getByText('Native coding actions wait for P2/P3')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /ship|merge|release|cancel/i })).toHaveCount(0)
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
