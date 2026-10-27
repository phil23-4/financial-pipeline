# Debugging guide

This guide is for operators who have hit a `fin_pipeline` failure and do not
have an obvious path forward. It assumes the reader has run a batch and has a
log line or an observed behavior in hand. Every symptom is paired with the
exact error text that appears in the log, a first command to run, and numbered
steps that move from the cheapest check to the fix.

The two log sinks are the JSON metric file and the SurrealDB server log. The
pipeline writes one JSON object per line to `logs/pipeline_metrics.json`
(Verified from src/fin_pipeline/config/logger.py:56-65). SurrealDB writes its
own log to `logs/surrealdb.log.*` when started with
`SURREAL_LOG_FILE_ENABLED=true` (Verified from README.md:53-56). Both are
authoritative; trust them over a recollection of what the command "should"
have done.

## 1. Debugging principles

1. **Trust the logs.** The pipeline records the filing ID, the stage that
   failed, and the underlying error in a single structured line. The error
   prefixes are stable string constants in the source, so a grep for the exact
   text returns the failing stage. For example, the commit-failure line begins
   `Database adapter failed to securely commit transaction records for id:`
   (Verified from src/fin_pipeline/pipeline.py:503-505). Read the whole line
   before acting; the trailing `Err:` clause is the actual cause, and the
   prefix only tells you the stage that reported it.

2. **Check the simplest cause first.** Most failures are environmental:
   SurrealDB is not running, the HTTP body limits were not raised, the
   credentials are wrong, or the disk is full. These are cheap to check and
   common in practice. Do not start by re-running the batch or editing code;
   run the diagnostic commands in section 2 first, in the order they are
   listed.

3. **Preserve state.** Do not delete `logs/`, the `xbrl_bulk/` archives, or
   the SurrealDB store while diagnosing. The `.done` checkpoints in
   `xbrl_bulk/` make downloads idempotent (Verified from
   docs/DESIGN_RATIONALE.md:437-438), and the `surrealkv` store under
   `data/analytics_data` is the record of what has been ingested. Copy or
   back up the failing input file before touching it, and do not run a second
   batch against the same store until the cause is understood, because a retry
   can overwrite the record that contains the evidence.

4. **Reproduce with one filing.** Batch commands stop or skip records, which
   obscures the failing document. Reproduce the failure with the single-file
   command against the exact input and metadata that failed, so the same error
   appears in isolation without the concurrency of a batch run.

5. **Document before retrying.** Write down the symptom, the failing filing ID,
   and the commands tried before re-running. The write path is delete-then-upsert
   (Verified from src/fin_pipeline/pipeline.py:480-481), so a retry overwrites
   the record and can destroy the evidence of a transient state. Record the
   error text first.

## 2. Diagnostic commands reference

Run these from the repository root. Replace `<FILING_ID>` with the value from
the failing log line.

```bash
# Check process alive — list any running SurrealDB server process
pgrep -af surreal || ps aux | grep -i surreal

# Check SurrealDB health — confirm the HTTP endpoint answers 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health

# Check disk space — remaining space on the volume and size of the two stores
df -h .
du -sh xbrl_bulk data/analytics_data

# Check log tail — last pipeline log lines and last SurrealDB server log lines
tail -n 50 logs/pipeline_metrics.json
tail -n 50 logs/surrealdb.log.*

# Check DB record — fetch one filing record by its filingId field over /sql
curl -s -X POST http://127.0.0.1:8000/sql \
  -H 'Content-Type: text/plain' -H 'Accept: application/json' \
  -u 'root:secret' -H 'Surreal-NS: finance' -H 'Surreal-DB: analytics' \
  --data "SELECT * FROM exchange_filing WHERE filingId = '<FILING_ID>';"

# Check recent errors — most recent ERROR/CRITICAL lines from the metric log
grep -oE '"level": "(ERROR|CRITICAL)"[^}]*' logs/pipeline_metrics.json | tail -n 20
```

The `/sql` request above mirrors the headers the pipeline sends: Basic
authentication plus the `Surreal-NS` and `Surreal-DB` headers (Verified from
src/fin_pipeline/db/db.py:56-68). The default endpoint is
`http://127.0.0.1:8000` (Verified from src/fin_pipeline/config/settings.py:53).
If `.env` overrides the username, password, namespace, or database, substitute
those values for `root`, `secret`, `finance`, and `analytics`.

Two additional checks are worth running when a diagnostic command prints
nothing:

```bash
# Check SurrealDB version — confirm the binary and its version banner
surreal version

# Check log level — confirm the pipeline log level, default INFO
env | grep FIN_PIPELINE_LOG_LEVEL
```

`FIN_PIPELINE_LOG_LEVEL` controls the stream and file log level and defaults
to `INFO` (Verified from src/fin_pipeline/config/logger.py:8). Debug-level
lines, such as the per-worker error detail emitted after a batch error summary
(Verified from src/fin_pipeline/pipeline.py:714-715), are suppressed unless the
level is set to `DEBUG`. Raise it temporarily with
`FIN_PIPELINE_LOG_LEVEL=DEBUG` when a batch summary reports errors but the
individual cause is not visible.

## 3. Symptom to cause to fix playbook

### Symptom: `Err: HTTP 413: length limit exceeded`

First check:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
```

1. Confirm SurrealDB is reachable (expect `200`).
2. Confirm the server was started with the raised limits. SurrealDB 3.x
   defaults to 4 MiB on `/rpc` and 1 MiB on `/sql`, which large filings exceed
   (Verified from README.md:46-50). The in-process write path sends payloads
   whose serialized statement exceeds `_SQL_UPSERT_LIMIT_BYTES = 900_000`
   through `/rpc`, but a payload larger than 4 MiB still fails with HTTP 413
   if the server limit was not raised (Verified from
   src/fin_pipeline/db/connection.py:10, 117-126).
3. Stop the server and restart it with the two environment variables set on
   the server process itself:

   ```bash
   SURREAL_HTTP_MAX_RPC_BODY_SIZE=128MiB \
   SURREAL_HTTP_MAX_SQL_BODY_SIZE=16MiB \
   SURREAL_LOG_FILE_ENABLED=true \
   surreal start surrealkv:data/analytics_data --log info
   ```

   (Verified from README.md:52-57.)
4. Re-run the failing filing with `--force` (for `file`) or without skip flags,
   since the earlier attempt left no committed record.

### Symptom: `Err: <urlopen error [Errno 61] Connection refused>`

First check:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health
```

1. If the health check does not print `200`, SurrealDB is not running or is
   listening on a different endpoint.
2. Check whether any server process is alive with `pgrep -af surreal`.
3. Start SurrealDB with the command in the preceding entry. The pipeline
   attempts the request up to three times with exponential backoff
   (Verified from src/fin_pipeline/db/db.py:45-50); a persistent refusal
   surfaces as `Max retries (3) exceeded for surreal_query:` followed by the
   same `Connection refused` text (Verified from src/fin_pipeline/utils/retry.py:47-50).
4. If SurrealDB is running elsewhere, set `SURREAL_ENDPOINT` to the correct
   host and port (Verified from src/fin_pipeline/config/settings.py:53).

### Symptom: `Err: <urlopen error [Errno 32] Broken pipe>`

First check:

```bash
tail -n 50 logs/surrealdb.log.*
```

1. A broken pipe means the server closed the connection mid-request, typically
   because the server process died or restarted during a write.
2. Read the SurrealDB server log for a crash, an out-of-memory termination, or
   a restart marker.
3. Confirm the server is now up with the health command in section 2.
4. Re-ingest the filing. The write path deletes the stale record before
   upserting (Verified from src/fin_pipeline/pipeline.py:480-481), so a retry
   is safe and idempotent.

### Symptom: `Err: There was a problem with authentication`

First check:

```bash
env | grep SURREAL_USER && env | grep SURREAL_PASS
```

1. Confirm the environment (or `.env`) supplies the values the SurrealDB
   server was started with. The pipeline reads `SURREAL_USER` (default `root`)
   and `SURREAL_PASS` (default `secret`) (Verified from
   src/fin_pipeline/config/settings.py:54-55).
2. Confirm OS environment values have precedence: `get_env` gives the OS
   environment priority over `.env` (Verified from
   src/fin_pipeline/config/settings.py:48-50), so a stale exported variable
   overrides the file.
3. Correct the mismatch and re-run. No database changes are needed.

### Symptom: `Err: The table 'exchange_filing' does not exist` (or `The table 'company' does not exist`, or `The namespace 'default' does not exist`)

First check:

```bash
curl -s -X POST http://127.0.0.1:8000/sql \
  -H 'Content-Type: text/plain' -H 'Accept: application/json' \
  -u 'root:secret' -H 'Surreal-NS: finance' -H 'Surreal-DB: analytics' \
  --data "INFO FOR TABLE exchange_filing;" | head -c 500
```

1. If the namespace error names `default`, the pipeline is signing into the
   wrong namespace. Verify `SURREAL_NAMESPACE` (default `finance`) and
   `SURREAL_DATABASE` (default `analytics`) (Verified from
   src/fin_pipeline/config/settings.py:56-57).
2. If the table is missing in the correct namespace, the schema has not been
   initialized. Schema creation runs once, on the first write, inside
   `SurrealConnection.__aenter__` (Verified from
   src/fin_pipeline/db/connection.py:179-184).
3. Trigger initialization by running a real (non-dry-run) single-file ingest.
   A `--dry-run` does not open the database and therefore does not initialize
   the schema (Verified from src/fin_pipeline/pipeline.py:469-473).

### Symptom: `Err: Found field 'metadataConfidence', but no such field exists for table 'exchange_filing'`

First check:

```bash
grep -oE '"level": "ERROR"[^}]*' logs/pipeline_metrics.json | tail -n 5
```

1. This indicates the live schema predates the metadata provenance fields.
   `initialize_schema` runs idempotent migrations that remove and re-declare
   `metadataSources` and `metadataConfidence` as `option<string>`
   (Verified from src/fin_pipeline/db/db.py:370-384).
2. Trigger a real single-file ingest (not `--dry-run`) so schema
   initialization and its migrations execute before the write.
3. Re-run the failing filing. The schema is then current and the field is
   accepted.

### Symptom: `Err: Couldn't coerce value for field 'documentChunks.*.text' of 'exchange_filing:<id>'` (or `documentTables.*.headers`)

First check:

```bash
grep -oE '"level": "ERROR"[^}]*' logs/pipeline_metrics.json | grep "Couldn't coerce" | tail -n 5
```

1. This error means a nested field was written as a native SurrealDB object or
   array whose text contains a `word:word` pattern that SurrealDB attempts to
   coerce into a RecordID reference (Verified from
   docs/DESIGN_RATIONALE.md:653-667).
2. Confirm the live schema declares these fields as `option<string>`:
   `documentChunks` and `documentTables` are both `option<string>` in the DDL
   (Verified from src/fin_pipeline/db/db.py:236, 254).
3. Confirm the write path serializes them to JSON strings before the upsert:
   `_HttpSurrealConnection.upsert` calls `_serialize_metadata_map` on
   `documentChunks`, `documentTables`, and `xbrlFacts` (Verified from
   src/fin_pipeline/db/connection.py:101-111).
4. Re-ingest the filing after a fresh schema initialization (see the previous
   entry). Records written under an older `option<object>` schema must be
   re-ingested to be re-encoded.

### Symptom: `EDGAR_IDENTITY is required for SEC downloads` (a `RuntimeError`)

First check:

```bash
env | grep EDGAR_IDENTITY
```

1. SEC download paths require a User-Agent identity. `_stream_filings_from_csv`
   raises `RuntimeError` when neither the OS environment variable
   `EDGAR_IDENTITY` nor the `.env` value is set (Verified from
   src/fin_pipeline/sec_edgar.py:289-293).
2. Set it, for example `EDGAR_IDENTITY="Your Name your.email@example.com"`,
   and re-run. This does not affect the database.

### Symptom: `Skipping AAPL: filings lookup failed after retries (SecEdgarFetchError: ... SEC lookup timed out); continuing`

First check:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany
```

1. This is a transient SEC network failure. The stream path retries company
   lookups three times, then skips the company and continues with the next CSV
   row (Verified from src/fin_pipeline/sec_edgar.py:54-70, 340-345).
2. Confirm outbound HTTPS to SEC is reachable and that `EDGAR_IDENTITY` is set.
3. Re-run the batch with `--skip-existing` so already-ingested rows are not
   re-fetched (Verified from src/fin_pipeline/cli.py:210).

### Symptom: `Skipping <accession>: html retrieval failed (SecEdgarFetchError: ... after 3 attempts: ... handshake operation timed out)`

First check:

```bash
env | grep EDGAR_IDENTITY
```

1. A TLS handshake timeout is an outbound network or proxy problem, not a
   code problem. Confirm the host can reach SEC over HTTPS and that no proxy
   or firewall is dropping the connection.
2. Confirm `EDGAR_IDENTITY` is set; SEC enforces a declared User-Agent.
3. The filing is skipped and the batch continues (Verified from
   src/fin_pipeline/sec_edgar.py:375-380). Re-run the batch; resume flags
   avoid re-fetching completed rows.

### Symptom: `Skipping <accession>: html retrieval failed (MarkupError: closing tag ...)`

First check:

```bash
grep -oE '"level": "ERROR"[^}]*' logs/pipeline_metrics.json | grep MarkupError | tail -n 5
```

1. This is malformed HTML returned by SEC or mis-parsed by the retrieval
   library. The filing is skipped and the batch continues (Verified from
   src/fin_pipeline/sec_edgar.py:375-380).
2. Re-run once; if the same accession fails identically, note the accession
   and confirm the primary document is malformed by fetching it directly.
3. Exclude the accession from the CSV or file an issue with the accession
   number. This failure does not affect other filings.

### Symptom: `File missing on target file execution block path: /nonexistent.pdf`

First check:

```bash
ls -la /path/to/input.pdf
```

1. The input path no longer exists or was moved. The pipeline records
   `documentStatus = "FAILED"` with reason `Binary file missing from local
   path` and returns without writing the record (Verified from
   src/fin_pipeline/pipeline.py:350-357).
2. Restore the file to its original absolute path, or correct the path.
3. Re-run the single-file ingest with the corrected path.

### Symptom: `Corrupt download for 2020q3: Download failed after 3 attempts` followed by `Skipping; re-run to retry`

First check:

```bash
df -h . && du -sh xbrl_bulk
```

1. A quarterly FSDS archive failed to download cleanly and was skipped. The
   download helper logs the corrupt archive and advises re-running
   (Verified from docs/_FACTS.md:449-450, citing src/fin_pipeline/utils/xbrl_bulk.py:490-491).
2. Confirm sufficient disk space for the archives; the bulk working directory
   grows by roughly 4.6 GB in an observed run (Verified from
   docs/OPERATIONAL_REFERENCE.md:36-40).
3. Re-run `fin-pipeline xbrl-bulk`. Completed archives carry `.done`
   checkpoints and are reused, so only the missing quarter is re-fetched
   (Verified from docs/DESIGN_RATIONALE.md:437-438).

### Symptom: `Stopping SEC filing batch because the current record was not fully committed.`

First check:

```bash
grep -oE '"level": "ERROR"[^}]*' logs/pipeline_metrics.json | tail -n 10
```

1. A filing failed to commit and the sequential SEC directory batch aborted
   (Verified from src/fin_pipeline/pipeline.py:802-806).
2. Read the immediately preceding error line, which names the filing and the
   underlying cause. Treat that entry first.
3. Fix the cause, then re-run with `--skip-existing` so committed filings are
   skipped and the batch resumes at the failed filing (Verified from
   src/fin_pipeline/cli.py:142).

### Symptom: `SEC stream stopped because a filing could not be downloaded: SEC read timed out after 3 attempts`

First check:

```bash
env | grep EDGAR_IDENTITY
```

1. This is the stream path's download-abort message, emitted when the CSV
   iterator raises `SecEdgarFetchError` (Verified from
   src/fin_pipeline/pipeline.py:888-891).
2. Confirm `EDGAR_IDENTITY` is set and outbound HTTPS to SEC is reachable; a
   read timeout is a network condition, not a data problem.
3. Re-run the stream with `--skip-existing` so completed rows are not
   re-fetched (Verified from src/fin_pipeline/cli.py:210).

### Symptom: `Failed parser engine step processing file layout layers: <path>`

First check:

```bash
ls -la <path>
```

1. This line is emitted by `log.exception`, so a full traceback follows it in
   the log (Verified from src/fin_pipeline/pipeline.py:440-443). Read the
   traceback; it names the actual exception class.
2. The failing record is marked `documentStatus = "FAILED"` with a reason
   prefixed `Layout Error:` (Verified from src/fin_pipeline/pipeline.py:444-449),
   so the failure is visible in the record even if the batch continues.
3. Common causes are a corrupt or encrypted PDF, an unsupported file
   extension (the parser returns an empty result with an `Unsupported file
   type` reason, Verified from src/fin_pipeline/pipeline.py:265-271), or a
   missing OCR dependency. Confirm the file opens, and confirm Tesseract is
   installed if the document is a scanned PDF (Verified from README.md:280-281).

### Symptom: `Err: Database index 'idx_hf_unique' already contains [company:<TICKER>, ...]`

First check:

```bash
grep -oE '"level": "ERROR"[^}]*' logs/pipeline_metrics.json | grep idx_hf_unique | tail -n 5
```

1. The `has_filing` relation edge has a unique index on `(in, out)` (Verified
   from src/fin_pipeline/db/db.py:276-278). The error means the edge already
   exists when the pipeline attempts to create it.
2. The graph step deletes the edge before re-relating it, in a single
   SurrealQL string: `DELETE has_filing WHERE in = ... AND out = ...; RELATE ...`
   (Verified from src/fin_pipeline/db/relations.py:151-154). A conflict
   indicates the delete did not match the edge, or two workers wrote the same
   edge concurrently.
3. Re-ingest the filing. The delete-then-upsert and delete-then-relate steps
   are idempotent. If the error persists, delete the offending edge manually
   over `/sql` and re-ingest.

### Symptom: `Skipping <identifier>: no filings found for the requested filters` (and no records are ingested)

First check:

```bash
head -n 5 companies.csv
```

1. This `INFO`-level line means `company.get_filings(...)` returned an empty
   result for the CSV row's filters (Verified from
   src/fin_pipeline/sec_edgar.py:347-349). It is a filter mismatch, not a
   crash.
2. Confirm the CLI `--year-range` and `--forms` flags, which override the
   row-level `year` and `forms` columns (Verified from
   src/fin_pipeline/sec_edgar.py:325-330). A reversed `--year-range` is
   rejected at the CLI (Verified from src/fin_pipeline/cli.py:222-232), but a
   too-narrow range simply returns nothing.
3. Confirm the CSV rows have a non-empty `ticker` or `cik`; a row with neither
   raises `ValueError` (Verified from src/fin_pipeline/sec_edgar.py:202-203).
4. Widen the filters or correct the CSV, then re-run.

### Symptom: `Completed with <N> errors during batch processing`

First check:

```bash
grep -oE '"level": "ERROR"[^}]*' logs/pipeline_metrics.json | tail -n 20
```

1. The batch did not abort, but some workers failed. The summary warning is
   emitted by `process_entire_directory` and `process_mixed_directory` after
   the concurrent workers finish (Verified from src/fin_pipeline/pipeline.py:712-715,
   770-773).
2. The individual worker failures are written at `DEBUG` level immediately
   below the summary (Verified from src/fin_pipeline/pipeline.py:714-715).
   Re-run with `FIN_PIPELINE_LOG_LEVEL=DEBUG` or read the `ERROR` lines in the
   metric log, which name each failing file.
3. Treat each named file with the single-file command to isolate the cause,
   then re-run the batch with `--skip-existing` to resume.

### Symptom: `No PDF assets detected inside target folder path.` (or `No PDF or HTML assets detected inside target folder path.`)

First check:

```bash
find /path/to/documents -type f \( -name '*.pdf' -o -name '*.html' \) | head
```

1. The directory scan produced no candidate files, so no tasks were created
   (Verified from src/fin_pipeline/pipeline.py:706-708, 765-767).
2. Confirm the directory path is correct and contains files with `.pdf` (for
   `scan`) or `.pdf`/`.htm`/`.html` (for `scan-mixed`) extensions. The PDF
   scanner globs `**/*.pdf` (Verified from src/fin_pipeline/crawler.py:223-228),
   and the mixed scanner filters `**/*` to those three extensions (Verified
   from src/fin_pipeline/crawler.py:332-340).
3. Confirm the directory is readable and not a symlink to an empty location,
   then re-run.

## 4. Escalation: what to capture

When a failure survives the playbook, collect the following before asking for
help. These reproduce the failure without requiring access to the live
database.

1. The exact `ERROR` or `CRITICAL` line, including the trailing `Err:` text.
2. The filing ID from that line, and the input path if it is a local file.
3. The three lines before and after the error in `logs/pipeline_metrics.json`.
4. The matching window from `logs/surrealdb.log.*`.
5. The output of `fin-pipeline file <path> --filing-id ... --ticker ...
   --type ... --source ... --dry-run` for the failing document, which isolates
   parsing from persistence (Verified from src/fin_pipeline/cli.py:279-283).
6. The version of the pipeline and the SurrealDB server. The server logs its
   version at startup (Verified from docs/OPERATIONAL_REFERENCE.md:29-30).

Do not include credentials or the raw `documentText` of a filing in the report;
both may contain sensitive data.

After a fix is applied, confirm the repair with a single filing before
re-running a full batch. A single successful non-dry-run ingest proves the
full path — parsing, validation, schema initialization, and the delete-then-
upsert write — works again (Verified from src/fin_pipeline/pipeline.py:475-500).
Only then resume the batch with `--skip-existing`, which pre-loads already-
committed filing IDs in one query and skips them at yield time (Verified from
src/fin_pipeline/pipeline.py:38-72), so the resume restarts at the first
uncommitted filing rather than redoing the whole run.
