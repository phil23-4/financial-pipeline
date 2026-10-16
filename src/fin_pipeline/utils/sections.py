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


def label_sections_structured(chunks: list[dict]) -> list[dict]:
    """Label each chunk from its most recent PART or ITEM heading."""
    labeled_chunks: list[dict] = []
    current_label: str | None = None
    for chunk in chunks:
        text = chunk.get("text", "")
        if isinstance(text, str):
            for match in _STRUCTURED_HEADER.finditer(text):
                part = match.group("part")
                if part:
                    current_label = _STRUCTURED_PART_LABELS[part.upper()]
                else:
                    item = int(match.group("item"))
                    item_suffix = match.group("item_suffix")
                    current_label = (
                        "risk_factors"
                        if item == 1 and item_suffix and item_suffix.upper() == "A"
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
