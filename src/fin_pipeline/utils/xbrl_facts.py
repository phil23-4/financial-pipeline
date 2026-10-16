"""Extraction of selected numeric facts from Inline XBRL HTML."""

import math
from typing import Any

from bs4 import BeautifulSoup, Tag

_FACT_CONCEPTS = {
    "revenues": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "capitalExpenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "researchAndDevelopmentExpense": ("ResearchAndDevelopmentExpense",),
    "intangibleAssets": (
        "IntangibleAssetsNetExcludingGoodwill",
        "FiniteLivedIntangibleAssetsNet",
    ),
    "assets": ("Assets",),
}


def _concept_name(tag: Tag) -> str:
    name = tag.get("name", "")
    if isinstance(name, list):
        name = " ".join(name)
    return str(name).rsplit(":", 1)[-1]


def _numeric_value(tag: Tag) -> float | None:
    raw_value = tag.get_text(strip=True).replace(",", "").strip()
    if not raw_value:
        return None

    negative_parentheses = raw_value.startswith("(") and raw_value.endswith(")")
    if negative_parentheses:
        raw_value = raw_value[1:-1].strip()
    try:
        value = float(raw_value)
        scale = int(str(tag.get("scale", "0")))
    except (OverflowError, TypeError, ValueError):
        return None

    sign = str(tag.get("sign", ""))
    if sign not in ("", "+", "-"):
        return None
    if sign == "-" or negative_parentheses:
        value = -abs(value)
    elif sign == "+":
        value = abs(value)

    value *= 10**scale
    return value if math.isfinite(value) else None


def _context_has_segment_axis(
    context_id: str, contexts: dict[str, Tag]
) -> bool:
    context = contexts.get(context_id)
    if context is None:
        return False
    return context.find(
        lambda tag: isinstance(tag, Tag)
        and (
            tag.name.lower().endswith("explicitmember")
            or tag.name.lower().endswith("typedmember")
        )
        and bool(tag.get("dimension"))
    ) is not None


def extract_xbrl_facts(html_content: str) -> dict[str, float | None]:
    """Extract requested numeric Inline XBRL facts without inferring missing values."""
    facts: dict[str, float | None] = {
        key: None for key in (*_FACT_CONCEPTS, "segmentRevenue")
    }
    soup = BeautifulSoup(html_content, "html.parser")
    contexts = {
        str(context.get("id")): context
        for context in soup.find_all(
            lambda tag: isinstance(tag, Tag)
            and tag.name.lower().endswith("context")
            and tag.get("id")
        )
    }
    facts_by_concept: dict[str, list[Tag]] = {}
    for tag in soup.find_all(
        lambda candidate: isinstance(candidate, Tag)
        and candidate.name.lower() == "ix:nonfraction"
    ):
        facts_by_concept.setdefault(_concept_name(tag), []).append(tag)

    for output_key, concepts in _FACT_CONCEPTS.items():
        for concept in concepts:
            matches = facts_by_concept.get(concept, [])
            if matches:
                facts[output_key] = _numeric_value(matches[0])
                break

    segment_concept = "RevenueFromContractWithCustomerExcludingAssessedTax"
    for tag in facts_by_concept.get(segment_concept, []):
        context_ref = str(tag.get("contextref", ""))
        if _context_has_segment_axis(context_ref, contexts):
            facts["segmentRevenue"] = _numeric_value(tag)
            break

    return facts
