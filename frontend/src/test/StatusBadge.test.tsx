import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  ExtractionStatusBadge,
  ApprovalTypeBadge,
  ClaimStatusBadge,
} from '../components/StatusBadge'

describe('ExtractionStatusBadge', () => {
  it('renders Draft label for draft status', () => {
    render(<ExtractionStatusBadge status="draft" />)
    const el = screen.getByTestId('extraction-status-badge')
    expect(el).toHaveTextContent('Draft')
    expect(el.className).toContain('slate')
  })

  it('renders Under Review label for under_review status with amber color', () => {
    render(<ExtractionStatusBadge status="under_review" />)
    const el = screen.getByTestId('extraction-status-badge')
    expect(el).toHaveTextContent('Under Review')
    expect(el.className).toContain('amber')
  })

  it('renders Approved label for approved status with green color', () => {
    render(<ExtractionStatusBadge status="approved" />)
    const el = screen.getByTestId('extraction-status-badge')
    expect(el).toHaveTextContent('Approved')
    expect(el.className).toContain('green')
  })
})

describe('ApprovalTypeBadge', () => {
  it('renders Issuer Attestation label with blue color', () => {
    render(<ApprovalTypeBadge type="issuer_attestation" />)
    const el = screen.getByTestId('approval-type-badge')
    expect(el).toHaveTextContent('Issuer Attestation')
    expect(el.className).toContain('blue')
  })

  it('renders Legal Verification label with indigo color', () => {
    render(<ApprovalTypeBadge type="legal_verification" />)
    const el = screen.getByTestId('approval-type-badge')
    expect(el).toHaveTextContent('Legal Verification')
    expect(el.className).toContain('indigo')
  })
})

describe('ClaimStatusBadge', () => {
  it('renders Unknown badge when isUnknown=true', () => {
    render(<ClaimStatusBadge isUnknown={true} verified={false} />)
    const el = screen.getByTestId('claim-status-badge-unknown')
    expect(el).toHaveTextContent('Unknown')
    expect(el.className).toContain('orange')
  })

  it('renders Verified badge when isUnknown=false and verified=true', () => {
    render(<ClaimStatusBadge isUnknown={false} verified={true} />)
    const el = screen.getByTestId('claim-status-badge-verified')
    expect(el).toHaveTextContent('Verified')
    expect(el.className).toContain('emerald')
  })

  it('renders Unverified badge when isUnknown=false and verified=false', () => {
    render(<ClaimStatusBadge isUnknown={false} verified={false} />)
    const el = screen.getByTestId('claim-status-badge-unverified')
    expect(el).toHaveTextContent('Unverified')
    expect(el.className).toContain('amber')
  })

  it('renders Unknown when isUnknown=true regardless of verified flag', () => {
    render(<ClaimStatusBadge isUnknown={true} verified={true} />)
    expect(screen.getByTestId('claim-status-badge-unknown')).toBeInTheDocument()
    expect(screen.queryByTestId('claim-status-badge-verified')).not.toBeInTheDocument()
  })
})
