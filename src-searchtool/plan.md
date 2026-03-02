# Plan: DITA Specs MCP Server (retrieval-only)

## 1. What the existing tools do

### `get_available_sources`
Queries the `sources` table (`SELECT * ORDER BY source_id`) and returns every
known domain (source_id) with its summary, word count, and timestamps.
No config dependencies; works the same regardless of feature flags.

### `perform_rag_query` — config interactions

| Config flag | Effect |
|---|---|
| `USE_HYBRID_SEARCH=true` | Combines two searches: (1) vector similarity via `match_crawled_pages` RPC, (2) ILIKE keyword scan on `crawled_pages.content`. Results appearing in both lists are boosted (similarity ×1.2, capped at 1.0) and ranked first. Vector-only matches come second; keyword-only matches fill the tail. |
| `USE_RERANKING=true` | After retrieval, a cross-encoder model (`cross-encoder/ms-marco-MiniLM-L-6-v2`) re-scores every result and re-sorts the list by relevance. The model is loaded once at server startup. |
| `USE_AGENTIC_RAG=true` | **Ingestion-time only.** When this flag was set during crawling, long code/XML blocks were extracted and stored in the separate `code_examples` table with LLM-generated summaries and their own embeddings. At query time the separate `search_code_examples` tool uses that table. The flag is not read during retrieval. |

### `search_code_examples` (tool at line 934)
Queries the `code_examples` table via `match_code_examples` RPC using an
enriched query ("Code example for X\n\nSummary: …"). Supports optional
source_id filter. Also applies reranking if `USE_RERANKING=true`. Returns
code content + summary per hit.

---

## 2. What to remove / keep / add

### Remove entirely (crawling & Neo4j junk)
- Tools: `crawl_single_page`, `smart_crawl_url`, `check_ai_script_hallucinations`,
  `query_knowledge_graph`, `parse_github_repository`
- Helpers: `is_sitemap`, `is_txt`, `parse_sitemap`, `smart_chunk_markdown`,
  `extract_section_info`, `process_code_example`, lazy browser singleton,
  `validate_neo4j_connection`, `format_neo4j_error`, `validate_script_path`,
  `validate_github_url`, all Neo4j imports, all crawl4ai imports
- Lifespan complexity: Neo4j init/cleanup, browser cleanup

### Keep and adapt
- Supabase client singleton (unchanged)
- CrossEncoder singleton (unchanged — `USE_RERANKING`)
- Simplified `DitaSpecsContext` dataclass (only `supabase_client`, `reranking_model`)
- Simplified `lifespan` (no Neo4j, no browser)
- `rerank_results` helper
- `get_available_sources` (**updated** — see below)
- `perform_rag_query` (unchanged logic, cleaner context)
- `search_code_examples` (unchanged — useful for DITA XML markup examples stored
  during agentic ingestion)

### Updated: `get_available_sources`

Instead of querying only the `sources` table (one row per netloc), this tool now
queries `crawled_pages` to derive sub-collections at `netloc/first-path-segment`
granularity. This lets Claude discover that `dita-lang.org` has two distinct
sub-collections (`/1.3` and `/dita`) rather than appearing as a single opaque blob.

Each collection entry in the response contains:
- **`label`** — human/Claude-readable name, e.g. `dita-lang.org/1.3`.
  Root URLs (no path segment) are labelled `dita-lang.org/(root)`.
- **`url_prefix`** — the actual URL prefix used for filtering, e.g.
  `https://dita-lang.org/1.3`. This is the value to pass to `list_spec_files`.
- **`file_count`** — number of distinct URLs in that sub-collection.
- **`summary`** — the existing per-netloc summary from the `sources` table,
  shared across all sub-collections of the same domain.

SQL to derive sub-collections (run inside the server, not as a stored RPC):
```sql
SELECT
  source_id,
  COALESCE(
    NULLIF(REGEXP_REPLACE(url, '^https?://[^/]+(/[^/?#]*)?.*$', '\1'), ''),
    '(root)'
  ) AS first_segment,
  COUNT(DISTINCT url) AS file_count
FROM crawled_pages
GROUP BY source_id, first_segment
ORDER BY source_id, first_segment
```

The `url_prefix` for each row is reconstructed as
`https://{source_id}{first_segment}` (replacing `(root)` with empty string).

### Add — two new tools

#### `list_spec_files`
Returns **all distinct URLs** for a given sub-collection in one response (~950
to ~2500 URLs is well within MCP message limits as a JSON array).
No pagination — the `url_prefix` filter keeps the list focused.

```
list_spec_files(url_prefix: str) -> str
```

Input is the `url_prefix` value returned by `get_available_sources`
(e.g. `https://dita-lang.org/1.3`). Claude obtains valid values from
`get_available_sources` first — no guessing required.

Supabase query:
```sql
SELECT DISTINCT url
FROM crawled_pages
WHERE url LIKE :url_prefix || '%'
ORDER BY url
```

Returns a JSON list of URLs and a `count`.

#### `retrieve_content_by_links`
Given one or more URLs (exact matches to values stored in the `url` column),
retrieves **all chunks** for each URL, sorted by `chunk_number`, and
concatenates them into a single string per file.

```
retrieve_content_by_links(urls: list[str]) -> str
```

Supabase query per URL:
```sql
SELECT url, chunk_number, content
FROM crawled_pages
WHERE url = :url
ORDER BY chunk_number ASC
```

Hard limit: **maximum 10 URLs per call**. Requests with more than 10 URLs are
rejected with an error. Claude must be selective — choose the most relevant
URLs from `list_spec_files` or a prior RAG result. If the first batch does not
answer the question, Claude should read the results and make a new, refined
request (still max 10). Bulk dumping and splitting are explicitly not supported.

---

## 3. Rewrite plan (files)

### `dita_specs_mcp.py` (rename/rewrite from `crawl4ai_mcp.py`)

```
imports:
  mcp.server.fastmcp, sentence_transformers.CrossEncoder
  contextlib, dataclasses, typing
  dotenv, supabase, json, os

  from utils import (
      get_supabase_client,
      search_documents,
      search_code_examples as _search_code_examples,  # avoid name clash
  )

module-level singletons:
  _supabase_client  (Supabase)
  _reranking_model  (CrossEncoder | None)

lifespan:
  yields DitaSpecsContext(supabase_client, reranking_model)
  no startup/teardown side-effects

tools:
  get_available_sources      (updated — sub-collection granularity)
  perform_rag_query          (keep logic, clean up context access)
  search_code_examples       (keep logic, clean up context access)
  list_spec_files            (new)
  retrieve_content_by_links  (new)
```

### `utils.py` (stripped)

Keep only:
- `get_supabase_client`
- `create_embeddings_batch`
- `create_embedding`
- `search_documents`
- `search_code_examples`

Remove everything related to ingestion:
`generate_contextual_embedding`, `process_chunk_with_context`,
`add_documents_to_supabase`, `extract_code_blocks`,
`generate_code_example_summary`, `add_code_examples_to_supabase`,
`update_source_info`, `extract_source_summary`

---

## 4. Additional thoughts for the DITA skill

1. **Query guidance for Claude**: The server description and tool docstrings should
   explain the expected `source_id` values (OASIS domains like
   `docs.oasis-open.org`) so Claude's DITA skill can filter correctly without
   calling `get_available_sources` every time.

2. **Useful RAG query patterns for DITA authoring**:
   - `"content model for <topic> element"` → `perform_rag_query`
   - `"attribute definitions for <element>"` → `perform_rag_query`
   - `"specialization rules"` → `perform_rag_query`
   - `"conref keyref usage"` → `perform_rag_query`
   - `"XML markup example <element>"` → `search_code_examples`
   - Known spec file (e.g. `https://…/dita-v2.0-langref/attributes/…`) →
     `retrieve_content_by_links`

3. **Two-phase retrieval pattern** recommended for the DITA skill:
   1. Use `perform_rag_query` for free-text semantic search when the exact file
      is unknown.
   2. Use `list_spec_files` to browse if the user/Claude needs to orient itself
      (e.g., "show me all attribute spec pages").
   3. Use `retrieve_content_by_links` when the relevant URL is already known
      (e.g., found in a previous RAG result or in a fixed reference list).

4. **`search_code_examples`** is particularly useful because DITA spec pages
   contain many XML markup examples that were extracted separately during
   agentic ingestion and embedded with richer context. This is the best tool
   for "show me how to write a valid DITA <X> element".

5. **Dependencies to add**: `mcp`, `sentence-transformers`, `supabase`,
   `openai`, `python-dotenv`. Remove `crawl4ai`, `requests`, `neo4j` driver,
   knowledge-graph modules.

6. **Environment variables needed** (subset of original):
   - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
   - `OPENAI_API_KEY` (embeddings via `text-embedding-3-small`)
   - `USE_HYBRID_SEARCH`, `USE_RERANKING`
   - `HOST`, `PORT`
   - _Drop_: `USE_AGENTIC_RAG`, `USE_CONTEXTUAL_EMBEDDINGS`, `MODEL_CHOICE`,
     all `NEO4J_*`

---

## 5. Questions / decisions needed before implementation

- [x] **Naming**: Use Python-style underscores — `retrieve_content_by_links`.
- [x] **Pagination in `list_spec_files`**: No pagination. ~950–2500 distinct URLs
  fit comfortably in one response. The `url_prefix` filter (one sub-collection
  at a time) keeps lists focused and manageable.
- [x] **Content cap in `retrieve_content_by_links`**: Hard limit of 10 URLs per
  call. No splitting allowed — Claude must select the most relevant URLs and
  refine subsequent requests based on what it reads.
- [x] **`search_code_examples`**: Keep name as-is.

---

## 6. Progress checklist (from instructions.md)

- [x] Read the files in the current folder
- [x] Explain what `get_available_sources` and `perform_rag_query` do
- [x] Suggest a rewrite plan
- [x] Think about additional information needed (questions above)
- [x] Write this plan to the current directory as .md
- [ ] Await user feedback / additional information
- [ ] Implement the rewrite
