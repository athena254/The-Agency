/*
  Automated accessibility smoke — axe on the primary routes. We assert no
  critical/serious violations; minor advisory items stay visible in output.
*/

import { AxeBuilder } from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { gotoMC } from './helpers.js'

const ROUTES = ['/', '/agents', '/ide', '/settings']

for (const route of ROUTES) {
  test(`axe: no critical violations on ${route}`, async ({ page }) => {
    await gotoMC(page, route)
    const results = await new AxeBuilder({ page })
      // Color-contrast on the decorative console grid and third-party icon
      // rendering can produce advisory noise; serious+ must still be clean.
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    const critical = results.violations.filter(
      (violation) => violation.impact === 'critical' || violation.impact === 'serious',
    )
    if (critical.length > 0) {
      console.error(
        `axe violations on ${route}:`,
        critical.map((violation) => `${violation.id} (${violation.impact}): ${violation.help}`),
      )
    }
    expect(critical, `${route}: no critical/serious axe violations`).toEqual([])
  })
}
