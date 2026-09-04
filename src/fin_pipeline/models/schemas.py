from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class DocumentTableSchema(BaseModel):
    tableIndex: int | None = None
    sheetName: str | None = None
    pageNumber: int | None = None
    headers: list[str] | None = None
    rowCount: int | None = None
    accuracy: float | None = None
    markdown: str | None = None


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
    updatedAt: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    referencedTickers: list[str] | None = Field(default_factory=list)
    documentSize: int | None = None
    documentType: str | None = "PDF"
    documentHash: str | None = None
    documentText: str | None = None
    documentTextLen: int | None = None
    documentTables: list[DocumentTableSchema] | None = Field(default_factory=list)
    documentTableCnt: int | None = 0
    documentStatus: str | None = "PROCESSED"
    documentStatusReason: str | None = None
    metadataSources: str | None = None
    metadataConfidence: str | None = None

    @field_validator("filingDate", mode="before")
    @classmethod
    def format_date(cls, value):
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, str) and len(value) == 10:
            return f"{value}T00:00:00Z"
        return value
