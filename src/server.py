"""
mcp-dita-specs/src/server.py

MCP server for crawling web documentation and storing it in a Supabase
vector database. Ingestion-only: no search, no reranking, no Neo4j.
"""
import asyncio
import json
import os
import concurrent.futures
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urldefrag
from xml.etree import ElementTree

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context
from supabase import Client

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    MemoryAdaptiveDispatcher,
)

from utils import (
    get_supabase_client,
    add_documents_to_supabase,
    add_code_examples_to_supabase,
    update_source_info,
    extract_source_summary,
    extract_code_blocks,
    extract_section_info,
    generate_code_example_summary,
    smart_chunk_markdown,
    _process_code_example,
)

# Load .env from project root (one level up from src/)
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env", override=True)

# Redirect stdout to stderr in stdio mode so crawl4ai logs don't corrupt the protocol
#import sys
#if os.getenv("TRANSPORT", "stdio") == "stdio":
    #sys.stdout = sys.stderr

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_supabase_client: Client = get_supabase_client()

_crawler: Optional[AsyncWebCrawler] = None
_crawler_lock = asyncio.Lock()


async def _get_crawler() -> AsyncWebCrawler:
    """Return the shared browser crawler, creating it lazily on first call."""
    global _crawler
    if _crawler is None:
        async with _crawler_lock:
            if _crawler is None:
                print("Launching browser (lazy init)…")
                _crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
                await _crawler.__aenter__()
    return _crawler


# ---------------------------------------------------------------------------
# Server context & lifespan
# ---------------------------------------------------------------------------

@dataclass
class ServerContext:
    supabase_client: Client


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[ServerContext]:
    yield ServerContext(supabase_client=_supabase_client)

    global _crawler
    if _crawler is not None:
        print("Closing browser…")
        await _crawler.__aexit__(None, None, None)
        _crawler = None


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "mcp-dita-specs",
    instructions="Crawl web documentation and store it in a Supabase vector database.",
    lifespan=_lifespan,
    host=os.getenv("HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", "8051")),
)


# ---------------------------------------------------------------------------
# Helper: URL type detection & crawling
# ---------------------------------------------------------------------------

def _is_sitemap(url: str) -> bool:
    return url.endswith("sitemap.xml") or "sitemap" in urlparse(url).path


def _is_txt(url: str) -> bool:
    return url.endswith(".txt")


def _parse_sitemap(sitemap_url: str) -> List[str]:
    try:
        resp = requests.get(sitemap_url, timeout=30)
        if resp.status_code == 200:
            tree = ElementTree.fromstring(resp.content)
            return [loc.text for loc in tree.findall(".//{*}loc")]
    except Exception as e:
        print(f"Sitemap parse error: {e}")
    return []


async def _crawl_text_file(crawler: AsyncWebCrawler, url: str) -> List[Dict[str, Any]]:
    result = await crawler.arun(url=url, config=CrawlerRunConfig())
    if result.success and result.markdown:
        return [{"url": url, "markdown": result.markdown}]
    print(f"Failed to crawl {url}: {result.error_message}")
    return []


async def _crawl_batch(
    crawler: AsyncWebCrawler, urls: List[str], max_concurrent: int = 10
) -> List[Dict[str, Any]]:
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, stream=False)
    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=70.0,
        check_interval=1.0,
        max_session_permit=max_concurrent,
    )
    results = await crawler.arun_many(urls=urls, config=config, dispatcher=dispatcher)
    return [{"url": r.url, "markdown": r.markdown} for r in results if r.success and r.markdown]


async def _crawl_recursive(
    crawler: AsyncWebCrawler,
    start_urls: List[str],
    supabase_client: Client,
    crawl_type: str = "webpage",
    max_depth: int = 3,
    max_concurrent: int = 10,
    chunk_size: int = 5000,
) -> Dict[str, Any]:
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, stream=False)
    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=70.0,
        check_interval=1.0,
        max_session_permit=max_concurrent,
    )

    def normalize(url: str) -> str:
        return urldefrag(url)[0]

    # Only follow links under the same path prefix as the starting URL.
    # If the start URL is a page (no trailing slash), use its parent directory.
    parsed_start = urlparse(normalize(start_urls[0]))
    path = parsed_start.path
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    url_prefix = f"{parsed_start.scheme}://{parsed_start.netloc}{path}"

    def is_in_scope(url: str) -> bool:
        return url.startswith(url_prefix)

    visited: set = set()
    current = {normalize(u) for u in start_urls}
    cumulative_stats: Dict[str, Any] = {
        "chunks_stored": 0,
        "code_examples_stored": 0,
        "sources_updated": 0,
        "delete_ok": True,
        "sources_ok": True,
        "pages_crawled": 0,
    }

    for _ in range(max_depth):
        to_crawl = [u for u in current if u not in visited]
        if not to_crawl:
            break

        results = await crawler.arun_many(urls=to_crawl, config=config, dispatcher=dispatcher)
        next_level: set = set()
        depth_results: List[Dict[str, Any]] = []

        for r in results:
            norm = normalize(r.url)
            visited.add(norm)
            if r.success and r.markdown:
                depth_results.append({"url": r.url, "markdown": r.markdown})
                for link in r.links.get("internal", []):
                    nxt = normalize(link["href"])
                    if nxt not in visited and is_in_scope(nxt):
                        next_level.add(nxt)

        current = next_level

        if depth_results:
            stats = await _ingest_results(supabase_client, depth_results, crawl_type, chunk_size=chunk_size)
            cumulative_stats["chunks_stored"] += stats["chunks_stored"]
            cumulative_stats["code_examples_stored"] += stats["code_examples_stored"]
            cumulative_stats["sources_updated"] += stats["sources_updated"]
            cumulative_stats["pages_crawled"] += len(depth_results)
            if not stats["delete_ok"]:
                cumulative_stats["delete_ok"] = False
            if not stats["sources_ok"]:
                cumulative_stats["sources_ok"] = False

    return cumulative_stats


# ---------------------------------------------------------------------------
# Shared ingestion logic
# ---------------------------------------------------------------------------

async def _ingest_results(
    supabase_client: Client,
    crawl_results: List[Dict[str, Any]],
    crawl_type: str,
    chunk_size: int = 5000,
    batch_size: int = 20,
) -> Dict[str, Any]:
    """
    Process crawl results: chunk, embed, store docs and optional code examples.
    Returns a summary dict.
    """
    urls, chunk_numbers, contents, metadatas = [], [], [], []
    source_content_map: Dict[str, str] = {}
    source_word_counts: Dict[str, int] = {}

    for doc in crawl_results:
        src_url = doc["url"]
        md = doc["markdown"]
        parsed = urlparse(src_url)
        source_id = parsed.netloc or parsed.path

        if source_id not in source_content_map:
            source_content_map[source_id] = md[:5000]
            source_word_counts[source_id] = 0

        for i, chunk in enumerate(smart_chunk_markdown(md, chunk_size=chunk_size)):
            urls.append(src_url)
            chunk_numbers.append(i)
            contents.append(chunk)
            meta = extract_section_info(chunk)
            meta.update({"chunk_index": i, "url": src_url, "source": source_id, "crawl_type": crawl_type})
            metadatas.append(meta)
            source_word_counts[source_id] += meta.get("word_count", 0)

    url_to_full_doc = {doc["url"]: doc["markdown"] for doc in crawl_results}

    # Update source metadata first (FK constraint)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        args = list(source_content_map.items())
        summaries = list(ex.map(lambda a: extract_source_summary(a[0], a[1]), args))

    sources_ok = True
    for (source_id, _), summary in zip(args, summaries):
        if not update_source_info(supabase_client, source_id, summary, source_word_counts[source_id]):
            sources_ok = False

    chunks_stored, docs_delete_ok = add_documents_to_supabase(
        supabase_client, urls, chunk_numbers, contents, metadatas, url_to_full_doc,
        batch_size=batch_size,
    )

    # Optional: extract and store code examples
    code_stored, code_delete_ok = 0, True
    if os.getenv("USE_AGENTIC_RAG", "false") == "true":
        code_urls, code_chunk_numbers, code_examples, code_summaries, code_metas = [], [], [], [], []
        global_idx = 0

        for doc in crawl_results:
            src_url = doc["url"]
            blocks = extract_code_blocks(doc["markdown"])
            if not blocks:
                continue

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                block_summaries = list(ex.map(
                    _process_code_example,
                    [(b["code"], b["context_before"], b["context_after"]) for b in blocks],
                ))

            parsed = urlparse(src_url)
            source_id = parsed.netloc or parsed.path
            for b, summary in zip(blocks, block_summaries):
                code_urls.append(src_url)
                code_chunk_numbers.append(global_idx)
                code_examples.append(b["code"])
                code_summaries.append(summary)
                code_metas.append({
                    "chunk_index": global_idx,
                    "url": src_url,
                    "source": source_id,
                    "char_count": len(b["code"]),
                    "word_count": len(b["code"].split()),
                })
                global_idx += 1

        if code_examples:
            code_stored, code_delete_ok = add_code_examples_to_supabase(
                supabase_client, code_urls, code_chunk_numbers,
                code_examples, code_summaries, code_metas,
                batch_size=batch_size,
            )

    return {
        "sources_ok": sources_ok,
        "chunks_stored": chunks_stored,
        "code_examples_stored": code_stored,
        "delete_ok": docs_delete_ok and code_delete_ok,
        "sources_updated": len(source_content_map),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def crawl_single_page(ctx: Context, url: str) -> str:
    """
    Crawl a single web page and store its content in Supabase.

    Use this for quickly ingesting a specific URL without following any links.
    The content is chunked, embedded, and stored for later retrieval.

    Args:
        ctx: MCP server context
        url: URL of the web page to crawl
    """
    try:
        crawler = await _get_crawler()
        supabase_client = ctx.request_context.lifespan_context.supabase_client

        result = await crawler.arun(
            url=url,
            config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, stream=False),
        )

        if not result.success or not result.markdown:
            return json.dumps({"success": False, "url": url, "error": result.error_message}, indent=2)

        parsed = urlparse(url)
        source_id = parsed.netloc or parsed.path

        stats = _ingest_results(
            supabase_client,
            [{"url": url, "markdown": result.markdown}],
            crawl_type="single_page",
        )

        return json.dumps({
            "success": stats["sources_ok"] and stats["chunks_stored"] > 0,
            "url": url,
            "source_id": source_id,
            "chunks_stored": stats["chunks_stored"],
            "code_examples_stored": stats["code_examples_stored"],
            "delete_ok": stats["delete_ok"],
            "content_length": len(result.markdown),
            "links_count": {
                "internal": len(result.links.get("internal", [])),
                "external": len(result.links.get("external", [])),
            },
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "url": url, "error": str(e)}, indent=2)


@mcp.tool()
async def smart_crawl_url(
    ctx: Context,
    url: str,
    max_depth: int = 3,
    max_concurrent: int = 10,
    chunk_size: int = 5000,
) -> str:
    """
    Intelligently crawl a URL and store all content in Supabase.

    Auto-detects the URL type and applies the appropriate strategy:
    - sitemap.xml  → parse all URLs, crawl in parallel
    - *.txt        → fetch directly (e.g. llms.txt)
    - webpage      → recursively follow internal links up to max_depth

    Args:
        ctx: MCP server context
        url: URL to crawl
        max_depth: Maximum recursion depth for webpages (default: 3)
        max_concurrent: Maximum parallel browser sessions (default: 10)
        chunk_size: Maximum characters per content chunk (default: 5000)
    """
    try:
        crawler = await _get_crawler()
        supabase_client = ctx.request_context.lifespan_context.supabase_client

        if _is_txt(url):
            crawl_results = await _crawl_text_file(crawler, url)
            crawl_type = "text_file"
            if not crawl_results:
                return json.dumps({"success": False, "url": url, "error": "No content found"}, indent=2)
            stats = await _ingest_results(supabase_client, crawl_results, crawl_type, chunk_size=chunk_size)
            stats["pages_crawled"] = len(crawl_results)
        elif _is_sitemap(url):
            sitemap_urls = _parse_sitemap(url)
            if not sitemap_urls:
                return json.dumps({"success": False, "url": url, "error": "No URLs found in sitemap"}, indent=2)
            crawl_results = await _crawl_batch(crawler, sitemap_urls, max_concurrent=max_concurrent)
            crawl_type = "sitemap"
            if not crawl_results:
                return json.dumps({"success": False, "url": url, "error": "No content found"}, indent=2)
            stats = await _ingest_results(supabase_client, crawl_results, crawl_type, chunk_size=chunk_size)
            stats["pages_crawled"] = len(crawl_results)
        else:
            crawl_type = "webpage"
            stats = await _crawl_recursive(
                crawler, [url],
                supabase_client=supabase_client,
                crawl_type=crawl_type,
                max_depth=max_depth,
                max_concurrent=max_concurrent,
                chunk_size=chunk_size,
            )
            if stats["pages_crawled"] == 0:
                return json.dumps({"success": False, "url": url, "error": "No content found"}, indent=2)

        return json.dumps({
            "success": stats["sources_ok"] and stats["chunks_stored"] > 0,
            "url": url,
            "crawl_type": crawl_type,
            "pages_crawled": stats["pages_crawled"],
            "chunks_stored": stats["chunks_stored"],
            "code_examples_stored": stats["code_examples_stored"],
            "delete_ok": stats["delete_ok"],
            "sources_updated": stats["sources_updated"],
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "url": url, "error": str(e)}, indent=2)


@mcp.tool()
async def get_available_sources(ctx: Context) -> str:
    """
    List all documentation sources that have been crawled and stored.

    Returns source IDs (domains), their summaries, word counts, and timestamps.
    Use this before deciding what to crawl or delete.
    """
    try:
        supabase_client = ctx.request_context.lifespan_context.supabase_client
        result = supabase_client.from_("sources").select("*").order("source_id").execute()

        sources = [
            {
                "source_id": s.get("source_id"),
                "summary": s.get("summary"),
                "total_words": s.get("total_words"),
                "created_at": s.get("created_at"),
                "updated_at": s.get("updated_at"),
            }
            for s in (result.data or [])
        ]

        return json.dumps({"success": True, "sources": sources, "count": len(sources)}, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
async def delete_source(ctx: Context, source_id: str) -> str:
    """
    Delete a source and all its associated content from the database.

    Removes all crawled page chunks, code examples, and the source record
    for the given source_id (domain). This operation is irreversible.

    Use get_available_sources to find valid source_id values.

    Args:
        ctx: MCP server context
        source_id: The source domain to delete (e.g. 'docs.example.com')
    """
    try:
        supabase_client = ctx.request_context.lifespan_context.supabase_client
        deleted = {}

        # Delete crawled page chunks
        pages_result = (
            supabase_client.table("crawled_pages")
            .delete()
            .eq("source_id", source_id)
            .execute()
        )
        deleted["pages"] = len(pages_result.data) if pages_result.data else 0

        # Delete code examples
        code_result = (
            supabase_client.table("code_examples")
            .delete()
            .eq("source_id", source_id)
            .execute()
        )
        deleted["code_examples"] = len(code_result.data) if code_result.data else 0

        # Delete source record
        source_result = (
            supabase_client.table("sources")
            .delete()
            .eq("source_id", source_id)
            .execute()
        )
        deleted["source_record"] = len(source_result.data) if source_result.data else 0

        found = deleted["source_record"] > 0

        return json.dumps({
            "success": True,
            "source_id": source_id,
            "found": found,
            "deleted": deleted,
        }, indent=2)

    except Exception as e:
        return json.dumps({"success": False, "source_id": source_id, "error": str(e)}, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    transport = os.getenv("TRANSPORT", "stdio")
    if transport == "sse":
        await mcp.run_sse_async()
    else:
        await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
