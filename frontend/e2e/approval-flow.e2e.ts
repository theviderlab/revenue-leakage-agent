import { expect, test, type Page } from '@playwright/test'

// The mock's scripted flow (docs/api.md, "Mock"; D29) with the Base ground-truth numbers of
// PLN-1001: -5,500 before, +1,500 after the February make-good, -5,500 again after the rollback.

async function send(page: Page, text: string) {
  const input = page.getByRole('textbox', { name: 'Message' })
  await expect(input).toBeEnabled()
  await input.fill(text)
  await input.press('Enter')
}

test('investigate, propose, approve, roll back', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Revenue Leakage Agent' })).toBeVisible()

  await send(page, 'Check plan PLN-1001 for revenue leakage')
  const lastFindings = page.getByTestId('findings-PLN-1001').last()
  await expect(lastFindings).toContainText('-5,500.00')

  await send(page, 'Create a make-good invoice for February 2026')
  await expect(page.getByTestId('proposal-card')).toContainText('PRP-001')

  await send(page, 'Yes, apply it')
  const approval = page.getByTestId('approval-card').last()
  await expect(approval).toContainText('Approval needed')
  // Nothing is written before the card is approved: the audit log is still empty.
  await page.getByRole('tab', { name: 'Audit log' }).click()
  await expect(page.getByText('INV-MG-001')).toHaveCount(0)

  await approval.getByRole('button', { name: 'Approve' }).click()
  await expect(approval.getByTestId('approval-outcome')).toContainText('INV-MG-001')
  await expect(page.getByTestId('findings-card')).toHaveCount(2)
  await expect(lastFindings).toContainText('+1,500.00')

  await send(page, 'Roll it back')
  const rollback = page.getByTestId('approval-card').last()
  await expect(rollback).toContainText('Rollback approval needed')
  await rollback.getByRole('button', { name: 'Approve' }).click()
  await expect(rollback.getByTestId('approval-outcome')).toContainText('Rolled back')
  await expect(page.getByTestId('findings-card')).toHaveCount(3)
  await expect(lastFindings).toContainText('-5,500.00')
})
