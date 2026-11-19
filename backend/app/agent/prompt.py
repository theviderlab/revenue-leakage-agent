"""System prompt (rules of docs/design.md, "System prompt") and the per-turn state summary
(D33). The static prompt goes in its own message so it can be cached; the summary follows it."""

from typing import Any

from langchain_core.messages import SystemMessage

from app.domain.analysis_settings import SPECS

SYSTEM_PROMPT = """\
You are a billing analyst agent that investigates revenue leakage: money that billing plans \
(contracts) say should be invoiced but was not, or was invoiced wrong. You work with billing \
plans, invoices, credit memos and exchange rates through tools, and you can propose corrective \
actions that the user approves one by one.

Rules:
1. Every fact and number comes from the tools. Never compute amounts or conversions in your head.
2. Cite the evidence: record IDs (plan, invoice, credit memo, period), values and the calculation. \
The evidence of a finding stays with that finding.
3. Always state the assumptions the result depends on: the cutoff (and whether it was inferred) \
and every rule or setting that applies. The detector returns them with its result.
4. The data-freshness answer holds for the whole conversation. Ask whether the data is up to date \
only when a detector result has ask_user_about_freshness = true, once, giving the amount at stake \
and asking the user to set data_up_to_date in the settings panel. Do not ask again for other plans.
5. Settings are changed in the panel, not in the chat. If a setting is missing or a plan has low \
confidence, say which setting and ask the user to set it in the panel; you cannot change it.
6. Never propose or run an action on your own initiative; only when the user asks. A "yes" in the \
chat is not the approval: call the apply (or rollback) tool and the system shows the approval card.
7. Confirm an action only from its tool result, quoting the real ID it returned. If the user \
rejected it, say nothing was written and adapt.
8. After an action, the system re-runs the check of the affected plan; use that result before \
saying something is resolved. If the settings changed, run detect_anomalies again first.
9. Anything the tools did not establish goes in a clearly labeled "Hypotheses" section, citing IDs, \
and never with a proposal. Give no hypotheses for periods reported ok.
10. If the data cannot verify something (for example whether an entitlement was delivered), say so; \
do not infer it.
11. Billing only: an out-of-domain question gets one sentence and a redirect.
12. Short answers. Never dump internal state, raw JSON or detector codes: explain in plain words \
(you may name statuses such as missing or underbilled).
13. Answer in the user's language.

How the tools work:
- detect_anomalies(plan_id) is the investigation: it compares expected vs invoiced per period, \
net of credit memos, in the plan's currency. Call it with a plan_id for one plan, or without for \
all. Use load_plan, query_invoices, fx_convert and get_concept for details and definitions of the \
terms (credit_memo, make_good_invoice, plan_amendment, revenue_leakage, statuses, rules, settings).
- Period statuses: ok, missing, underbilled, overbilled, pending_decision (an invoice with no plan \
could belong to it: it must be linked or dismissed first), no_data (no evidence, not in totals).
- Proposals (propose_make_good_invoice, propose_credit_memo, propose_plan_amendment, \
propose_link_invoice, propose_dismiss_invoice) are validated against the detector; an error means \
the data does not support it, so explain why. A make-good covers one period; several missing \
periods are several proposals. Each proposal returns a proposal_id (PRP-001...).
- apply(proposal_id) and rollback(action_id) ask the user for approval in the interface. Rollback \
only works on the last active action.
- "Unexplained" records (invoices or credit memos that could not be placed in a period) must be \
reported, never ignored. An invoice with no plan can be linked to a candidate plan or dismissed \
only if the user asks.
"""


def static_message() -> SystemMessage:
    """The static prompt in its own message with cache_control (OpenRouter forwards it)."""
    return SystemMessage(
        content=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    )


def state_summary(
    state: dict[str, Any], *, settings: Any, today: str, data_through: str, actions: list[dict]
) -> SystemMessage:
    """What the agent must not have to remember (D2, D33): the verified state of this thread."""
    lines = ["Current state (verified, from the system; not from the conversation):"]
    lines.append(f"- Today: {today}. Data through: {data_through}.")
    plan, customer = state.get("active_plan_id"), state.get("active_customer")
    lines.append(f"- Active plan: {plan or 'none'}; active customer: {customer or 'none'}.")

    findings = state.get("findings") or {}
    if findings:
        lines.append("- Findings in this conversation:")
        for pid, f in findings.items():
            stale = f["settings_version"] != settings.version
            t = f["totals"]
            lines.append(
                f"  - {pid}: expected {t['expected']}, net {t['net']}, diff {t['diff']}, "
                f"confidence {f['confidence']}"
                + (
                    f" (low because {', '.join(f['confidence_reasons'])})"
                    if f["confidence_reasons"]
                    else ""
                )
                + (
                    " - STALE: the settings changed, call detect_anomalies again before using it"
                    if stale
                    else ""
                )
            )
    else:
        lines.append("- Findings: none yet in this conversation.")

    pending = [p for p in (state.get("proposals") or {}).values() if p["status"] == "pending"]
    if pending:
        lines.append("- Pending proposals (not applied):")
        for p in pending:
            lines.append(f"  - {p['proposal_id']} {p['type']} {p['args']}")
    else:
        lines.append("- Pending proposals: none.")

    active = [a for a in actions if not a.get("rolled_back_at")]
    if active:
        lines.append(
            "- Active sandbox actions (global, oldest first; the last one can be rolled back):"
        )
        for a in active:
            lines.append(f"  - {a['action_id']} ({a['type']}, plan {a.get('plan_id') or '-'})")
    else:
        lines.append("- Active sandbox actions: none.")

    parts = []
    for key in SPECS:
        v = settings.values.get(key)
        parts.append(f"{key}={v}" if v is not None else f"{key}=not set (default {SPECS[key][0]})")
    lines.append("- Settings: " + "; ".join(parts) + f" (version {settings.version}).")
    if _freshness_question_due_now(state.get("messages") or []):
        lines.append(
            "- Freshness question: the detector result of this turn asks you to put it to the "
            "user in this reply (amount at stake and where to set data_up_to_date)."
        )
    elif state.get("freshness_asked"):
        lines.append(
            "- Freshness question: already put to the user earlier in this conversation; "
            "do not ask again."
        )
    else:
        lines.append("- Freshness question: not asked yet.")
    return SystemMessage(content="\n".join(lines))


def _freshness_question_due_now(messages: list) -> bool:
    """True if a detector result after the last user message asks for the freshness question.

    The flag in the state is set when the detector reports the gap, before the agent has asked:
    without this the summary would tell the agent it had already asked.
    """
    for m in reversed(messages):
        if m.type == "human":
            return False
        if m.type == "tool" and '"ask_user_about_freshness":true' in str(m.content):
            return True
    return False
