import { test, expect, ctx, signIn, openAsset, api, bearer } from '../support'
import type { Page } from '@playwright/test'

// D. Legal claims and their evidence. A found quotation proves only that the text appears on the
// cited page; it is never presented as an independently verified legal right.
const approved = ctx.assets!.approved
const draft = ctx.assets!.draft

async function openExtraction(page: Page, code: string) {
  await signIn(page, ctx.credentials.issuer_a)
  await openAsset(page, code)
  await page.getByRole('link', { name: /Review →/ }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'AI Extraction Result' })).toBeVisible()
}

test.describe('D. Legal claims', () => {
  test('approved claims show their cited page, the found quotation and the approvals', async ({ page, request }) => {
    await openExtraction(page, approved.stellar_asset_code)
    const backend = await api(request, `/extractions/${approved.extraction!.id}`, bearer(ctx.credentials.issuer_a))
    expect(backend.status).toBe(200)
    for (const claim of backend.body.claims) {
      const row = page.getByTestId(`claim-row-${claim.field_name}`)
      await expect(row.getByTestId('evidence-badge-verified')).toHaveText(`Quote found on page ${claim.source_page}`)
      await expect(row).toContainText(`Page ${claim.source_page}`)
    }
    await expect(page.getByTestId('count-quote-found')).toHaveText('7')
    await expect(page.getByTestId('count-not-verified')).toHaveText('0')
    await expect(page.getByTestId('count-unknown')).toHaveText('0')
    await expect(page.getByTestId('extraction-version')).toHaveText('Document version 1')
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Approved')
    await expect(page.getByTestId('approval-state-badge').first()).toHaveText('Approval current')
    // The scripted seed is named for what it is, never as an AI model result
    await expect(page.getByText(ctx.seed_model!)).toBeVisible()
    await expect(page.getByTestId('disclaimer-banner').first()).toContainText('AI-GENERATED DRAFT')
    // Both internal attestations are listed, with the backend's own types
    const types = new Set(ctx.assets!.approved.approval_types)
    expect(types).toEqual(new Set(['issuer_attestation', 'legal_verification']))
    await expect(page.getByRole('heading', { name: 'Approvals (2)' })).toBeVisible()
    await expect(page.getByTestId('approval-type-badge').filter({ hasText: 'Issuer Attestation' })).toHaveCount(1)
    await expect(page.getByTestId('approval-type-badge').filter({ hasText: 'Independent Legal Review' })).toHaveCount(1)
  })

  test("a claim's cited page can be opened in the source text", async ({ page }) => {
    await openExtraction(page, approved.stellar_asset_code)
    await page.getByRole('button', { name: 'View page 4 of the source text for Jurisdiction' }).click()
    const page4 = page.getByTestId('source-page').nth(3)
    await expect(page4).toBeInViewport()
    await expect(page4).toContainText('governed by the laws of England and Wales')
  })

  test('a draft shows unverified and unknown claims as such and can never be approved', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 422/)
    await openExtraction(page, draft.stellar_asset_code)
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Draft')
    await expect(page.getByTestId('claim-row-jurisdiction').getByTestId('evidence-badge-failed'))
      .toHaveText('Not on cited page 2; found on page 4')
    await expect(page.getByTestId('claim-row-redemption_rights').getByTestId('evidence-badge-failed'))
      .toHaveText('Quote not found in document')
    await expect(page.getByTestId('claim-row-voting_rights').getByTestId('evidence-badge-unknown')).toHaveText('Unknown')
    await expect(page.getByTestId('count-quote-found')).toHaveText('4')
    await expect(page.getByTestId('count-not-verified')).toHaveText('2')
    await expect(page.getByTestId('count-unknown')).toHaveText('1')

    // The approval gate refuses, and names the unverified claims
    await page.getByRole('button', { name: 'Approve Extraction' }).click()
    const refusal = page.getByTestId('action-error')
    await expect(refusal).toContainText('Jurisdiction: quotation is not on the cited page')
    await expect(refusal).toContainText('Redemption Rights: quotation not found in this document version')
    const after = await api(request, `/extractions/${draft.extraction!.id}`, bearer(ctx.credentials.issuer_a))
    expect(after.body.status).toBe('draft')
    expect(after.body.approval_state.current_approval_types).toEqual([])
  })
})
