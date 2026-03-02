# Fix Plan: Crawler Content Extraction Quality

## 1. Problem Analysis

### 1.1 Root Cause

Every crawl call in `server.py` uses a bare `CrawlerRunConfig` with no content
filtering whatsoever:

```python
# _crawl_batch (line 146) and _crawl_recursive (line 165)
config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, stream=False)
```

crawl4ai receives the full rendered page HTML — navigation sidebar, site header,
footer, "In this section" index panels, cookie banners — and converts it all to
markdown. Every chunk stored in `crawled_pages` therefore starts with the entire
left-nav tree of the DITA spec site (~3000 words) before the actual spec content
begins.

Consequences observed:
- Single page retrieval returns massive blobs (~65k tokens) instead of ~500-2000 words
- The end of one page's content bleeds into the beginning of another page's
  sidebar/navigation links (seen in result.json: `bodydiv` page ends with
  `linkinfo` content from a related-links sidebar)
- Embeddings are polluted with navigation boilerplate, degrading RAG quality
- Chunks are dominated by nav text, making BM25 keyword search noisy

### 1.2 Secondary Issue: `result.markdown` API change

In current crawl4ai, `result.markdown` returns a `MarkdownGenerationResult`
object, not a plain string. The correct properties are:
- `result.markdown.raw_markdown` — full unfiltered markdown string
- `result.markdown.fit_markdown` — filtered markdown string (only populated when
  a `content_filter` is configured)

The current code passes `r.markdown` directly as if it were a string. This works
only because `MarkdownGenerationResult.__str__` falls back to `raw_markdown`. It
is fragile and must be made explicit. When we add a content filter we must switch
to `.fit_markdown`.

---

## 2. What crawl4ai Now Offers (from skill reference)

The following `CrawlerRunConfig` parameters are relevant and were **not used** in
the original code:

| Parameter | Type | Purpose |
|-----------|------|---------|
| `css_selector` | `str` | Keep only the DOM subtree matching this selector. Affects the entire extraction pipeline. Most surgical fix. |
| `excluded_tags` | `list` | Remove entire HTML tags before markdown generation. e.g. `["nav", "footer", "aside"]`. |
| `excluded_selector` | `str` | CSS selector for elements to **remove**. e.g. `"#sidebar, .breadcrumbs"`. |
| `word_count_threshold` | `int` | Skip text blocks below N words. Filters out nav labels like "Next", "Home". |
| `remove_overlay_elements` | `bool` | Remove modals and cookie banners. |
| `markdown_generator` | `MarkdownGenerationStrategy` | Attach a content filter to produce `fit_markdown`. |

Available content filters (via `DefaultMarkdownGenerator(content_filter=...)`):

| Filter | When to use |
|--------|------------|
| `PruningContentFilter` | **Best for our case.** No query needed. Scores text/link density and structure to prune low-quality blocks. Removes nav lists automatically. |
| `BM25ContentFilter` | Relevance-based. Useful when a fixed query topic is known. |
| `LLMContentFilter` | Expensive. Reserve for cases where structural filters fail. |

---

## 3. Recommended Approach: Layered Filtering

Use three layers. Each catches what the previous one misses.

### Layer 1 — Semantic tag exclusion (fast, zero-cost)
```python
excluded_tags=["nav", "footer", "header", "aside"]
```
Removes HTML elements that are semantically navigation/chrome. Zero performance cost.

### Layer 2 — PruningContentFilter (structural, zero-cost)
```python
PruningContentFilter(threshold=0.45, threshold_type="fixed", min_word_threshold=20)
```
After tag exclusion, some nav content remains in generic `<div>` or `<ul>` elements.
PruningContentFilter scores each block by text density vs. link density. Navigation
lists (many short anchor links, little prose) score low and get pruned. Actual spec
content (dense paragraphs, code blocks) scores high. Results go to
`result.markdown.fit_markdown`.

### Layer 3 — word_count_threshold (quick pre-filter)
```python
word_count_threshold=10
```
Discards any text block under 10 words before markdown generation. Eliminates
isolated nav labels ("Overview", "Next topic") that survive the other filters.

### Optional Layer 4 — css_selector (most precise, site-specific)
If the above still leaves noise, inspect the dita-lang.org page source (DevTools)
to find the CSS selector for the main content container — likely `main`, `article`,
or a class like `.body-content`. Add:
```python
css_selector="main"
```
This is the single most effective change but requires knowing the site's HTML.

---

## 4. Specific Code Changes in `server.py`

### 4.1 New imports (add at top)

```python
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
```

### 4.2 Module-level config (add after existing singletons, ~line 58)

```python
# Shared crawl config: strips nav/chrome, produces clean fit_markdown
_CRAWL_CONFIG = CrawlerRunConfig(
    cache_mode=CacheMode.BYPASS,
    stream=False,
    excluded_tags=["nav", "footer", "header", "aside"],
    word_count_threshold=10,
    remove_overlay_elements=True,
    markdown_generator=DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=0.45,
            threshold_type="fixed",
            min_word_threshold=20,
        ),
        options={"ignore_links": False, "body_width": 0},
    ),
)
```

`body_width=0` disables line-wrapping so spec content (long attribute lists, XML
examples) is not artificially broken at 80 characters.

### 4.3 Helper: extract clean markdown from a CrawlResult (add near singletons)

```python
def _extract_markdown(result) -> str:
    """Return the best available markdown string from a CrawlResult."""
    md = result.markdown
    if hasattr(md, "fit_markdown") and md.fit_markdown:
        return md.fit_markdown
    if hasattr(md, "raw_markdown") and md.raw_markdown:
        return md.raw_markdown
    return str(md)  # legacy fallback for older crawl4ai versions
```

### 4.4 `_crawl_batch` (line 143)

Change `config = CrawlerRunConfig(...)` to use `_CRAWL_CONFIG`.
Change `r.markdown` to `_extract_markdown(r)`:

```python
async def _crawl_batch(
    crawler: AsyncWebCrawler, urls: List[str], max_concurrent: int = 10
) -> List[Dict[str, Any]]:
    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=70.0,
        check_interval=1.0,
        max_session_permit=max_concurrent,
    )
    results = await crawler.arun_many(urls=urls, config=_CRAWL_CONFIG, dispatcher=dispatcher)
    return [
        {"url": r.url, "markdown": _extract_markdown(r)}
        for r in results
        if r.success and r.markdown
    ]
```

### 4.5 `_crawl_recursive` (line 165)

Remove the local `config = CrawlerRunConfig(...)` at the top of the function.
Pass `_CRAWL_CONFIG` to `crawler.arun_many`.
Change `r.markdown` to `_extract_markdown(r)`:

```python
# Remove:
config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, stream=False)

# Change arun_many call to:
results = await crawler.arun_many(urls=to_crawl, config=_CRAWL_CONFIG, dispatcher=dispatcher)

# Change result collection to:
if r.success and r.markdown:
    depth_results.append({"url": r.url, "markdown": _extract_markdown(r)})
```

### 4.6 `crawl_single_page` tool (line 357)

```python
# Replace the arun call config:
result = await crawler.arun(url=url, config=_CRAWL_CONFIG)

# Replace result dict:
stats = await _ingest_results(
    supabase_client,
    [{"url": url, "markdown": _extract_markdown(result)}],
    crawl_type="single_page",
)

# Update content_length stat:
"content_length": len(_extract_markdown(result)),
```

Note: `crawl_single_page` currently calls `_ingest_results` without `await`
(line 368). This is a bug — `_ingest_results` is an `async` function. Add `await`.

---

## 5. Tuning PruningContentFilter After Re-crawl

`threshold=0.45` is a starting point. Test a single page first:

```python
result = await crawler.arun(
    "https://dita-lang.org/dita/langRef/base/topic",
    config=_CRAWL_CONFIG
)
print(result.markdown.fit_markdown[:3000])
```

Expected output: starts with the `<topic>` element description, content model,
attributes table. No site nav, no left sidebar.

- If nav content still leaks → raise threshold (e.g. `0.55`)
- If real spec content is pruned → lower threshold (e.g. `0.35`)
- Raise `min_word_threshold` to `30` if single-sentence nav items persist

---

## 6. Re-crawl Required

The fix only affects new crawls. Existing data in `crawled_pages` was stored with
polluted content. After implementing the fix:

1. For each source, call `delete_source` to remove all stored data
2. Re-crawl from the links.txt file via `smart_crawl_url`

This ensures all chunks and embeddings reflect actual spec content.

---

## 7. Bug Found: Missing `await` in `crawl_single_page`

Line 368 of `server.py`:
```python
stats = _ingest_results(   # ← missing await
    supabase_client,
    [{"url": url, "markdown": result.markdown}],
    crawl_type="single_page",
)
```
`_ingest_results` is an `async def`. Calling it without `await` returns a
coroutine object instead of the result dict. Fix: add `await`.

---

## 8. Summary of All Changes

| Location | Change |
|----------|--------|
| Imports | Add `PruningContentFilter`, `DefaultMarkdownGenerator` |
| Module level | Add `_CRAWL_CONFIG` singleton |
| Module level | Add `_extract_markdown()` helper |
| `_crawl_batch` | Use `_CRAWL_CONFIG`, use `_extract_markdown` |
| `_crawl_recursive` | Remove local config, use `_CRAWL_CONFIG`, use `_extract_markdown` |
| `crawl_single_page` | Use `_CRAWL_CONFIG`, use `_extract_markdown`, add missing `await` |
