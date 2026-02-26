# Plan: mcp-crawl4ai2vectordb

## Purpose

A focused MCP server responsible exclusively for **crawling web documentation and storing it in a Supabase vector database**. It is one of three servers extracted from the monolithic `crawl4ai_mcp.py`:

| Server | Responsibility |
|---|---|
| **mcp-crawl4ai2vectordb** (this one) | Crawl → chunk → embed → store |
| mcp-vectordb-query | Search / RAG query against stored content |
| mcp-neo4j-validator | Parse GitHub repos into Neo4j, detect AI hallucinations |

This server is **write-only** toward the database. It does not search, rerank, or validate code.

---

## Tools

### 1. `crawl_single_page(url)`
Crawl one URL, chunk its markdown, embed and store in Supabase.

- Detects no link following, just the single page
- Upserts source metadata in `sources` table
- Deletes existing chunks for the URL before reinserting (idempotent)
- Optionally extracts code blocks if `USE_AGENTIC_RAG=true`
- Returns: `{ success, url, chunks_stored, code_examples_stored, content_length, total_word_count, source_id }`

### 2. `smart_crawl_url(url, max_depth=3, max_concurrent=10, chunk_size=5000)`
Intelligently crawl a URL, auto-detecting the crawl strategy:

| URL type | Strategy |
|---|---|
| `*.txt` (e.g. `llms.txt`) | Direct fetch, no following links |
| `sitemap.xml` | Parse all URLs from sitemap, crawl in parallel |
| Regular webpage | Recursively follow internal links up to `max_depth` |

- Processes all discovered pages in parallel (memory-adaptive dispatcher)
- Upserts source metadata per unique domain
- Batched embedding and insertion (default batch size: 20)
- Returns: `{ success, url, crawl_type, pages_crawled, chunks_stored, code_examples_stored, urls_crawled }`

### 3. `get_available_sources()`
List all sources that have been crawled and stored.

- Queries the `sources` table directly
- Returns: `{ sources: [{ source_id, summary, total_words, created_at, updated_at }], count }`
- Needed so the caller knows what is available before deciding what to crawl or delete

### 4. `delete_source(source_id)`
Delete a source and all its associated content from the database.

- Deletes all rows in `crawled_pages` with matching `source_id`
- Deletes all rows in `code_examples` with matching `source_id`
- Deletes the row in `sources`
- Returns: `{ success, source_id, deleted: { pages, code_examples, source_record } }`
- Not present in the original server — needed for source lifecycle management

---

## Architecture

### Transport
`stdio` by default. This server runs locally; there is no multi-client sharing requirement. Claude Code manages the process lifecycle — no manual startup needed.

Override with `TRANSPORT=sse` if SSE is needed (e.g. for Docker or multi-client use).

### Lifecycle
- **Supabase client**: module-level singleton, initialized once at import time (stateless HTTP client, safe to share)
- **Browser**: lazy singleton, created on first crawl call via `_get_crawler()`, closed on server shutdown
- **No cross-encoder model**: reranking is the search server's concern
- **No Neo4j**: validation is the neo4j server's concern

### Content processing pipeline (per page)
```
raw HTML
  └─ crawl4ai → markdown
       └─ smart_chunk_markdown() → chunks[]
            ├─ [optional] generate_contextual_embedding() per chunk (LLM call)
            └─ create_embeddings_batch() → vectors[]
                 └─ Supabase insert → crawled_pages

  [optional, USE_AGENTIC_RAG=true]
  markdown
    └─ extract_code_blocks() → code_blocks[]
         ├─ generate_code_example_summary() per block (LLM call, parallel)
         └─ create_embeddings_batch() → vectors[]
              └─ Supabase insert → code_examples
```

### Chunking strategy
Respects content structure — splits at code block boundaries (```` ``` ````), then paragraph breaks (`\n\n`), then sentence boundaries (`. `). Minimum break threshold: 30% of chunk size to avoid tiny tail chunks.

---

## File structure

```
mcp-crawl4ai2vectordb/
├── docs/
│   └── plan.md               ← this file
├── src/
│   ├── server.py             ← MCP server, tool definitions
│   └── utils.py              ← Supabase, embedding, chunking helpers
├── .env.example
├── pyproject.toml
└── README.md
```

`src/utils.py` is a trimmed copy of the original `utils.py`, keeping only ingestion-relevant functions:
- `get_supabase_client`
- `create_embedding` / `create_embeddings_batch`
- `generate_contextual_embedding` / `process_chunk_with_context`
- `add_documents_to_supabase`
- `add_code_examples_to_supabase`
- `update_source_info`
- `extract_source_summary`
- `extract_code_blocks`
- `generate_code_example_summary`

Removed from utils (belong to search server):
- `search_documents`
- `search_code_examples`

---

## Environment variables

```dotenv
# Transport: 'stdio' (default) or 'sse'
TRANSPORT=stdio

# Required for embedding and optional LLM calls
OPENAI_API_KEY=

# LLM model for source summaries and contextual embeddings
# A cheap/fast model is sufficient: e.g. gpt-4.1-nano, gpt-4o-mini
MODEL_CHOICE=

# Supabase connection
SUPABASE_URL=
SUPABASE_SERVICE_KEY=

# Optional: enrich chunk embeddings with surrounding context (LLM call per chunk)
USE_CONTEXTUAL_EMBEDDINGS=false

# Optional: extract and store code blocks separately in code_examples table
USE_AGENTIC_RAG=false

# Only needed if running as SSE server
HOST=localhost
PORT=8051
```

---

## Dependencies

```toml
[project]
name = "mcp-crawl4ai2vectordb"
requires-python = ">=3.12"

dependencies = [
    "mcp[cli]",
    "crawl4ai",
    "supabase",
    "openai",
    "python-dotenv",
]
```

No `sentence-transformers`, no `neo4j` driver — those are not needed here.

---

## What changes from the original server

| Area | Original | This server |
|---|---|---|
| Transport default | `sse` | `stdio` |
| Search tools | `perform_rag_query`, `search_code_examples` | Removed — search server's job |
| Neo4j tools | `parse_github_repository`, `check_ai_script_hallucinations`, `query_knowledge_graph` | Removed — neo4j server's job |
| Source deletion | Not available | New `delete_source` tool |
| Cross-encoder model | Loaded at startup if `USE_RERANKING=true` | Not loaded — not relevant |
| Neo4j init at startup | Conditional | Not present |
| `knowledge_graphs/` modules | Imported | Not imported |

---

## Out of scope

- Vector similarity search / RAG queries
- Hybrid search (keyword + vector)
- Reranking with cross-encoder
- Neo4j knowledge graph
- AI hallucination detection
- GitHub repository parsing
