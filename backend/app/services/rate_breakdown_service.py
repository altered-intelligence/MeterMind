"""Deterministic rate & fee breakdown for a parsed CoServ bill.

Splits each service section (electric, gas) into spend categories — fixed cost, variable
energy/usage, adjustments/credits, local fees, taxes, optional/rounding — and derives
effective-rate metrics so the user can see what portion of the bill is fixed versus
variable, and how the *true* effective rate (all charges) compares to the raw
energy/commodity rate.

Everything is computed deterministically from the already-parsed line items stored on the
:class:`Bill` ORM row. No LLM, no floats — money is :class:`Decimal` throughout and is
serialized to precision-preserving strings at the edge.

Categorization is driven by a label -> category map. CoServ tweaks spacing/wording and
appends usage to some labels (e.g. "Energy Charge 1,725 kWh"), so labels are matched by
normalized prefix. Anything unrecognized lands in ``uncategorized`` and emits a warning;
it is still summed so the section continues to reconcile to the service total.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

from ..models import Bill
from ..schemas import RateBreakdown, RateBreakdownCategory, ServiceRateBreakdown

RATE_BREAKDOWN_VERSION = "rate-breakdown-v0.1.0"

MONEY_TOL = Decimal("0.01")
_RATE_PLACES = Decimal("0.0001")
_PCT_PLACES = Decimal("0.0001")
_MONEY_PLACES = Decimal("0.01")

# Category keys per service. Order is display order. ``total`` is a presentational bucket
# (the printed Total Current Charges) and is NEVER summed into the categorized total.
ELECTRIC_CATEGORY_ORDER = [
    "fixed_charges",
    "variable_energy_charges",
    "credits_and_adjustments",
    "local_fees",
    "taxes",
    "optional_or_rounding",
]
GAS_CATEGORY_ORDER = [
    "fixed_charges",
    "variable_usage_charges",
    "purchased_gas_charges",
    "adjustments",
    "local_fees",
    "taxes",
]

CATEGORY_LABELS = {
    "fixed_charges": "Fixed charges",
    "variable_energy_charges": "Variable energy charges",
    "variable_usage_charges": "Variable usage charges",
    "purchased_gas_charges": "Purchased gas charges",
    "credits_and_adjustments": "Credits & adjustments",
    "adjustments": "Adjustments",
    "local_fees": "Local fees",
    "taxes": "Taxes",
    "optional_or_rounding": "Optional / rounding",
    "uncategorized": "Uncategorized",
    "total": "Total current charges",
}

# Normalized-label-prefix -> category. Keys are lowercased and whitespace-collapsed; a line
# item matches a key when its normalized label starts with that key. Longer keys are tried
# first so "purchased gas factor" wins over a hypothetical "purchased gas".
_ELECTRIC_LABEL_MAP = {
    "customer charge": "fixed_charges",
    "energy charge": "variable_energy_charges",
    "pcrf": "credits_and_adjustments",
    "scrf": "credits_and_adjustments",
    "municipal franchise fee": "local_fees",
    "sales tax": "taxes",
    "operation roundup": "optional_or_rounding",
    # Recurring/occasional CoServ electric riders:
    "connection fee": "fixed_charges",          # one-time service connection
    "membership": "fixed_charges",              # co-op "Membership Applied"
    "capital credit": "credits_and_adjustments",  # "Capital Credit Applied" (refund)
}
_GAS_LABEL_MAP = {
    "customer charge": "fixed_charges",
    "interim rate adjustment": "adjustments",
    "usage charge": "variable_usage_charges",
    "purchased gas factor": "purchased_gas_charges",
    "municipal franchise fee": "local_fees",
    "gross receipts tax": "taxes",
    "sales tax": "taxes",
    # Recurring/occasional CoServ gas riders:
    "gas rate case expense": "adjustments",     # regulatory rate-case cost recovery
    "weather norm": "adjustments",              # "Weather Norm Adj.-Res" normalization
    "initial gas meter": "fixed_charges",       # one-time meter set/read
    "pipeline safety": "local_fees",            # regulatory pipeline-safety fee
}

# Labels that denote the section total rather than a chargeable line.
_TOTAL_LABELS = {"total current charges"}


def _norm(label: str) -> str:
    return " ".join((label or "").split()).lower()


def _categorize(label: str, label_map: dict[str, str]) -> Optional[str]:
    """Return the category key for a line-item label, or ``None`` if unknown.

    Returns ``"total"`` for the section-total line. Matching is longest-prefix-first so a
    more specific key is preferred over a shorter one that is also a prefix.
    """
    n = _norm(label)
    if n in _TOTAL_LABELS:
        return "total"
    for key in sorted(label_map, key=len, reverse=True):
        if n.startswith(key):
            return label_map[key]
    return None


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _q_money(v: Optional[Decimal]) -> Optional[str]:
    if v is None:
        return None
    return str(v.quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP))


def _q_rate(num: Optional[Decimal], den: Optional[Decimal]) -> Optional[str]:
    """Safe division -> 4dp string. Returns ``None`` for missing/zero denominator."""
    if num is None or den is None or den == 0:
        return None
    return str((num / den).quantize(_RATE_PLACES, rounding=ROUND_HALF_UP))


def _q_pct(num: Optional[Decimal], den: Optional[Decimal]) -> Optional[str]:
    """Fraction of ``den`` (e.g. 0.42), 4dp string. ``None`` for missing/zero denominator."""
    if num is None or den is None or den == 0:
        return None
    return str((num / den).quantize(_PCT_PLACES, rounding=ROUND_HALF_UP))


def _service_line_items(bill: Bill, service_type: str) -> list:
    return [li for li in bill.line_items if li.service_type == service_type]


def _build_service(
    *,
    service_type: str,
    line_items: Iterable,
    category_order: list[str],
    label_map: dict[str, str],
    total_current_charges: Optional[Decimal],
    usage: Optional[Decimal],
) -> ServiceRateBreakdown:
    # Accumulate signed amounts and member labels per category.
    sums: dict[str, Decimal] = {}
    names: dict[str, list[str]] = {}
    uncategorized: list[str] = []
    warnings: list[str] = []

    for li in line_items:
        cat = _categorize(li.label, label_map)
        if cat == "total":
            continue  # presentational only; excluded from category summing
        amount = _dec(li.amount) or Decimal("0")
        if cat is None:
            cat = "uncategorized"
            uncategorized.append(li.label)
            warnings.append(
                f"Unrecognized {service_type} line item '{li.label}' "
                f"({_q_money(amount)}); categorized as uncategorized."
            )
        sums[cat] = sums.get(cat, Decimal("0")) + amount
        names.setdefault(cat, []).append(li.label)

    total_categorized = sum(sums.values(), Decimal("0")) if sums else Decimal("0")
    total = _dec(total_current_charges)

    # Build category list in display order, appending uncategorized last if present.
    order = list(category_order)
    if "uncategorized" in sums and "uncategorized" not in order:
        order.append("uncategorized")

    categories: list[RateBreakdownCategory] = []
    for key in order:
        amt = sums.get(key)
        categories.append(
            RateBreakdownCategory(
                key=key,
                label=CATEGORY_LABELS.get(key, key),
                amount=_q_money(amt) if amt is not None else _q_money(Decimal("0")),
                percentage_of_service_total=_q_pct(amt if amt is not None else Decimal("0"), total),
                line_item_names=names.get(key, []),
            )
        )

    # Always surface the printed total as its own (non-summed) category for the UI.
    categories.append(
        RateBreakdownCategory(
            key="total",
            label=CATEGORY_LABELS["total"],
            amount=_q_money(total),
            percentage_of_service_total="1" if total not in (None, Decimal("0")) else None,
            line_item_names=[],
        )
    )

    reconciles = total is not None and abs(total_categorized - total) <= MONEY_TOL
    if total is not None and not reconciles:
        warnings.append(
            f"{service_type.capitalize()} categories sum to {_q_money(total_categorized)} "
            f"but Total Current Charges is {_q_money(total)} "
            f"(off by {_q_money(total_categorized - total)})."
        )

    metrics = _service_metrics(
        service_type=service_type,
        sums=sums,
        total=total,
        total_categorized=total_categorized,
        usage=_dec(usage),
    )

    return ServiceRateBreakdown(
        service_type=service_type,
        total_current_charges=_q_money(total),
        total_categorized=_q_money(total_categorized),
        reconciles=reconciles,
        categories=categories,
        metrics=metrics,
        uncategorized_labels=uncategorized,
        warnings=warnings,
    )


def _service_metrics(
    *,
    service_type: str,
    sums: dict[str, Decimal],
    total: Optional[Decimal],
    total_categorized: Decimal,
    usage: Optional[Decimal],
) -> dict[str, Optional[str]]:
    """Derived rate/percentage metrics. ``total`` is preferred as the charges base; when it
    is missing we fall back to the categorized sum so the UI still has numbers."""
    base = total if total is not None else total_categorized
    fixed = sums.get("fixed_charges")
    taxes = sums.get("taxes")
    local = sums.get("local_fees")
    tax_fee = (taxes or Decimal("0")) + (local or Decimal("0"))

    if service_type == "electric":
        energy = sums.get("variable_energy_charges")
        adjustments = sums.get("credits_and_adjustments")
        return {
            "total_electric_charges": _q_money(base),
            "kwh_used": (str(usage) if usage is not None else None),
            "true_effective_rate_per_kwh": _q_rate(base, usage),
            "energy_only_rate_per_kwh": _q_rate(energy, usage),
            "fixed_cost_percentage": _q_pct(fixed, base),
            "variable_cost_percentage": _q_pct(energy, base),
            "tax_fee_percentage": _q_pct(tax_fee if (taxes or local) else None, base),
            "adjustment_percentage": _q_pct(adjustments, base),
        }

    # gas
    commodity = sums.get("purchased_gas_charges")
    usage_charge = sums.get("variable_usage_charges")
    return {
        "total_gas_charges": _q_money(base),
        "ccf_used": (str(usage) if usage is not None else None),
        "true_effective_rate_per_ccf": _q_rate(base, usage),
        "purchased_gas_rate_per_ccf": _q_rate(commodity, usage),
        "fixed_cost_percentage": _q_pct(fixed, base),
        "commodity_cost_percentage": _q_pct(commodity, base),
        "tax_fee_percentage": _q_pct(tax_fee if (taxes or local) else None, base),
    }


def build_breakdown(bill: Bill) -> RateBreakdown:
    """Deterministic rate & fee breakdown for ``bill`` (electric and/or gas)."""
    electric = None
    if bill.electric_service is not None:
        electric = _build_service(
            service_type="electric",
            line_items=_service_line_items(bill, "electric"),
            category_order=ELECTRIC_CATEGORY_ORDER,
            label_map=_ELECTRIC_LABEL_MAP,
            total_current_charges=bill.electric_service.total_current_charges,
            usage=bill.electric_service.kwh_used,
        )

    gas = None
    if bill.gas_service is not None:
        gas = _build_service(
            service_type="gas",
            line_items=_service_line_items(bill, "gas"),
            category_order=GAS_CATEGORY_ORDER,
            label_map=_GAS_LABEL_MAP,
            total_current_charges=bill.gas_service.total_current_charges,
            usage=bill.gas_service.ccf_used,
        )

    return RateBreakdown(
        bill_id=bill.id,
        breakdown_version=RATE_BREAKDOWN_VERSION,
        electric=electric,
        gas=gas,
        generated_at=datetime.now(timezone.utc),
    )


def breakdown_dict(bill: Bill) -> dict:
    """JSON-serializable breakdown for an API response."""
    return build_breakdown(bill).model_dump(mode="json")


def trends(bills: Iterable[Bill]) -> list[dict]:
    """Per-bill effective-rate trend points, chronological (caller supplies order).

    Each point carries the effective and raw rates plus headline category percentages, so
    the frontend can chart "true effective rate vs energy-only rate" over time.
    """
    points: list[dict] = []
    for bill in bills:
        b = build_breakdown(bill)
        e = b.electric.metrics if b.electric else {}
        g = b.gas.metrics if b.gas else {}
        points.append(
            {
                "bill_id": bill.id,
                "bill_date": bill.bill_date.isoformat() if bill.bill_date else None,
                "electric_effective_rate_per_kwh": e.get("true_effective_rate_per_kwh"),
                "electric_energy_only_rate_per_kwh": e.get("energy_only_rate_per_kwh"),
                "electric_fixed_cost_percentage": e.get("fixed_cost_percentage"),
                "electric_tax_fee_percentage": e.get("tax_fee_percentage"),
                "gas_effective_rate_per_ccf": g.get("true_effective_rate_per_ccf"),
                "gas_commodity_rate_per_ccf": g.get("purchased_gas_rate_per_ccf"),
                "gas_fixed_cost_percentage": g.get("fixed_cost_percentage"),
                "gas_tax_fee_percentage": g.get("tax_fee_percentage"),
            }
        )
    return points
