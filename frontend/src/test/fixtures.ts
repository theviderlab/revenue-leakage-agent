import type {
  ApiEvent,
  AuditAction,
  PendingAction,
  Plan,
  PlanFindings,
  Proposal,
  SettingsResponse,
  ThreadState,
} from '../lib/types'

export const PLAN: Plan = {
  plan_id: 'PLN-1001',
  customer_name: 'Alder Creek Logistics',
  total_value: 84000,
  currency: 'USD',
  cadence: 'Monthly',
  start_date: '2025-12-01',
  amends: null,
  entitlements: ['Analytics', 'Support'],
  notes: '12-month SaaS; 7,000 USD per month, billed in advance',
  source: 'data',
}

export const SETTINGS: SettingsResponse = {
  version: 1,
  data_through: '2026-06-30',
  settings: [
    { key: 'as_of', value: null, default: null, valid_values: null },
    { key: 'data_up_to_date', value: null, default: null, valid_values: [true, false] },
    {
      key: 'period_calendar',
      value: null,
      default: 'anchored',
      valid_values: ['anchored', 'calendar'],
    },
    {
      key: 'amendment_proration',
      value: null,
      default: 'prorate',
      valid_values: ['prorate', 'full_period'],
    },
    { key: 'auto_renewal', value: null, default: false, valid_values: [false, true] },
  ],
}

export function planFindings(net: number, overrides: Partial<PlanFindings> = {}): PlanFindings {
  return {
    plan_id: 'PLN-1001',
    confidence: 'high',
    confidence_reasons: [],
    periods: [
      {
        period_start: '2026-02-01',
        period_end: '2026-02-28',
        status: 'missing',
        expected: 7000,
        invoiced: 0,
        credited: 0,
        net: 0,
        diff: -7000,
        invoice_ids: [],
        memo_ids: [],
        flags: [],
      },
      {
        period_start: '2026-05-01',
        period_end: '2026-05-31',
        status: 'overbilled',
        expected: 7000,
        invoiced: 8500,
        credited: 0,
        net: 8500,
        diff: 1500,
        invoice_ids: ['INV-1006'],
        memo_ids: [],
        flags: [],
      },
    ],
    totals: { expected: 49000, net, diff: net - 49000 },
    settings_version: 1,
    stale: false,
    ...overrides,
  }
}

export function findingsEvent(callId: string, plan: PlanFindings): ApiEvent {
  return {
    type: 'findings',
    tool_call_id: callId,
    findings: {
      plans: [plan],
      totals: plan.totals,
      assumptions: ['Billing is in advance'],
      unexplained: [],
    },
  }
}

export function proposal(status: Proposal['status'] = 'pending'): Proposal {
  return {
    proposal_id: 'PRP-001',
    type: 'make_good_invoice',
    plan_id: 'PLN-1001',
    args: { plan_id: 'PLN-1001', period_start: '2026-02-01', amount: 7000 },
    evidence: ['No invoice in 2026-02'],
    reason: 'Recover the missing February 2026 billing',
    settings_version: 1,
    status,
  }
}

export function pendingAction(): PendingAction {
  const p = proposal()
  return {
    kind: 'apply',
    proposal: p,
    action_id: null,
    summary: 'Apply PRP-001',
    evidence: p.evidence,
  }
}

export function audit(id = 'INV-MG-001', rolledBack = false): AuditAction {
  return {
    action_id: id,
    type: 'make_good_invoice',
    proposal_id: 'PRP-001',
    thread_id: 'thr-1',
    plan_id: 'PLN-1001',
    payload: { invoice_id: id },
    evidence: ['No invoice in 2026-02'],
    reason: 'Recover the missing February 2026 billing',
    applied_at: '2026-11-19T10:00:00',
    rolled_back_at: rolledBack ? '2026-11-19T11:00:00' : null,
  }
}

export function emptyState(overrides: Partial<ThreadState> = {}): ThreadState {
  return {
    active_plan_id: null,
    active_customer: null,
    findings: [],
    proposals: [],
    pending_approval: null,
    actions: [],
    settings_version: 1,
    ...overrides,
  }
}
