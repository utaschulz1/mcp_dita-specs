"""
DITA Specs MCP Server — retrieval-only.

Provides semantic search, keyword search, code-example search, and direct content
retrieval over DITA specification pages stored in Supabase.

"""

from mcp.server.fastmcp import FastMCP, Context
from sentence_transformers import CrossEncoder
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from supabase import Client
from pathlib import Path
import asyncio
import json
import os

from utils import (
    get_supabase_client,
    search_documents,
    search_code_examples as _search_code_examples,
)

# Load environment variables from the project root .env file
project_root = Path(__file__).resolve().parent.parent
dotenv_path = project_root / '.env'
load_dotenv(dotenv_path, override=True)

# Load cross-encoder model once at module level
_reranking_model = None
if os.getenv("USE_RERANKING", "false") == "true":
    try:
        _reranking_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        print("Cross-encoder reranking model loaded")
    except Exception as e:
        print(f"Failed to load reranking model: {e}")
        _reranking_model = None

# Initialize Supabase client once at module level (stateless HTTP client, safe to share)
_supabase_client = get_supabase_client()


@dataclass
class DitaSpecsContext:
    """Context for the DITA Specs MCP server."""
    supabase_client: Client
    reranking_model: Optional[CrossEncoder] = None


@asynccontextmanager
async def dita_specs_lifespan(server: FastMCP) -> AsyncIterator[DitaSpecsContext]:
    """
    Manages the DITA Specs server lifecycle.
    Singletons are module-level; no startup/teardown side-effects needed.
    """
    yield DitaSpecsContext(
        supabase_client=_supabase_client,
        reranking_model=_reranking_model,
    )


mcp = FastMCP(
    "dita-specs-rag",
    description=(
        "DITA specification retrieval server. Searches DITA spec content indexed in Supabase.\n\n"
        "Known source_id values (for source filtering):\n"
        "  - dita-lang.org  (DITA Language Reference, DITA Architecture Spec)\n"
        "  - dita-ot.org    (DITA Open Toolkit documentation)\n\n"
        "Recommended retrieval patterns:\n"
        "  - Free-text semantic search over spec content → perform_rag_query\n"
        "  - XML markup examples for a specific element → search_code_examples\n"
        "  - Discover available sub-collections → get_available_sources\n"
        "  - List all pages in a sub-collection → list_spec_files (URL paths are self-describing)\n"
        "  - Fetch full spec page content by known URL → retrieve_content_by_links (max 10 URLs)\n"
    ),
    lifespan=dita_specs_lifespan,
    host=os.getenv("HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", "8051")),
)


def rerank_results(
    model: CrossEncoder,
    query: str,
    results: List[Dict[str, Any]],
    content_key: str = "content",
) -> List[Dict[str, Any]]:
    """Rerank search results using a cross-encoder model."""
    if not model or not results:
        return results

    try:
        texts = [result.get(content_key, "") for result in results]
        pairs = [[query, text] for text in texts]
        scores = model.predict(pairs)

        for i, result in enumerate(results):
            result["rerank_score"] = float(scores[i])

        return sorted(results, key=lambda x: x.get("rerank_score", 0), reverse=True)
    except Exception as e:
        print(f"Error during reranking: {e}")
        return results


@mcp.tool()
async def get_available_sources(ctx: Context) -> str:
    """
    Discover all DITA spec sub-collections indexed in the database.

    Returns entries at sub-collection granularity (finer than just domain), so
    "dita-lang.org/dita/archspecs" and "dita-lang.org/1.3" appear as separate entries.

    Each entry contains:
      - label:      Human-readable name, e.g. "dita-lang.org/dita/archspecs"
      - source_id:  Domain only, e.g. "dita-lang.org" — pass to perform_rag_query
                    or search_code_examples as the source filter
      - url_prefix: Full URL prefix, e.g. "https://dita-lang.org/dita/archspecs" —
                    pass to list_spec_files
      - file_count: Number of distinct spec pages in this sub-collection

    Call this first to orient yourself before searching or browsing spec files.
    """
    try:
        supabase_client = ctx.request_context.lifespan_context.supabase_client

        response = supabase_client.rpc('get_specs_collections').execute()

        collections = []
        if response.data:
            for row in response.data:
                # RPC returns a relative path (e.g. "/1.3/dita"), not a full URL
                url_prefix_path = row.get('url_prefix', '')
                source_id = row.get('source_id', '')
                file_count = row.get('file_count', 0)

                path = url_prefix_path.rstrip('/')
                label = f"{source_id}{path}" if path and path != '/' else f"{source_id}/(root)"
                full_url_prefix = f"https://{source_id}{path}" if path and path != '/' else f"https://{source_id}"

                collections.append({
                    "label": label,
                    "source_id": source_id,
                    "url_prefix": full_url_prefix,
                    "file_count": file_count,
                })

        return json.dumps({
            "success": True,
            "collections": collections,
            "count": len(collections),
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
async def perform_rag_query(
    ctx: Context,
    query: str,
    source: str = None,
    match_count: int = 5,
) -> str:
    """
    Perform a RAG (Retrieval Augmented Generation) query on DITA spec content.

    Searches crawled spec pages using vector similarity. With USE_HYBRID_SEARCH=true,
    also runs a keyword scan and boosts results appearing in both lists.
    With USE_RERANKING=true, applies a cross-encoder to re-score results.

    Use get_available_sources to discover valid source_id values for filtering.

    Args:
        query:       The search query, e.g. "content model for topic element"
        source:      Optional source_id filter, e.g. "dita-lang.org"
        match_count: Maximum results to return (default 5)

    Returns:
        JSON with matching content chunks, their URLs, similarity scores, and metadata.
    """
    try:
        supabase_client = ctx.request_context.lifespan_context.supabase_client
        use_keyword_search = os.getenv("USE_KEYWORD_SEARCH", "false") == "true"
        use_hybrid_search = os.getenv("USE_HYBRID_SEARCH", "false") == "true" and not use_keyword_search

        filter_metadata = None
        if source and source.strip():
            filter_metadata = {"source": source}

        if use_keyword_search:
            keyword_query = (
                supabase_client.from_('crawled_pages')
                .select('id, url, chunk_number, content, metadata, source_id')
                .ilike('content', f'%{query}%')
            )
            if source and source.strip():
                keyword_query = keyword_query.eq('source_id', source)
            keyword_response = keyword_query.limit(match_count * 3).execute()
            results = [
                {**row, 'similarity': None}
                for row in (keyword_response.data or [])
            ]
        elif use_hybrid_search:
            vector_results = search_documents(
                client=supabase_client,
                query=query,
                match_count=match_count * 3,
                filter_metadata=filter_metadata,
            )

            keyword_query = (
                supabase_client.from_('crawled_pages')
                .select('id, url, chunk_number, content, metadata, source_id')
                .ilike('content', f'%{query}%')
            )
            if source and source.strip():
                keyword_query = keyword_query.eq('source_id', source)
            keyword_response = keyword_query.limit(match_count * 3).execute()
            keyword_results = keyword_response.data if keyword_response.data else []

            seen_ids = set()
            combined_results = []

            vector_ids = {r.get('id') for r in vector_results if r.get('id')}
            for kr in keyword_results:
                if kr['id'] in vector_ids and kr['id'] not in seen_ids:
                    for vr in vector_results:
                        if vr.get('id') == kr['id']:
                            vr['similarity'] = min(1.0, vr.get('similarity', 0) * 1.2)
                            combined_results.append(vr)
                            seen_ids.add(kr['id'])
                            break

            for vr in vector_results:
                if vr.get('id') and vr['id'] not in seen_ids and len(combined_results) < match_count:
                    combined_results.append(vr)
                    seen_ids.add(vr['id'])

            for kr in keyword_results:
                if kr['id'] not in seen_ids and len(combined_results) < match_count:
                    combined_results.append({
                        'id': kr['id'],
                        'url': kr['url'],
                        'chunk_number': kr['chunk_number'],
                        'content': kr['content'],
                        'metadata': kr['metadata'],
                        'source_id': kr['source_id'],
                        'similarity': 0.5,
                    })
                    seen_ids.add(kr['id'])

            results = combined_results[:match_count]
        else:
            use_reranking = os.getenv("USE_RERANKING", "false") == "true"
            # Fetch extra candidates when reranking so the reranker sees a wider pool
            fetch_count = match_count * 3 if use_reranking else match_count
            results = search_documents(
                client=supabase_client,
                query=query,
                match_count=fetch_count,
                filter_metadata=filter_metadata,
            )

        use_reranking = os.getenv("USE_RERANKING", "false") == "true"
        if use_reranking and ctx.request_context.lifespan_context.reranking_model:
            results = rerank_results(
                ctx.request_context.lifespan_context.reranking_model,
                query,
                results,
                content_key="content",
            )
            results = results[:match_count]

        formatted_results = []
        for result in results:
            formatted_result = {
                "url": result.get("url"),
                "content": result.get("content"),
                "metadata": result.get("metadata"),
                "similarity": result.get("similarity"),
            }
            if "rerank_score" in result:
                formatted_result["rerank_score"] = result["rerank_score"]
            if result.get("rerank_score", 0) >= -5:
                formatted_results.append(formatted_result)

        return json.dumps({
            "success": True,
            "query": query,
            "source_filter": source,
            "search_mode": "keyword" if use_keyword_search else ("hybrid" if use_hybrid_search else "vector"),
            "reranking_applied": (
                use_reranking
                and ctx.request_context.lifespan_context.reranking_model is not None
            ),
            "results": formatted_results,
            "count": len(formatted_results),
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "query": query, "error": str(e)}, indent=2)


@mcp.tool()
async def search_code_examples(
    ctx: Context,
    query: str,
    source_id: str = None,
    match_count: int = 5,
) -> str:
    """
    Search for DITA XML markup examples relevant to the query.

    Queries the code_examples table, which contains XML blocks extracted during
    agentic ingestion and embedded with richer contextual summaries. This is the
    best tool for questions like "show me how to write a valid DITA <topic> element".
    May return empty results if agentic ingestion was not used for a source.

    Use get_available_sources to discover valid source_id values for filtering.

    Args:
        query:       The search query, e.g. "topic element with title and shortdesc"
        source_id:   Optional source filter, e.g. "dita-lang.org"
        match_count: Maximum results to return (default 5)

    Returns:
        JSON with matching code examples and their summaries.
    """
    try:
        supabase_client = ctx.request_context.lifespan_context.supabase_client
        use_hybrid_search = os.getenv("USE_HYBRID_SEARCH", "false") == "true"

        filter_metadata = None
        if source_id and source_id.strip():
            filter_metadata = {"source": source_id}

        if use_hybrid_search:
            vector_results = _search_code_examples(
                client=supabase_client,
                query=query,
                match_count=match_count * 2,
                filter_metadata=filter_metadata,
                source_id=source_id,
            )

            keyword_query = (
                supabase_client.from_('code_examples')
                .select('id, url, chunk_number, content, summary, metadata, source_id')
                .or_(f'content.ilike.%{query}%,summary.ilike.%{query}%')
            )
            if source_id and source_id.strip():
                keyword_query = keyword_query.eq('source_id', source_id)
            keyword_response = keyword_query.limit(match_count * 2).execute()
            keyword_results = keyword_response.data if keyword_response.data else []

            seen_ids = set()
            combined_results = []

            vector_ids = {r.get('id') for r in vector_results if r.get('id')}
            for kr in keyword_results:
                if kr['id'] in vector_ids and kr['id'] not in seen_ids:
                    for vr in vector_results:
                        if vr.get('id') == kr['id']:
                            vr['similarity'] = min(1.0, vr.get('similarity', 0) * 1.2)
                            combined_results.append(vr)
                            seen_ids.add(kr['id'])
                            break

            for vr in vector_results:
                if vr.get('id') and vr['id'] not in seen_ids and len(combined_results) < match_count:
                    combined_results.append(vr)
                    seen_ids.add(vr['id'])

            for kr in keyword_results:
                if kr['id'] not in seen_ids and len(combined_results) < match_count:
                    combined_results.append({
                        'id': kr['id'],
                        'url': kr['url'],
                        'chunk_number': kr['chunk_number'],
                        'content': kr['content'],
                        'summary': kr.get('summary', ''),
                        'metadata': kr['metadata'],
                        'source_id': kr['source_id'],
                        'similarity': 0.5,
                    })
                    seen_ids.add(kr['id'])

            results = combined_results[:match_count]
        else:
            results = _search_code_examples(
                client=supabase_client,
                query=query,
                match_count=match_count,
                filter_metadata=filter_metadata,
                source_id=source_id,
            )

        use_reranking = os.getenv("USE_RERANKING", "false") == "true"
        if use_reranking and ctx.request_context.lifespan_context.reranking_model:
            results = rerank_results(
                ctx.request_context.lifespan_context.reranking_model,
                query,
                results,
                content_key="content",
            )

        formatted_results = []
        for result in results:
            formatted_result = {
                "url": result.get("url"),
                "content": result.get("content"),
                "summary": result.get("summary"),
                "similarity": result.get("similarity"),
            }
            if "rerank_score" in result:
                formatted_result["rerank_score"] = result["rerank_score"]
            if result.get("rerank_score", 0) >= -5:
                formatted_results.append(formatted_result)

        return json.dumps({
            "success": True,
            "query": query,
            "source_filter": source_id,
            "search_mode": "hybrid" if use_hybrid_search else "vector",
            "reranking_applied": (
                use_reranking
                and ctx.request_context.lifespan_context.reranking_model is not None
            ),
            "results": formatted_results,
            "count": len(formatted_results),
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "query": query, "error": str(e)}, indent=2)


@mcp.tool()
async def list_spec_files(ctx: Context, source_id: str, url_prefix: str) -> str:
    """
    List all spec page URLs for a given sub-collection.

    Obtain valid source_id and url_prefix values from get_available_sources first.
    URL paths are self-describing — use them to prioritize which pages to fetch
    with retrieve_content_by_links without needing to read every page.

    Args:
        source_id:  Domain filter, e.g. "dita-lang.org"
        url_prefix: URL prefix filter, e.g. "https://dita-lang.org/dita/archspecs"

    Returns:
        JSON with a sorted list of all distinct spec page URLs in the sub-collection.
    """
    if not source_id or not source_id.strip():
        return json.dumps({"success": False, "error": "source_id is required"}, indent=2)
    if not url_prefix or not url_prefix.strip():
        return json.dumps({"success": False, "error": "url_prefix is required"}, indent=2)

    try:
        supabase_client = ctx.request_context.lifespan_context.supabase_client

        # Normalize: ensure https and no trailing slash
        prefix = url_prefix.strip().rstrip('/')
        if prefix.startswith('http://'):
            prefix = 'https://' + prefix[7:]

        response = (
            supabase_client
            .from_('crawled_pages')
            .select('url')
            .eq('source_id', source_id)
            .ilike('url', f'{prefix}%')
            .execute()
        )

        # Deduplicate (multiple chunks per URL) and sort
        urls = sorted(set(row['url'] for row in response.data)) if response.data else []

        return json.dumps({
            "success": True,
            "source_id": source_id,
            "url_prefix": url_prefix,
            "urls": urls,
            "count": len(urls),
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
async def retrieve_content_by_links(ctx: Context, urls: List[str]) -> str:
    """
    Retrieve full content of specific spec pages by their exact URLs.

    Fetches all stored chunks for each URL and concatenates them in order into a
    single string per page. URLs must be exact matches — obtain them from
    list_spec_files or from the url field in perform_rag_query results.

    Maximum 10 URLs per call. Be selective: use perform_rag_query or the
    self-describing URL paths from list_spec_files to identify the most relevant
    pages. If the first batch does not answer the question, make a new refined
    request (still max 10 URLs).

    Args:
        urls: List of exact spec page URLs to retrieve (max 10)

    Returns:
        JSON with full concatenated content per URL, plus any URLs with no stored content.
    """
    if not urls:
        return json.dumps({"success": False, "error": "urls must be a non-empty list"}, indent=2)

    if len(urls) > 10:
        return json.dumps({
            "success": False,
            "error": (
                f"Too many URLs ({len(urls)}). Maximum is 10 per call. "
                "Be selective — choose the most relevant URLs from list_spec_files "
                "or perform_rag_query results and refine in subsequent calls if needed."
            ),
        }, indent=2)

    try:
        supabase_client = ctx.request_context.lifespan_context.supabase_client

        results = []
        not_found = []

        for url in urls:
            response = (
                supabase_client
                .from_('crawled_pages')
                .select('url, chunk_number, content')
                .ilike('url', url)
                .order('chunk_number')
                .execute()
            )

            if not response.data:
                not_found.append(url)
            else:
                content = '\n\n'.join(row['content'] for row in response.data)
                results.append({"url": url, "content": content})

        return json.dumps({
            "success": True,
            "results": results,
            "not_found": not_found,
            "count": len(results),
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


async def main():
    transport = os.getenv("TRANSPORT", "sse")
    if transport == 'sse':
        await mcp.run_sse_async()
    else:
        await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
