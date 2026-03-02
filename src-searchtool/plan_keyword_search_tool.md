# Plan: `keyword_search` Tool

## Goal
Add a dedicated `keyword_search` tool to `src-searchtool/server.py` for exact-term
lookups against DITA spec content. Complements `perform_rag_query` (semantic) and
`search_code_examples` (code-focused).

## Why a separate tool
- Explicit intent: the LLM can choose keyword vs semantic based on query type
- No dependency on `USE_KEYWORD_SEARCH` env var — always available
- Cleaner than switching modes via env var for ad-hoc queries

## Implementation (single tool, ~40 lines)

### Signature
```python
@mcp.tool()
async def keyword_search(
    ctx: Context,
    query: str,
    source_id: str = None,
    match_count: int = 5,
) -> str:
```

### Logic
1. Run ILIKE query on `crawled_pages.content` — same as the `USE_KEYWORD_SEARCH`
   path already in `perform_rag_query`:
   ```python
   supabase_client.from_('crawled_pages')
       .select('id, url, chunk_number, content, metadata, source_id')
       .ilike('content', f'%{query}%')
       .eq('source_id', source_id)   # optional
       .limit(match_count * 3)
   ```
2. Apply reranking if `USE_RERANKING=true` (reuse `rerank_results` helper).
3. Apply `-5` rerank score threshold (same as other tools).
4. Slice to `match_count` and return.

### Return shape
Same as `perform_rag_query` but `similarity` omitted (not applicable) and
`search_mode` fixed to `"keyword"`.

## Notes
- The `USE_KEYWORD_SEARCH` env var in `perform_rag_query` can stay for users who
  want all queries to default to keyword mode, but the new tool is independent of it.
- No changes needed to `utils.py` — all logic is inline in the tool.
- Consider also adding keyword search on `code_examples.content` as an optional
  `search_target: str = "pages"` parameter (`"pages"` or `"code"`), but that can
  be a follow-up.
