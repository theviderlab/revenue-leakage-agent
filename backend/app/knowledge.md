# Knowledge base

One chunk per `## key` section (D16, D32). `get_concept(key)` returns a chunk verbatim. Keys are snake_case and match the names used in the code and in the detector output.

## credit_memo

A negative invoice that reduces what the customer owes. Used when the customer was overbilled or a pricing error occurred. Example: a monthly period expects USD 7,000 but the invoice is USD 8,500, so issue a credit memo for up to USD 1,500. In the data a memo's `amount` is positive and is subtracted from its invoice. The agent only proposes one for an invoice in an `overbilled` period of a plan with `high` confidence, for at most the excess.

## plan_amendment

An update to the contract (total, cadence, entitlements or notes) when the agreement itself changes. Example: raise a plan's total or add "Premium Support". In the sandbox it is a new version of the same `plan_id`, effective from the start of a period (`effective_from`). Identity fields (`plan_id`, `customer_name`, `start_date`, `currency`) cannot change. A plan that another plan replaces through `amends` (PLN-2001) cannot be amended: amend the plan that replaces it.

## make_good_invoice

A new invoice to recover missed or underbilled revenue. Example: missing February 2026 billing -> invoice USD 7,000. Each make-good covers one period (`period_start`). The agent only proposes one for a `missing` or `underbilled` period of a plan with `high` confidence, for at most the shortfall.

## revenue_leakage

Money a contract says should be billed but was not, or was billed wrong. The agent compares what each billing plan expects per period with what was invoiced, net of credit memos, in the plan's currency. A negative difference is under-billing (leakage); a positive one is over-billing.

## billing_in_advance

Fixed rule. Each invoice belongs to the period that contains its `issue_date`, not to the period before it. Make-good invoices belong to their explicit `period_start`.

## term_12_months

Fixed rule. Every plan lasts 12 months from its `start_date`. It is the only duration consistent with all four plans: PLN-1001 says "12-month", and 4 x 28,000 = 112,000 for PLN-2001-A1.

## amount_per_period

Fixed rule. Expected amount per period = `total_value` / periods in 12 months (Monthly 12, Quarterly 4, Annual 1), using the plan version in force at the start of the period. PLN-1001 gives 7,000 and PLN-2001-A1 gives 28,000, as their notes say.

## amendment_replaces_plan

Fixed rule. A plan with `amends` replaces the plan it amends from its own `start_date`, and its `total_value` covers 12 months from there. PLN-2001-A1 replaces PLN-2001 from 2025-07-01: PLN-2001 stops there.

## proration

Fixed rule. A partial period is prorated by whole months when the stretch is whole months (June of a quarter = 1/3), by days otherwise (PLN-2001 May-June = 47/92 = 10,728.26). Amounts are Decimal, rounded half-up to cents per period; totals are sums of rounded periods.

## currency_conversion

Fixed rule. Amounts are compared in the plan's currency. An invoice in another currency is converted with the rate of its own `issue_date`, only in the published direction (EUR to USD). With no such rate, the invoice goes to the unexplained list as `no_fx_rate`. Credit memos are subtracted after converting the invoice.

## credit_memo_netting

Fixed rule. A credit memo is subtracted from its invoice, in the period of that invoice. If the invoice is unexplained, its memos are too, with the same reason.

## unpaid_invoices

Fixed rule. An `unpaid` invoice counts as billed and gets the `unpaid` flag. It is a collection issue, not leakage.

## late_issuance

Fixed rule. Late issuance (days between the start of the period and `issue_date`) is a flag. It does not change amounts.

## entitlements

Fixed rule. Entitlements (Analytics, Support, Premium Support, Consulting) are not verified: there is no price per entitlement in the data, so the agent cannot say whether they were delivered or billed.

## as_of

Setting. The cutoff date. Periods that start up to `as_of` are expected. Unset means today. Data issued after `as_of` is not counted (unexplained, `after_as_of`). Changed only in the panel.

## data_up_to_date

Setting. Whether the data is complete up to the cutoff. Unset: periods that start after the last data date (`data_through`) are `no_data`. True: that gap is real and those periods count as `missing`. Changed only in the panel.

## period_calendar

Setting. `anchored` (default): periods start on `start_date`. `calendar`: periods follow calendar months, quarters or years, with a prorated first and last period. It changes PLN-2001 and PLN-3001. While unset, those plans have `low` confidence.

## amendment_proration

Setting. `prorate` (default): the replaced plan's last period is cut where the amendment starts and prorated. `full_period`: that period is billed in full to the replaced plan. It changes PLN-2001. While unset, that plan has `low` confidence.

## auto_renewal

Setting. `false` (default): a plan ends after 12 months. `true`: another 12-month term starts with the latest version of the plan. It matters only for periods after the end of the term. While unset and relevant, the plan has `low` confidence.

## data_through

The last `issue_date` in `data/` (2026-06-30), capped at `as_of`. Never moved by sandbox records. Periods that start after it and have no invoices are `no_data`, not `missing`, unless `data_up_to_date` is true.

## confidence

Per plan, `high` or `low`. `low` means an assumption the data does not decide (`period_calendar`, `amendment_proration`, `auto_renewal`) is unset and would change this plan's result. A plan with `low` confidence admits no proposals until the user sets those assumptions in the panel.

## ok

Period status: invoiced net of credit memos equals the expected amount.

## missing

Period status: expected, but no invoice. A make-good can recover up to the shortfall (plan confidence `high`).

## underbilled

Period status: invoiced net is below the expected amount. A make-good can recover up to the shortfall (plan confidence `high`). The cause is not known to the detector.

## overbilled

Period status: invoiced net is above the expected amount. A credit memo can return up to the excess (plan confidence `high`).

## pending_decision

Period status: the period has a finding and an invoice with no plan could belong to it (same customer, issued inside the period). Nothing is proposed for it until that invoice is linked to a plan or dismissed.

## no_data

Period status: the period starts after `data_through`, so there is no evidence either way. It does not count in totals and admits no proposals.

## no_plan

Unexplained reason: the invoice has no `plan_id` (or an unknown one). The candidates are the plans of the same customer whose term contains its `issue_date`. It can be linked to a candidate or dismissed, each with approval.

## no_fx_rate

Unexplained reason: the invoice or memo is in another currency and there is no exchange rate for its exact date and direction.

## outside_term

Unexplained reason: the invoice's `issue_date` is outside every period of its plan (before the start, or after the end of the term without renewal).

## after_as_of

Unexplained reason: the record was issued after the cutoff `as_of`, so it is not counted (or its period starts after it).

## orphan_memo

Unexplained reason: the credit memo points to an invoice that does not exist.

## dismissed

Unexplained reason: the user dismissed an invoice with no plan. It stays listed but no longer blocks a period.
