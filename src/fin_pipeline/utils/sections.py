"""Section labeling for structured filings and glossy reports."""

import json
import re
from collections.abc import Callable
from typing import Any

from loguru import logger as log

_STRUCTURED_HEADER = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:"
    r"PART\s+(?P<part>IV|III|II|I)\b"
    r"|ITEM\s+(?P<item>1[0-6]|[1-9])(?P<item_suffix>[A-Z])?\b"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

_STRUCTURED_PART_LABELS = {
    "I": "business",
    "II": "mdna",
    "III": "corporate_governance",
    "IV": "other",
}

_STRUCTURED_ITEM_LABELS = {
    1: "business",
    2: "business",
    7: "mdna",
    8: "financial_statements",
    10: "corporate_governance",
    11: "corporate_governance",
    12: "corporate_governance",
    13: "corporate_governance",
    14: "corporate_governance",
}

_20F_HEADER_PATTERNS = [
    (
        re.compile(
            r"\b(?:ITEM\s*)?3\s*(?:\.\s*)?D\b"
            r"(?:(?!\bITEM\s*\d).){0,200}?\bRISK\s+FACTORS\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "risk_factors",
    ),
    (
        re.compile(
            r"\bITEM\s*3\.?\s*[A-D]?\s*"
            r"(?:(?!\bITEM\s*\d).){0,200}?"
            r"\b(?:KEY\s+INFORMATION|RISK\s+FACTORS)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "risk_factors",
    ),
    (
        re.compile(
            r"\bITEM\s*4\.?\s*"
            r"(?:(?!\bITEM\s*\d).){0,200}?"
            r"\b(?:INFORMATION\s+ON\s+THE\s+COMPANY|BUSINESS\s+OVERVIEW)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "business",
    ),
    (
        re.compile(
            r"\bITEM\s*5\.?\s*"
            r"(?:(?!\bITEM\s*\d).){0,200}?"
            r"\b(?:OPERATING\s+AND\s+FINANCIAL\s+REVIEW|OPERATING\s+RESULTS)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "mdna",
    ),
    (
        re.compile(
            r"\bITEM\s*6\.?\s*"
            r"(?:(?!\bITEM\s*\d).){0,200}?"
            r"\b(?:DIRECTORS|SENIOR\s+MANAGEMENT|EMPLOYEES)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "corporate_governance",
    ),
    (
        re.compile(
            r"\bITEM\s*8\.?\s*"
            r"(?:(?!\bITEM\s*\d).){0,200}?"
            r"\b(?:FINANCIAL\s+INFORMATION|CONSOLIDATED\s+STATEMENTS)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "financial_statements",
    ),
    (
        re.compile(
            r"\bITEM\s*15\.?\s*"
            r"(?:(?!\bITEM\s*\d).){0,200}?\bCONTROLS\s+AND\s+PROCEDURES\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "corporate_governance",
    ),
]

_40F_HEADER_PATTERNS = [
    (re.compile(r"\bANNUAL\s+INFORMATION\s+FORM\b", re.IGNORECASE), "business"),
    (
        re.compile(
            r"\b(?:MANAGEMENT'?S\s+DISCUSSION\s+AND\s+ANALYSIS|MD&A)\b",
            re.IGNORECASE,
        ),
        "mdna",
    ),
    (
        re.compile(
            r"\b(?:AUDITED\s+)?(?:CONSOLIDATED\s+)?FINANCIAL\s+STATEMENTS\b",
            re.IGNORECASE,
        ),
        "financial_statements",
    ),
    (re.compile(r"\bRISK\s+FACTORS\b", re.IGNORECASE), "risk_factors"),
]

_GLOSSY_LABELS = {
    "ceo_letter",
    "strategy",
    "business_review",
    "innovation",
    "sustainability",
    "governance",
    "financial_review",
    "other",
}


def _structured_form_type(chunks: list[dict], form_type: str | None) -> str:
    if form_type is not None:
        normalized_form_type = re.sub(r"\s+", "", form_type.upper())
        if normalized_form_type in {"20-F", "40-F"}:
            return normalized_form_type
        return "10-K"

    text = "\n".join(
        chunk.get("text", "")
        for chunk in chunks
        if isinstance(chunk.get("text", ""), str)
    )[:5000]
    if re.search(r"\bFORM\s*20-F\b", text, re.IGNORECASE) or (
        re.search(
            r"\bANNUAL\s+REPORT\s+PURSUANT\s+TO\s+SECTION\s+13\b",
            text,
            re.IGNORECASE,
        )
        and re.search(r"\b20-F\b", text, re.IGNORECASE)
    ):
        return "20-F"
    if re.search(r"\bFORM\s*40-F\b", text, re.IGNORECASE):
        return "40-F"
    return "10-K"


def _apply_heading_patterns(
    text: str,
    current_label: str | None,
    patterns: list[tuple[re.Pattern[str], str]],
) -> str | None:
    matches_by_start: dict[int, tuple[int, str]] = {}
    for priority, (pattern, label) in enumerate(patterns):
        for match in pattern.finditer(text):
            existing = matches_by_start.get(match.start())
            if existing is None or priority < existing[0]:
                matches_by_start[match.start()] = (priority, label)

    for start in sorted(matches_by_start):
        _, label = matches_by_start[start]
        current_label = label
    return current_label


def label_sections_structured(
    chunks: list[dict],
    form_type: str | None = None,
) -> list[dict]:
    """Label chunks from the filing's structured section headings."""
    selected_form = _structured_form_type(chunks, form_type)
    labeled_chunks: list[dict] = []
    current_label: str | None = None
    for chunk in chunks:
        text = chunk.get("text", "")
        if isinstance(text, str):
            if selected_form == "20-F":
                current_label = _apply_heading_patterns(
                    text, current_label, _20F_HEADER_PATTERNS
                )
            elif selected_form == "40-F":
                current_label = _apply_heading_patterns(
                    text, current_label, _40F_HEADER_PATTERNS
                )
            else:
                for match in _STRUCTURED_HEADER.finditer(text):
                    part = match.group("part")
                    if part:
                        current_label = _STRUCTURED_PART_LABELS[part.upper()]
                    else:
                        item = int(match.group("item"))
                        item_suffix = match.group("item_suffix")
                        current_label = (
                            "risk_factors"
                            if item == 1
                            and item_suffix
                            and item_suffix.upper() == "A"
                            else _STRUCTURED_ITEM_LABELS.get(item, "other")
                        )
        labeled_chunks.append({**chunk, "sectionLabel": current_label})
    return labeled_chunks


def label_sections_glossy(
    chunks: list[dict], llm_callable: Callable[[str], str]
) -> list[dict]:
    """Apply LLM-provided section ranges to glossy report chunks."""
    unlabeled_chunks = [{**chunk, "sectionLabel": None} for chunk in chunks]
    preview = "\n\n".join(
        f"Chunk {index}: {str(chunk.get('text', ''))[:500]}"
        for index, chunk in enumerate(chunks)
    )
    prompt = (
        "Classify these document chunks into section ranges. Return only valid JSON "
        'with the shape {"sections":[{"startChunk":int,"endChunk":int,"label":str}]}. '
        "Chunk indices are zero-based and endChunk is inclusive. Labels must be one "
        "of: ceo_letter, strategy, business_review, innovation, sustainability, "
        "governance, financial_review, other. Do not infer ranges outside the chunks.\n\n"
        f"{preview}"
    )
    try:
        response = llm_callable(prompt)
        parsed: Any = json.loads(response)
        sections = parsed["sections"]
        if not isinstance(sections, list):
            raise ValueError("sections must be a list")

        validated_sections: list[tuple[int, int, str]] = []
        for section in sections:
            if not isinstance(section, dict):
                raise ValueError("each section must be an object")
            start = section.get("startChunk")
            end = section.get("endChunk")
            label = section.get("label")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end < start
                or end >= len(chunks)
                or label not in _GLOSSY_LABELS
            ):
                raise ValueError("section range or label is invalid")
            validated_sections.append((start, end, label))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        log.warning(f"Unable to parse glossy report section labels: {exc}")
        return unlabeled_chunks

    for start, end, label in validated_sections:
        for chunk_index in range(start, end + 1):
            unlabeled_chunks[chunk_index]["sectionLabel"] = label
    return unlabeled_chunks
