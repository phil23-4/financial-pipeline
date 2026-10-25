from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DocumentTableSchema(BaseModel):
    tableIndex: int | None = None
    sheetName: str | None = None
    pageNumber: int | None = None
    headers: list[str] | None = None
    rows: list[list[str]] | None = None
    rowCount: int | None = None
    accuracy: float | None = None
    markdown: str | None = None


class DocumentChunkSchema(BaseModel):
    chunkIndex: int
    pageNumber: int | None = None
    sectionLabel: str | None = None
    text: str
    charStart: int | None = None
    charEnd: int | None = None


class ExchangeFilingModel(BaseModel):
    filingId: str
    companyTicker: str
    stockCode: str
    stockName: str | None = None
    exchange: str
    filingType: str
    filingSubtype: str | None = None
    filingCategory: str | None = None
    title: str | None = None
    filingDate: str | None = None
    documentUrl: str | None = None
    source: str
    sourceType: Literal["structured_filing", "glossy_report", "unknown"] = "unknown"
    reportingYear: int | None = None
    # Metadata extraction fields
    periodEndDate: str | None = None
    fiscalYearEndMonth: int | None = None
    isAmendment: bool = False
    language: str | None = None
    pageCount: int | None = None
    industryCodeRaw: str | None = None
    gicsCode: str | None = None
    gicsSector: str | None = None
    industryConfidence: float | None = None
    # XBRL provenance fields
    xbrlCurrency: str | None = None
    xbrlProvenance: str | None = None
    xbrlSource: str | None = None
    updatedAt: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    referencedTickers: list[str] | None = Field(default_factory=list)
    documentSize: int | None = None
    documentType: str | None = "PDF"
    documentHash: str | None = None
    documentText: str | None = None
    documentTextLen: int | None = None
    documentChunks: str | None = None
    documentTables: str | None = None
    documentTableCnt: int | None = 0
    documentStatus: str | None = "PROCESSED"
    documentStatusReason: str | None = None
    xbrlFacts: str | None = None
    metadataSources: str | None = None
    metadataConfidence: str | None = None
    exhibitSectionMap: str | None = None

    @field_validator("filingDate", mode="before")
    @classmethod
    def format_date(cls, value):
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, str) and len(value) == 10:
            return f"{value}T00:00:00Z"
        return value

    @field_validator("periodEndDate", mode="before")
    @classmethod
    def format_period_end_date(cls, value):
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, str) and len(value) == 10:
            return f"{value}T00:00:00Z"
        return value
