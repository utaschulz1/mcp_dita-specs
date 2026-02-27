# Plan: Incremental Supabase Writes for `smart_crawl_url`

## Context

`smart_crawl_url` currently collects ALL crawled pages in memory via `_crawl_recursive`, then writes everything to Supabase in one shot via `_ingest_results`. If the crawl crashes or the SSE connection times out mid-crawl, nothing is written. The fix is to write to Supabase after each depth level completes, so partial progress is preserved.

## Files to Modify

- `src/server.py` — `_crawl_recursive` and `smart_crawl_url`
- `src/utils.py` — `_ingest_results` (make async)

---

## Implementation Plan

### 1. Make `_ingest_results` async (utils.py, line 200)

`add_documents_to_supabase` uses `time.sleep` and is currently sync. For now, just add `async` to the signature so it can be awaited from the async context — the internals can stay sync via `asyncio.to_thread` or left as-is (Supabase client is blocking anyway).

Change:
```python
def _ingest_results(...) -> Dict[str, Any]:
```
To:
```python
async def _ingest_results(...) -> Dict[str, Any]:
```

And wrap the blocking call at line 246 in `asyncio.to_thread` if needed.

### 2. Refactor `_crawl_recursive` (server.py, lines 148–193)

Add `supabase_client`, `crawl_type`, and `chunk_size` parameters. After each depth iteration, call `_ingest_results` with that depth's results immediately. Return cumulative stats instead of a list.

**New signature:**
```python
async def _crawl_recursive(
    crawler: AsyncWebCrawler,
    start_urls: List[str],
    supabase_client: Client,
    crawl_type: str = "webpage",
    max_depth: int = 3,
    max_concurrent: int = 10,
    chunk_size: int = 5000,
) -> Dict[str, Any]:
```

**New loop structure:**
```python
cumulative_stats = {"chunks_stored": 0, "code_examples_stored": 0, "sources_updated": 0, "delete_ok": True, "sources_ok": True}

for depth_num in range(max_depth):
    to_crawl = [u for u in current if u not in visited]
    if not to_crawl:
        break

    results = await crawler.arun_many(...)
    depth_results = []

    for r in results:
        norm = normalize(r.url)
        visited.add(norm)
        if r.success and r.markdown:
            depth_results.append({"url": r.url, "markdown": r.markdown})
            for link in r.links.get("internal", []):
                ...

    current = next_level

    if depth_results:
        stats = await _ingest_results(supabase_client, depth_results, crawl_type, chunk_size=chunk_size)
        # accumulate stats
        cumulative_stats["chunks_stored"] += stats["chunks_stored"]
        cumulative_stats["code_examples_stored"] += stats["code_examples_stored"]
        cumulative_stats["sources_updated"] += stats["sources_updated"]
        if not stats["delete_ok"]:
            cumulative_stats["delete_ok"] = False

return cumulative_stats
```

### 3. Update `smart_crawl_url` (server.py, lines 393–417)

Remove the `_ingest_results` call at the end (it now happens inside `_crawl_recursive`). Pass the new required parameters to `_crawl_recursive`. For sitemap/txt paths, call `_ingest_results` directly as before (they don't use `_crawl_recursive`).

```python
else:
    stats = await _crawl_recursive(
        crawler, [url],
        supabase_client=supabase_client,
        crawl_type="webpage",
        max_depth=max_depth,
        max_concurrent=max_concurrent,
        chunk_size=chunk_size,
    )
    crawl_type = "webpage"

# No _ingest_results call here for webpage — already done per depth
return json.dumps({
    "success": stats["sources_ok"] and stats["chunks_stored"] > 0,
    ...stats fields...
}, indent=2)
```

---

## What This Achieves

- Depth 1 pages written immediately after depth 1 crawl completes
- Depth 2 pages written after depth 2, etc.
- If the crawl times out or crashes at depth 3, depths 1 and 2 are already in Supabase
- Memory freed after each depth level

## Verification

1. Start server with `TRANSPORT=sse`
2. Run `smart_crawl_url` on `https://www.dita-ot.org/dev/topics/input-formats` with `max_depth=3`
3. While running, check Supabase — rows should appear before the crawl completes
4. Kill the server mid-crawl — verify data from completed depth levels is in Supabase
5. Let a full crawl complete — verify total chunks match expected
