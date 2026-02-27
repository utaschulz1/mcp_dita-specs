# mcp-crawl4ai2vectordb

MCP server for crawling web documentation and storing it in a Supabase vector database.
Credits: [craw4ai](https://github.com/unclecode/crawl4ai) and [Cole Medin's mcp server](https://github.com/coleam00/mcp-crawl4ai-rag.git).

**Responsibility**: ingestion only — crawl, chunk, embed, store. No search, no reranking, no Neo4j.

## Tools

| Tool | Description |
|---|---|
| `crawl_single_page` | Crawl one URL and store its content |
| `smart_crawl_url` | Auto-detect URL type (sitemap / txt / webpage) and crawl accordingly |
| `get_available_sources` | List all sources stored in the database |
| `delete_source` | Delete a source and all its content from the database |

## Setup

```bash
cp .env.example .env
# fill in OPENAI_API_KEY, MODEL_CHOICE, SUPABASE_URL, SUPABASE_SERVICE_KEY
uv sync
```

## Running

**stdio (recommended — Claude Code manages the process):**

Add to your MCP config:
```json
{
  "mcpServers": {
    "mcp_dita-specs": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "-u", "src/server.py"],
      "cwd": "/home/hpz440/projects/mcp_dita-specs"
    }
  }
}
```
or

```json
{
  "mcpServers": {
    "crawl4ai2vectordb": {
      "type": "sse",
      "url": "http://localhost:8051/sse"
    }
  }
}
```
**SSE (manual startup, for multi-client use):**
```bash
TRANSPORT=sse uv run python -u src/server.py
```

## Optional features

| Env var | Default | Effect |
|---|---|---|
| `USE_CONTEXTUAL_EMBEDDINGS` | `false` | Prepend LLM-generated context to each chunk before embedding (improves retrieval accuracy, costs more) |
| `USE_AGENTIC_RAG` | `false` | Extract code blocks and store them separately in `code_examples` table |
