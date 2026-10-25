def classify_source_type(filing_type: str | None) -> str:
    """Classify a filing type as a structured filing, glossy report, or unknown."""
    if not filing_type:
        return "unknown"
    ft = filing_type.strip().upper().replace("-", "_").replace(" ", "_")

    structured_prefixes = (
        "10_K",
        "10_Q",
        "8_K",
        "20_F",
        "40_F",
        "S_1",
        "S_3",
        "S_4",
        "DEF_14A",
        "6_K",
        "11_K",
    )
    if any(ft.startswith(prefix) for prefix in structured_prefixes):
        return "structured_filing"

    glossy_types = {
        "ANNUAL_REPORT",
        "ANNUAL",
        "AR",
        "INTEGRATED_REPORT",
        "INTEGRATED",
        "SUSTAINABILITY_REPORT",
        "SUSTAINABILITY",
        "ESG_REPORT",
        "ESG",
    }
    if ft in glossy_types:
        return "glossy_report"

    return "unknown"


def normalize_filing_type(raw: str | None) -> str | None:
    """Map common filing-type aliases to canonical filingType values."""
    if not raw:
        return raw
    normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "annual": "ANNUAL_REPORT",
        "annual_report": "ANNUAL_REPORT",
        "ar": "ANNUAL_REPORT",
        "integrated": "INTEGRATED_REPORT",
        "integrated_report": "INTEGRATED_REPORT",
        "sustainability": "SUSTAINABILITY_REPORT",
        "sustainability_report": "SUSTAINABILITY_REPORT",
        "esg": "ESG_REPORT",
        "esg_report": "ESG_REPORT",
    }
    return aliases.get(normalized, raw)
