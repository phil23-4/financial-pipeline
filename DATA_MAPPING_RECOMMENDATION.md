# Financial Pipeline Data Mapping Strategy

**Recommendation: Implement HYBRID approach with Phase 1 → Phase 2 rollout**

---

## Executive Summary

| Aspect | Recommendation |
|--------|-----------------|
| **Start with** | `filing_financials` (Document/Schemafull) |
| **Add later** | `filing_narratives` (Vector/Graph) |
| **Timeline** | Phase 1: Month 1-2, Phase 2: Month 3-4 |
| **Cost** | Phase 1: $0, Phase 2: $20-100/month |
| **ROI** | High (financial dashboards) → Very High (AI insights) |

---

## Why This Strategy Works

### ✅ filing_financials is the Right First Step

**Alignment with Current Strengths:**
- Pipeline already extracts tables perfectly
- Requires ZERO new dependencies
- Solves immediate business need: "Compare metrics across filings"
- Fast implementation (2-3 days vs. weeks)

**Immediate Value Delivery:**
```
Week 1: Schema design + prototype
Week 2: Implement table parser + classifier  
Week 3: Backfill historical data
Week 4: Deploy dashboards
```

**Use Cases Unlocked:**
- 📊 Revenue trends (AAPL: 2020-2024)
- 💰 Profitability comparison (AAPL vs MSFT vs GOOGL)
- 📈 Growth rate analysis (Q-o-Q, Y-o-Y)
- 🚨 Anomaly detection (sudden drops/spikes)

---

### ✅ filing_narratives Follows Naturally

**Why Start After Financials:**
1. **De-risk the approach** - Validate schema patterns with simpler data first
2. **Control costs** - Batch embeddings after proving ROI
3. **Build infrastructure** - Add ML pipeline when team is ready
4. **Reuse patterns** - Learn from financials table design

**Add When Ready For:**
- 🔍 Semantic search across filings
- ⚠️ Risk factor analysis
- 📋 Regulatory compliance scoring
- 🤖 LLM applications (RAG systems)

---

## Phase 1: filing_financials Implementation

### Schema Definition

```surrealdb
-- Quantitative financial data separated from narrative text
DEFINE TABLE IF NOT EXISTS filing_financials SCHEMAFULL;

DEFINE FIELD IF NOT EXISTS filing_id ON TABLE filing_financials 
  TYPE record<exchange_filing>;

DEFINE FIELD IF NOT EXISTS financial_type ON TABLE filing_financials 
  TYPE enum<income_statement|balance_sheet|cash_flow|other>;

DEFINE FIELD IF NOT EXISTS period ON TABLE filing_financials 
  TYPE string;  // "Q1 2024", "FY 2023", "Q1 2024-Q1 2023" (for comparisons)

DEFINE FIELD IF NOT EXISTS currency ON TABLE filing_financials 
  TYPE enum<USD|EUR|GBP|JPY|CNY|other>;

DEFINE FIELD IF NOT EXISTS tables ON TABLE filing_financials 
  TYPE array<object>;

DEFINE FIELD IF NOT EXISTS tables[*].lineItem ON TABLE filing_financials 
  TYPE string;

DEFINE FIELD IF NOT EXISTS tables[*].values ON TABLE filing_financials 
  TYPE object;  // {q1_2024: 91864000000, q1_2023: 83036000000, ...}

DEFINE FIELD IF NOT EXISTS extracted_at ON TABLE filing_financials 
  TYPE datetime;

DEFINE FIELD IF NOT EXISTS extraction_confidence ON TABLE filing_financials 
  TYPE float;  // 0.0-1.0, accuracy of extraction

DEFINE FIELD IF NOT EXISTS source_table_index ON TABLE filing_financials 
  TYPE option<int>;  // Reference back to original documentTables

DEFINE INDEX IF NOT EXISTS idx_ff_filing ON TABLE filing_financials 
  COLUMNS filing_id;

DEFINE INDEX IF NOT EXISTS idx_ff_type ON TABLE filing_financials 
  COLUMNS financial_type;

DEFINE INDEX IF NOT EXISTS idx_ff_period ON TABLE filing_financials 
  COLUMNS period;

DEFINE INDEX IF NOT EXISTS idx_ff_ticker ON TABLE filing_financials 
  COLUMNS filing_id, financial_type, period;
```

### Data Transformation Flow

```python
# 1. Parse documentTables from exchange_filing
def classify_financial_table(table: DocumentTableSchema) -> dict:
    """Classify extracted table as Income/Balance/Cash Flow."""
    keywords = {
        'income_statement': ['revenue', 'gross profit', 'operating income', 'net income'],
        'balance_sheet': ['assets', 'liabilities', 'equity', 'stockholders equity'],
        'cash_flow': ['operating activities', 'investing activities', 'financing activities']
    }
    
    table_text = ' '.join(table.headers + table.markdown).lower()
    
    for financial_type, keywords_list in keywords.items():
        if any(kw in table_text for kw in keywords_list):
            return {'financial_type': financial_type, 'confidence': 0.95}
    
    return {'financial_type': 'other', 'confidence': 0.5}

# 2. Transform markdown to structured JSON
def parse_financial_table(markdown: str) -> list[dict]:
    """Convert markdown table to array of objects."""
    lines = markdown.split('\n')
    headers = [h.strip() for h in lines[0].split('|')[1:-1]]
    
    rows = []
    for line in lines[2:]:
        if '|' in line:
            values = [v.strip() for v in line.split('|')[1:-1]]
            row = {'lineItem': values[0]}
            for i, header in enumerate(headers[1:], 1):
                try:
                    row[header.lower()] = float(values[i].replace(',', ''))
                except (ValueError, IndexError):
                    row[header.lower()] = values[i]
            rows.append(row)
    
    return rows

# 3. Insert into filing_financials
async def store_financial_data(db: Surreal, filing_id: str, document_tables: list):
    """Extract and store financial data from tables."""
    for table in document_tables:
        classification = classify_financial_table(table)
        parsed_rows = parse_financial_table(table.markdown)
        
        financial_doc = {
            'filing_id': filing_id,
            'financial_type': classification['financial_type'],
            'period': extract_period_from_table(table),  # Q1 2024, etc
            'currency': extract_currency_from_filing(filing_id),
            'tables': parsed_rows,
            'extracted_at': datetime.now(timezone.utc),
            'extraction_confidence': classification['confidence'],
            'source_table_index': table.tableIndex
        }
        
        await db.upsert(f'filing_financials:{filing_id}:{table.tableIndex}', financial_doc)
```

### Example Query: Multi-Year Revenue Comparison

```surrealdb
-- Find revenue trends for three companies
SELECT 
  filing_id.companyTicker as ticker,
  period,
  tables[0].values as financials
FROM filing_financials
WHERE financial_type = 'income_statement'
  AND filing_id.companyTicker IN ['AAPL', 'MSFT', 'GOOGL']
ORDER BY filing_id.companyTicker, period DESC
LIMIT 50;

-- Output:
-- {
--   "ticker": "AAPL",
--   "period": "Q1 2024",
--   "financials": {
--     "revenue": 91864000000,
--     "gross_profit": 45563000000,
--     "operating_income": 28674000000
--   }
-- }
```

---

## Phase 2: filing_narratives Implementation

### Schema Definition

```surrealdb
-- Qualitative narrative text with semantic embeddings
DEFINE TABLE IF NOT EXISTS filing_narratives SCHEMAFULL;

DEFINE FIELD IF NOT EXISTS narrative_id ON TABLE filing_narratives 
  TYPE string;  // Auto-generated: narrative:sha256(filing_id+chunk)

DEFINE FIELD IF NOT EXISTS filing_id ON TABLE filing_narratives 
  TYPE record<exchange_filing>;

DEFINE FIELD IF NOT EXISTS narrative_type ON TABLE filing_narratives 
  TYPE enum<md&a|risk_factors|business_overview|accounting_policies|discussion|other>;

DEFINE FIELD IF NOT EXISTS section_title ON TABLE filing_narratives 
  TYPE string;  // "MD&A - Market Risks", "Risk Factors - Geopolitical"

DEFINE FIELD IF NOT EXISTS chunk_index ON TABLE filing_narratives 
  TYPE int;  // Paragraph index within section

DEFINE FIELD IF NOT EXISTS text ON TABLE filing_narratives 
  TYPE string;  // 300-500 tokens per chunk

DEFINE FIELD IF NOT EXISTS token_count ON TABLE filing_narratives 
  TYPE int;  // For cost tracking

DEFINE FIELD IF NOT EXISTS embedding ON TABLE filing_narratives 
  TYPE vector<1536>;  // OpenAI text-embedding-3-small

DEFINE FIELD IF NOT EXISTS embedding_model ON TABLE filing_narratives 
  TYPE string;  // "text-embedding-3-small", versioning

DEFINE FIELD IF NOT EXISTS embedding_timestamp ON TABLE filing_narratives 
  TYPE datetime;

DEFINE INDEX IF NOT EXISTS idx_fn_filing ON TABLE filing_narratives 
  COLUMNS filing_id;

DEFINE INDEX IF NOT EXISTS idx_fn_type ON TABLE filing_narratives 
  COLUMNS narrative_type;

DEFINE INDEX IF NOT EXISTS idx_fn_vector ON TABLE filing_narratives 
  COLUMNS embedding MTREE;  // Vector index for similarity search

-- Graph edge: narratives → filing
DEFINE TABLE IF NOT EXISTS narrative_belongs_to_filing SCHEMAFULL 
  TYPE RELATION IN filing_narratives OUT exchange_filing;

DEFINE INDEX IF NOT EXISTS idx_nbf_unique ON TABLE narrative_belongs_to_filing 
  COLUMNS in, out UNIQUE;
```

### Text Chunking Strategy

```python
import tiktoken
from typing import Generator

def chunk_filing_text(
    text: str, 
    max_tokens: int = 500,
    overlap_tokens: int = 100,
    section_title: str = "General"
) -> Generator[dict, None, None]:
    """Split text into overlapping chunks for embedding."""
    
    encoder = tiktoken.encoding_for_model("gpt-3.5-turbo")
    tokens = encoder.encode(text)
    
    chunk_idx = 0
    start_idx = 0
    
    while start_idx < len(tokens):
        # Define chunk boundaries
        end_idx = min(start_idx + max_tokens, len(tokens))
        
        # Decode tokens back to text
        chunk_tokens = tokens[start_idx:end_idx]
        chunk_text = encoder.decode(chunk_tokens)
        
        yield {
            'section_title': section_title,
            'chunk_index': chunk_idx,
            'text': chunk_text.strip(),
            'token_count': len(chunk_tokens),
            'start_token': start_idx,
            'end_token': end_idx
        }
        
        # Move to next chunk with overlap
        start_idx = end_idx - overlap_tokens
        chunk_idx += 1
```

### Embedding Pipeline

```python
import openai
import asyncio

async def embed_narrative_chunks(
    db: Surreal,
    filing_id: str,
    chunks: list[dict],
    batch_size: int = 20
):
    """Generate embeddings for text chunks using OpenAI API."""
    
    model = "text-embedding-3-small"
    
    for batch in chunks_by_size(chunks, batch_size):
        texts = [c['text'] for c in batch]
        
        # Call OpenAI API
        response = await openai.Embedding.create(
            input=texts,
            model=model,
            dimensions=1536
        )
        
        # Store in SurrealDB
        for chunk, embedding_data in zip(batch, response['data']):
            narrative_id = f"narrative:{sha256(filing_id + str(chunk['chunk_index'])).hexdigest()}"
            
            doc = {
                'narrative_id': narrative_id,
                'filing_id': filing_id,
                'narrative_type': 'risk_factors',  # or other types
                'section_title': chunk['section_title'],
                'chunk_index': chunk['chunk_index'],
                'text': chunk['text'],
                'token_count': chunk['token_count'],
                'embedding': embedding_data['embedding'],
                'embedding_model': model,
                'embedding_timestamp': datetime.now(timezone.utc)
            }
            
            await db.upsert(narrative_id, doc)
```

### Example Query: Semantic Risk Search

```surrealdb
-- Find similar risk discussions across all filings
-- (Would need embedding for query: "currency exchange rate risk")
LET $query_embedding = [...];  -- OpenAI embedding of query

SELECT 
  filing_id.companyTicker as ticker,
  section_title,
  text,
  vector::similarity(embedding, $query_embedding) as relevance_score
FROM filing_narratives
WHERE narrative_type = 'risk_factors'
  AND vector::similarity(embedding, $query_embedding) > 0.78
ORDER BY relevance_score DESC
FETCH filing_id
LIMIT 20;

-- Output: Find all companies discussing foreign exchange risks
```

---

## Implementation Roadmap

### Week 1-2: filing_financials Foundation
- [ ] Design schema (SQLs)
- [ ] Implement table classifier
- [ ] Create markdown → JSON parser
- [ ] Unit test parsers

### Week 3: filing_financials Integration
- [ ] Integrate into pipeline
- [ ] Backfill 100 test filings
- [ ] Verify data quality
- [ ] Performance testing

### Week 4: filing_narratives Preparation
- [ ] Design schema
- [ ] Implement text chunker
- [ ] Set up OpenAI API integration
- [ ] Plan embedding strategy

### Week 5-6: filing_narratives Pipeline
- [ ] Implement embedding generator
- [ ] Create batch processing
- [ ] Set up cost monitoring
- [ ] Test vector search queries

### Week 7: Deployment & Optimization
- [ ] Full backfill of narratives
- [ ] Query optimization
- [ ] Build sample dashboards
- [ ] Document APIs

---

## Cost-Benefit Analysis

### filing_financials ROI
| Metric | Value |
|--------|-------|
| **Implementation cost** | 2-3 engineer days |
| **Monthly cost** | $0 |
| **Benefits** | Financial dashboards, anomaly detection, trend analysis |
| **Data volume** | ~50 tables per filing × 10K filings = 500K documents |
| **Storage** | ~500MB |

### filing_narratives ROI (Phase 2)
| Metric | Value |
|--------|-------|
| **Implementation cost** | 2 weeks engineer time + API costs |
| **Monthly cost** | $20-100 (1-5M embedding tokens) |
| **Benefits** | Risk scoring, compliance, semantic search, RAG applications |
| **Data volume** | ~100 chunks per filing × 10K filings = 1M documents |
| **Storage** | ~5GB (vectors are dense) |

**Breakeven:** Compliance/risk scoring dashboard pays for itself in month 1-2

---

## Recommendation: Start with filing_financials

✅ **Why:**
1. Zero technical debt (no ML dependencies)
2. Immediate ROI (financial dashboards in 2-3 weeks)
3. Validates data pipeline
4. Builds team confidence
5. Can scale narratives independently later

❌ **Don't start with filing_narratives because:**
1. High infrastructure overhead
2. Requires budget approval (OpenAI API)
3. Complex error handling (ML systems are fragile)
4. Need to learn vector search optimization
5. Can still run business without semantic search

✅ **Do add filing_narratives when:**
1. filing_financials is stable (month 2)
2. Business case for risk scoring is clear
3. ML infrastructure budget approved
4. Team has learned from first phase

---

## Success Metrics

### Phase 1: filing_financials
- [ ] 100% of numerical tables extracted
- [ ] Query latency < 100ms for revenue comparisons
- [ ] Data accuracy verified against source PDFs
- [ ] Dashboard shows 3+ years of trends

### Phase 2: filing_narratives
- [ ] Vector similarity scores match manual relevance ratings
- [ ] Risk detection dashboard identifies top 10 risks
- [ ] Regulatory compliance scoring automated
- [ ] LLM RAG queries return relevant context

---

## Conclusion

**Recommendation: HYBRID APPROACH**
- **Month 1-2:** Build `filing_financials` (quick win, zero cost)
- **Month 3-4:** Add `filing_narratives` (sustained value, modest cost)
- **Month 5+:** Combine both for AI-driven financial insights

This strategy de-risks implementation, proves ROI early, and sets up for intelligent systems later.

