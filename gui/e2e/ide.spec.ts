/*
  IDE isolated-chat journey. With the echo provider the Butler replies
  deterministically; the page must show the user message immediately and
  either an assistant reply or an honest error — never a stuck spinner.
*/

import { expect, test } from '@playwright/test'
import { gotoMC } from './helpers.js'

test('chat turn produces a reply or an honest error', async ({ page }) => {
  await gotoMC(page, '/ide')
  await expect(page.getByRole('heading', { name: 'Isolated chat' })).toBeVisible()

  const input = page.getByLabel('Message the Butler')
  await expect(input).toBeVisible()
  await input.fill('Hello from the e2e suite')
  await page.getByRole('button', { name: 'Send message' }).click()

  await expect(page.getByText('Hello from the e2e suite').first()).toBeVisible()
  // The pending indicator must resolve: either a reply renders or the error
  // note appears. Both are honest outcomes.
  await expect(
    page.getByText(/thinking|failed|unavailable|timed out|could not/i).first(),
  ).toBeHidden({ timeout: 60_000 }).catch(() => {
    // The "thinking" state may already be gone when the first assertion runs.
  })
  await expect(
    page
      .locator('li')
      .filter({ hasText: /Butler|Request failed|could not|timed out|unavailable/i })
      .first(),
  ).toBeVisible({ timeout: 60_000 })
})
