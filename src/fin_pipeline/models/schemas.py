from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class DocumentTableSchema(BaseModel):
    tableIndex: Optional[int] = None
    sheetName: Optional[str] = None
    pageNumber: Optional[int] = None
    headers: Optional[List[str]] = None
    rowCount: Optional[int] = None
    markdown: Optional[str] = None

class ExchangeFilingModel(BaseModel):
    filingId: str
    companyTicker: str
    stockCode: str
    stockName: Optional[str] = None
    exchange: str
    filingType: str
    filingSubtype: Optional[str] = None
    filingCategory: Optional[str] = None
    title: Optional[str] = None
    filingDate: Optional[str] = None
    documentUrl: Optional[str] = None
    source: str
    updatedAt: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    referencedTickers: Optional[List[str]] = Field(default_factory=list)
    documentSize: Optional[int] = None
    documentType: Optional[str] = "PDF"
    documentHash: Optional[str] = None
    documentText: Optional[str] = None
    documentTextLen: Optional[int] = None
    documentTables: Optional[List[DocumentTableSchema]] = Field(default_factory=list)
    documentTableCnt: Optional[int] = 0
    documentStatus: Optional[str] = "PROCESSED"
    documentStatusReason: Optional[str] = None
    metadataSources: Optional[dict] = Field(default_factory=dict)
    metadataConfidence: Optional[dict] = Field(default_factory=dict)

    @field_validator("filingDate", mode="before")
    @classmethod
    def format_date(cls, value):
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, str) and len(value) == 10:
            return f"{value}T00:00:00Z"
        return value
