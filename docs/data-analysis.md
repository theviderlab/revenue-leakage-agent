# Data analysis

Analysis of what is in `data/` (read-only) and of what the data decides about revenue leakage. It was written before implementing the detector: the fixed rules and the assumptions of [D19](design.md#d19-deterministic-detector-detect_anomalies) and [D20](design.md#d20-assumptions-fixed-rules-and-settings) come from here.

The portfolio has four plans and three customers, all in USD except one invoice in EUR. The data runs until 2026-06-30; the application clock (and the fixed date of the tests) is 2026-11-18.

## 1. Inventory

Four JSON files, with no database and no external API.

### `billing_plans.json` (4 records)

| Field | Type | Example | Shape | Nulls |
|---|---|---|---|---|
| `plan_id` | string | `PLN-1001` | `PLN-NNNN`, amendments `PLN-NNNN-A#` | never empty |
| `customer_name` | string | `Alder Creek Logistics` | free text | never empty |
| `total_value` | number | `84000` | contract amount | never null |
| `currency` | string | `USD` | `USD` in all four | never empty |
| `cadence` | string | `Monthly` | `Monthly` (1), `Quarterly` (2), `Annual` (1) | never empty |
| `start_date` | date | `2025-12-01` | ISO `YYYY-MM-DD` | never null |
| `amends` | string | `PLN-2001` | `plan_id` of the amended plan | **optional**: only in `PLN-2001-A1` |
| `entitlements` | list | `["Analytics", "Support"]` | free names, no price | may be missing |
| `notes` | string | free text | — | may be missing |

| `plan_id` | Customer | `total_value` | Cadence | `start_date` | Replaces |
|---|---|---:|---|---|---|
| `PLN-1001` | Alder Creek Logistics | 84,000 | Monthly | 2025-12-01 | — |
| `PLN-2001` | Vertex Dynamics | 84,000 | Quarterly | 2025-05-15 | — |
| `PLN-2001-A1` | Vertex Dynamics | 112,000 | Quarterly | 2025-07-01 | `PLN-2001` |
| `PLN-3001` | Lumen Health | 120,000 | Annual | 2025-07-15 | — |

- **`PLN-1001`:** 84,000 USD over 12 months, 7,000 per month. `notes`: "12-month SaaS; 7,000 USD per month, billed in advance".
- **`PLN-2001`:** 84,000 USD, 21,000 per quarter. In force from 2025-05-15; `PLN-2001-A1` replaces it from 2025-07-01. `notes`: "Quarterly target 21,000 USD; superseded by PLN-2001-A1 from 2025-07-01 (see amendment)".
- **`PLN-2001-A1`:** 112,000 USD, 28,000 per quarter from 2025-07-01. `notes`: "Amendment effective 2025-07-01. Quarterly target now 28,000 USD".
- **`PLN-3001`:** 120,000 USD per year from 2025-07-15. `notes`: "Enterprise annual; 120,000 USD for 12 months from 2025-07-15".

### `invoices.json` (12 records)

| Field | Type | Example | Shape | Nulls |
|---|---|---|---|---|
| `invoice_id` | string | `INV-1001` | `INV-NNNN` | never empty |
| `plan_id` | string | `PLN-1001` | same as in plans | **empty (`""`) in `INV-4001`** |
| `customer_name` | string | `Alder Creek Logistics` | free text | never empty |
| `issue_date` | date | `2025-12-22` | ISO `YYYY-MM-DD` | never null |
| `due_date` | date | `2026-01-06` | ISO `YYYY-MM-DD` | never null |
| `amount_invoiced` | number | `7000` | amount | never null |
| `currency` | string | `USD` | `USD` (10), `EUR` (2: `INV-2001`, `INV-1003`) | never empty |
| `status` | string | `paid` | `paid` (11), `unpaid` (1: `INV-1005`) | never empty |
| `description` | string | free text | — | may be missing |

### `credit_memos.json` (1 record)

| Field | Type | Example |
|---|---|---|
| `memo_id` | string | `CR-001` |
| `plan_id` | string | `PLN-2001-A1` |
| `invoice_id` | string | `INV-2001` |
| `amount` | number | `5000` (positive) |
| `currency` | string | `USD` |
| `issue_date` | date | `2025-08-28` |
| `reason` | string | "FX overbilling adjustment (EUR to USD)" |

### `exchange_rates.json` (1 record)

| Field | Type | Example |
|---|---|---|
| `date` | date | `2025-08-20` |
| `from_currency` | string | `EUR` |
| `to_currency` | string | `USD` |
| `rate` | number | `1.10` |

## 2. Relationships

| Relationship | Cases | Notes |
|---|---|---|
| `invoices.plan_id` → `billing_plans.plan_id` | 11 of 12 | `INV-4001` has an empty `plan_id` |
| `INV-4001.customer_name` → some plan | `Vertex Dynamics` matches `PLN-2001` and `PLN-2001-A1` | there is no way to pick between the two versions by ID |
| `billing_plans.amends` → `plan_id` | `PLN-2001-A1` → `PLN-2001` | — |
| `credit_memos.invoice_id` → invoice | `CR-001` → `INV-2001` | — |
| `credit_memos.plan_id` = its invoice's `plan_id` | `PLN-2001-A1` = `PLN-2001-A1` | — |
| Invoice in a currency other than the plan's, with a rate | `INV-2001` (EUR) has a rate on its `issue_date` (2025-08-20) | there is no rate on the date of `CR-001` (2025-08-28) or on any other date |

Invoices per plan: `PLN-1001` 7, `PLN-2001` 0, `PLN-2001-A1` 3, `PLN-3001` 1, no plan 1.

The customer is identified only by `customer_name`; there is no `customer_id`. The two Vertex versions are told apart by `plan_id` (`PLN-2001-A1`) and by the `amends` field.

## 3. Date range

| Data | Min | Max | Notes |
|---|---|---|---|
| `billing_plans.start_date` | 2025-05-15 (`PLN-2001`) | 2025-12-01 (`PLN-1001`) | the amendment starts on 2025-07-01 |
| Dates in `notes` | 2025-07-01 | 2026-07-15 | `PLN-3001` says "12 months from 2025-07-15" |
| `invoices.issue_date` | 2025-08-20 (`INV-2001`) | 2026-06-30 (`INV-1007`) | none before 2025-08-20 |
| `invoices.due_date` | 2025-09-04 | 2026-07-15 | — |
| `exchange_rates.date` | 2025-08-20 | 2025-08-20 | = `issue_date` of `INV-2001` |

Payment term (`due_date − issue_date`): 15 days on every invoice.

The last data point is 2026-06-30 (issue date of `INV-1007`; also the last day of the `PLN-2001-A1` term). Today is 2026-11-18: there are 4.6 months without data, `PLN-1001` ends on 2026-11-30 and `PLN-3001` ended on 2026-07-14.

## 4. Expected vs invoiced

Calculation rules, justified in section 5:

- The contract lasts 12 months from `start_date`.
- The amount per period is `total_value` / periods in 12 months.
- A partial period is prorated by whole months if the span is whole months, by days otherwise.
- A plan with `amends` replaces the amended plan from its `start_date`.
- Each invoice is attributed to the period that contains its `issue_date`.
- Memos are subtracted from their invoice, in the invoice's period.
- Everything is converted to the plan's currency.

### 4.1 `PLN-1001` — Alder Creek Logistics, 84,000 USD, Monthly, from 2025-12-01

7,000 USD per month. Term: 2025-12-01 to 2026-11-30 (12 months).

| Period | Expected | Invoice | Invoiced | CM | Net | Diff. | Status |
|---|---:|---|---:|---:|---:|---:|---|
| 2025-12 | 7,000 | `INV-1001` | 7,000 | — | 7,000 | 0 | ok |
| 2026-01 | 7,000 | `INV-1002` | 7,000 | — | 7,000 | 0 | ok |
| 2026-02 | 7,000 | — | 0 | — | 0 | **−7,000** | missing |
| 2026-03 | 7,000 | `INV-1004` | 7,000 | — | 7,000 | 0 | ok |
| 2026-04 | 7,000 | `INV-1005` (unpaid) | 7,000 | — | 7,000 | 0 | ok, flag `unpaid` |
| 2026-05 | 7,000 | `INV-1006` | 8,500 | — | 8,500 | **+1,500** | overbilled |
| 2026-06 | 7,000 | `INV-1007` | 7,000 | — | 7,000 | 0 | ok |
| **Total** | **49,000** | | **43,500** | **0** | **43,500** | **−5,500** | |

- `INV-1003` (400 EUR, 2026-01-20) has no available rate: it goes to the residue (`no_fx_rate`) and does not count in the January period.
- `INV-1005` has been unpaid since 2026-04-21; it counts as invoiced, with a flag (it is a collection problem, not leakage).
- `INV-1006` bills 8,500 against 7,000: the only excess in the portfolio (1,500).
- `INV-1007` was issued on 2026-06-30, the last day with data: it sets `data_through`.

### 4.2 `PLN-2001` — Vertex Dynamics, 84,000 USD, Quarterly, from 2025-05-15, replaced by `PLN-2001-A1` from 2025-07-01

21,000 USD per quarter (84,000 / 4). Effective term: 2025-05-15 to 2025-06-30 (47 days). The amendment replaces it in the middle of the quarter: the period that starts on 2025-07-01 already belongs to `PLN-2001-A1`.

| Period | Expected | Invoiced | CM | Net | Diff. | Status |
|---|---:|---:|---:|---:|---:|---|
| 2025-05-15 – 2025-06-30 (47 of 92 days, prorated) | 10,728.26 | 0 | — | 0 | **−10,728.26** | missing |
| **Total** | **10,728.26** | **0** | **0** | **0** | **−10,728.26** | |

There is no invoice on record for this period: the first Vertex invoice is `INV-2001` (2025-08-20), which falls in the amendment's quarter.

Variants: with the whole quarter charged to the old plan, 21,000 (−21,000); with calendar quarters, April–June prorated 47/91 = 10,846.15 (−10,846.15).

### 4.3 `PLN-2001-A1` — Vertex Dynamics, 112,000 USD, Quarterly, from 2025-07-01

28,000 USD per quarter (112,000 / 4). Term: 2025-07-01 to 2026-06-30.

| Period | Expected | Invoice | Invoiced | CM | Net | Diff. | Status |
|---|---:|---|---:|---:|---:|---:|---|
| 2025-07-01 – 09-30 | 28,000 | `INV-2001` | 30,000 EUR × 1.10 = 33,000 | `CR-001` −5,000 | 28,000 | 0 | ok, flag `currency_mismatch` |
| 2025-10-01 – 12-31 | 28,000 | — | 0 | — | 0 | **−28,000** | pending_decision (`INV-4001`) |
| 2026-01-01 – 03-31 | 28,000 | `INV-2002` | 28,000 | — | 28,000 | 0 | ok |
| 2026-04-01 – 06-30 | 28,000 | `INV-2003` | 28,000 | — | 28,000 | 0 | ok |
| **Total** | **112,000** | | **89,000** | **−5,000** | **84,000** | **−28,000** | |

- `INV-2001` is in EUR; the 1.10 rate is exactly from its issue date (2025-08-20). `CR-001` (5,000 USD, 8 days later) leaves the quarter at 28,000 net: it covers the difference between the converted 33,000 and the expected 28,000.
- The plan's whole difference is the fourth quarter (Oct–Dec): there is no invoice, and `INV-4001` (15,000 USD, Vertex Dynamics, 2025-11-20, empty `plan_id`) is its only candidate. Until that is decided, the period stays `pending_decision`.
- If `INV-4001` is linked to this plan, the quarter becomes `underbilled` (15,000 vs 28,000, −13,000). If it is dismissed, it stays `missing` (−28,000) and accepts a make-good.

### 4.4 `PLN-3001` — Lumen Health, 120,000 USD, Annual, from 2025-07-15

120,000 USD per year. Term: 2025-07-15 to 2026-07-14.

| Period | Expected | Invoice | Invoiced | CM | Net | Diff. | Status |
|---|---:|---|---:|---:|---:|---:|---|
| 2025-07-15 – 2026-07-14 | 120,000 | `INV-3001` | 100,000 | — | 100,000 | **−20,000** | underbilled |
| **Total** | **120,000** | | **100,000** | **0** | **100,000** | **−20,000** | |

`INV-3001` was issued on 2025-09-02, inside the period. Neither `notes` nor the description ("Annual plan") explains what the missing 20,000 corresponds to.

With calendar periods, the year splits into 2025-07-15 – 12-31 (expected 55,890.41, invoiced 100,000: `overbilled` +44,109.59) and 2026-01-01 – 07-14 (expected 64,109.59: `missing`). The total difference is still −20,000.

### 4.5 Totals

| Plan | Expected | Invoiced | CM | Net | Diff. |
|---|---:|---:|---:|---:|---:|
| `PLN-1001` | 49,000 | 43,500 | 0 | 43,500 | −5,500 |
| `PLN-2001` | 10,728.26 | 0 | 0 | 0 | −10,728.26 |
| `PLN-2001-A1` | 112,000 | 89,000 | 5,000 | 84,000 | −28,000 |
| `PLN-3001` | 120,000 | 100,000 | 0 | 100,000 | −20,000 |
| **Total** | **291,728.26** | **232,500** | **5,000** | **227,500** | **−64,228.26** |

### 4.6 Cutoffs and the data gap

With `as_of = data_through` (2026-06-30) the only expected periods are those in the table above. A later cutoff adds periods that start after 2026-06-30: they are `no_data` while `data_up_to_date` is not set.

| Cutoff and settings | `PLN-1001` | `PLN-2001` | `PLN-2001-A1` | `PLN-3001` | Total |
|---|---:|---:|---:|---:|---:|
| `as_of = 2026-06-30` (base) | −5,500 | −10,728.26 | −28,000 | −20,000 | **−64,228.26** |
| `as_of = 2026-11-18`, `data_up_to_date = null` | −5,500 | −10,728.26 | −28,000 | −20,000 | **−64,228.26** |
| `as_of = 2026-11-18`, `data_up_to_date = true` | −40,500 | −10,728.26 | −28,000 | −20,000 | **−99,228.26** |
| `as_of = 2026-11-18`, `data_up_to_date = true`, `auto_renewal = true` | −40,500 | −10,728.26 | −84,000 | −140,000 | **−275,228.26** |
| `as_of = 2026-02-28` | −7,000 | −10,728.26 | −28,000 | −20,000 | **−65,728.26** |

- **Gap 2026-07-01 → 2026-11-18:** five months of `PLN-1001` (35,000). `PLN-2001-A1` ended on 2026-06-30 and `PLN-3001` on 2026-07-14; they only generate new periods if `auto_renewal` is on.
- **Cutoff 2026-02-28:** `PLN-1001` only December–February; `INV-1004` … `INV-1007` and `INV-2003` fall after the cutoff and go to the residue with reason `after_as_of`; `INV-4001` stays `no_plan`.

## 5. Assumptions

### Fixed rules (what the data decides)

| Rule | Source |
|---|---|
| 12 months from `start_date` for every plan | `PLN-1001` and `PLN-3001` say so in `notes`; `PLN-2001-A1` 112,000 / 4 = 28,000 matches "Quarterly target now 28,000" |
| Amount per period = `total_value` / periods in 12 months | `PLN-1001` 7,000 = 84,000 / 12; `PLN-2001` 21,000 = 84,000 / 4; `PLN-3001` 120,000 |
| A plan with `amends` replaces the amended plan from its `start_date` | `PLN-2001-A1` starts on 2025-07-01 and its `total_value` covers 12 months from there: 4 × 28,000 = 112,000 |
| Each invoice is attributed to the period that contains its `issue_date` (billing in advance) | `PLN-1001` says "billed in advance"; of its seven invoices, six fall in the billed month and `INV-1003` goes to the residue; `INV-3001` inside the year; `INV-2001` inside the quarter |
| Proration of a partial period: by whole months if the span is whole months, by days otherwise | `PLN-2001` May–June = 47/92; `PLN-3001` with the calendar = 170/365 |
| Rate of the `issue_date`, only in the published direction; with no rate, to the residue | `INV-2001` has a rate exactly on its date; `INV-1003` has none |
| Memos are subtracted from their invoice, in the same period; positive `amount` | `CR-001` leaves `INV-2001` at 28,000 net |
| An `unpaid` invoice counts as invoiced, with a flag | `INV-1005` is overdue: a collection problem, not leakage |
| Late issuing is a flag and does not change amounts | `INV-2001` was issued 50 days after the start of the quarter; `INV-3001`, 49 days after the start of the year |
| Entitlements are not verified: there is no price per entitlement | `Analytics`, `Support`, `Premium Support`, `Consulting` do not appear with an amount on any invoice |

### Settings (what the data does not decide)

| Setting | Values (default first) | Plans whose readings differ | Ambiguity |
|---|---|---|---|
| `period_calendar` | `anchored` (to `start_date`) / `calendar` | `PLN-2001`, `PLN-3001` | A6, A10 |
| `amendment_proration` | `prorate` / `full_period` (the period of the cut, fully charged to the amended plan) | `PLN-2001` | A5 |
| `auto_renewal` | `false` / `true` (when the term ends, another 12-month term repeats with the latest version) | `PLN-2001-A1`, `PLN-3001` when the cutoff passes the end of their term | A2 |

With an unset setting and two different readings, the plan is `confidence: low` and accepts no proposals. The cutoff (`as_of`) and `data_up_to_date` are settings in the same sense ([D21](design.md#d21-cutoff-as_of-and-data_through)).

## 6. What does not add up

| Record | Problem | Amount |
|---|---|---:|
| `INV-1003` | 400 EUR with no rate on its `issue_date` or any other date | 400 EUR (residue) |
| `INV-4001` | empty `plan_id`; 15,000 USD matches neither 28,000 (A1 quarter) nor 7,000 | 15,000 (residue) |
| `PLN-1001` 2026-02 | period with no invoice | 7,000 |
| `INV-1006` | 8,500 against 7,000 in May | 1,500 excess |
| `PLN-2001` | quarter before the amendment with no invoice | 10,728.26 (or 21,000) |
| `PLN-3001` | 100,000 against 120,000; the data does not explain the cause | 20,000 |

Amounts that match no period or proration:

- `INV-1003`: 400 EUR is neither 7,000 nor a fraction of 84,000.
- `INV-4001`: 15,000 is not 28,000 (quarter), 7,000 (month), 21,000 (old quarter) or 10,728.26 (proration), nor any whole fraction of 84,000 or 112,000.
- `INV-1006`: 8,500 is not a multiple of 7,000; the excess is 1,500.
- `INV-3001`: 100,000 is neither 120,000 nor a natural fraction of it (exactly 5/6, but there is no 10-month period).

## 7. Observations

- **Alder Creek invoice numbering:** `INV-1001` … `INV-1007`, with no gaps; `INV-1003` is the only one in EUR and `INV-1005` the only unpaid one.
- **Numbering blocks per customer:** `INV-100x` Alder Creek, `INV-200x` Vertex, `INV-300x` Lumen, `INV-400x` no plan. `INV-4001` belongs to Vertex but sits in the 4xxx block.
- **`INV-4001`:** empty `plan_id`; the `description` says "Payment with no contract reference". It calls it a "payment" although it is an invoice, and it does not mention the amendment. It is `paid`.
- **`INV-2001` in EUR for a USD plan.** The available rate is exactly from its issue date. It is `paid`, and `CR-001` was issued 8 days later (2025-08-28), in USD, against an invoice in EUR. There is no rate for the memo's date and no payment date.
- **Late billing:** `INV-2001` was issued 50 days after the start of its quarter; `INV-3001`, 49 days after the start of the year; `INV-1001`, 21 days after the start of the month.
- **`INV-1005` `unpaid`** (7,000 USD, due 2026-04-21). It is the only unpaid invoice.
- **Credit memo sign:** `CR-001.amount` is positive; the domain defines a credit memo as a negative invoice.
- **`PLN-2001.notes`:** says "superseded by PLN-2001-A1 from 2025-07-01 (see amendment)"; there is no `superseded_by` field. The replacement comes from the `amends` field of `PLN-2001-A1`, not from parsing `notes`.
- **`PLN-2001-A1` only changes `total_value`:** same `entitlements`, cadence and currency as `PLN-2001`.
- **Entitlements that do not appear in the descriptions:** `Support` (`PLN-1001`), `Premium Support` (`PLN-2001`, `PLN-2001-A1`), `Consulting` (`PLN-3001`). The descriptions only say "subscription", "Annual plan" or "Payment with no contract reference". There is no price per entitlement.
- **Structural data only in `notes`:** 12 months and 7,000/month (`PLN-1001`), 28,000/quarter and the effective date (`PLN-2001-A1`), the replacement date (`PLN-2001`), 12 months from 2025-07-15 (`PLN-3001`).
- **No `customer_id`:** the customer is identified only by `customer_name` (identical strings in every case).
- **Uniform payment term:** 15 days on all 12 invoices.
- **No invoice for `PLN-2001` or for the fourth quarter of `PLN-2001-A1`**, and `PLN-2001-A1` ends exactly on the day of the last data point.

## 8. Ambiguities for a human to decide

| # | Ambiguity | Options | Amount at stake |
|---|---|---|---|
| A1 | Cutoff date | last data point 2026-06-30 / today 2026-11-18 / 2026-02-28 / end of each term | total between −64,228.26 and −275,228.26: **211,000** |
| A2 | Automatic renewal when the 12 months end | no / yes | **176,000** up to 2026-11-18 (`PLN-2001-A1` 56,000, `PLN-3001` 120,000) |
| A3 | Span 2026-07-01 → 2026-11-18 without data | not billed / the data has not arrived | **35,000** without renewal (`PLN-1001`); 211,000 with renewal |
| A4 | Term and price of `PLN-2001` | 12 months at 21,000/quarter (84,000 / 4) / other | no basis for another figure |
| A5 | Proration of `PLN-2001` at the 2025-07-01 cut | prorate 47/92 / full quarter | **10,271.74** (10,728.26 vs 21,000) |
| A6 | `PLN-2001` with anchored or calendar periods | 47/92 = 10,728.26 / 47/91 = 10,846.15 | **117.89** |
| A7 | Scope of `PLN-2001-A1.total_value` | 112,000 for 12 months from 2025-07-01 / 112,000 for the whole Vertex contract from 2025-05-15 | **10,728.26** (the span before the amendment) |
| A8 | Which plan `INV-4001` belongs to | `PLN-2001-A1` Oct–Dec / `PLN-2001` / none (payment with no contract) | **15,000** |
| A9 | `PLN-3001`: 100,000 invoiced vs 120,000 | underbilling / discount or unbilled entitlement (`Analytics`, `Consulting`) | **20,000** |
| A10 | `PLN-3001` with anchored or calendar periods | one anchored year / split calendar year | 0 at the end of the term; it changes which period comes up short (**44,109.59** excess and **64,109.59** shortfall) |
| A11 | `INV-2001` + `CR-001`: is it settled? | settled (28,000 USD net at the issue-date rate) / review with another rate date (memo, payment) | **5,000** (risk of counting it twice); another rate is not available |
| A12 | `INV-1005` unpaid: does it count as invoiced? | invoiced (collection problem) / not collected (leakage) | **7,000** |
| A13 | Late invoices (`INV-1001`, `INV-2001`, `INV-3001`): are they an anomaly? | yes / no | 0 in amount; it only affects dates |
| A14 | Entitlements with no price and no mention on invoices | ignore / review | unknown |
| A15 | Conversion outside 2025-08-20 or in a direction other than EUR→USD | no rate | unknown (**33,000** if `INV-2001` could not be converted) |
