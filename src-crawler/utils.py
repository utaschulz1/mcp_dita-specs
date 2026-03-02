"""
Utility functions for mcp-crawl4ai2vectordb.
Ingestion-only: embedding, chunking, Supabase writes, source management.
"""
import os
import concurrent.futures
import time
import re
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

import openai
from supabase import create_client, Client

openai.api_key = os.getenv("OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def create_embeddings_batch(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    max_retries = 3
    delay = 1.0

    for attempt in range(max_retries):
        try:
            response = openai.embeddings.create(
                model="text-embedding-3-small",
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Batch embedding attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {delay}s…")
                time.sleep(delay)
                delay *= 2
            else:
                print(f"Batch embedding failed after {max_retries} attempts: {e}. Falling back to individual calls.")
                embeddings = []
                for i, text in enumerate(texts):
                    try:
                        r = openai.embeddings.create(model="text-embedding-3-small", input=[text])
                        embeddings.append(r.data[0].embedding)
                    except Exception as ie:
                        print(f"Individual embedding {i} failed: {ie}. Using zero vector.")
                        embeddings.append([0.0] * 1536)
                return embeddings


def create_embedding(text: str) -> List[float]:
    result = create_embeddings_batch([text])
    return result[0] if result else [0.0] * 1536


# ---------------------------------------------------------------------------
# Contextual embeddings (optional, USE_CONTEXTUAL_EMBEDDINGS=false for DITA specs)
# ---------------------------------------------------------------------------

def generate_contextual_embedding(full_document: str, chunk: str) -> Tuple[str, bool]:
    model = os.getenv("MODEL_CHOICE")
    prompt = f"""<document>
{full_document[:25000]}
</document>
Here is the chunk we want to situate within the whole document
<chunk>
{chunk}
</chunk>
Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Answer only with the succinct context and nothing else."""

    try:
        response = openai.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides concise contextual information."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        context = response.choices[0].message.content.strip()
        return f"{context}\n---\n{chunk}", True
    except Exception as e:
        print(f"Contextual embedding failed: {e}. Using original chunk.")
        return chunk, False


def _process_chunk_with_context(args: Tuple[str, str, str]) -> Tuple[str, bool]:
    _, content, full_document = args
    return generate_contextual_embedding(full_document, content)


# ---------------------------------------------------------------------------
# Chunking (adjusted to small 30 for dita specs)
# ---------------------------------------------------------------------------

def smart_chunk_markdown(text: str, chunk_size: int = 30) -> List[str]:
    """Split markdown into chunks, respecting code blocks and paragraphs."""
    chunks = []
    start = 0
    length = len(text)

    while start < length:
        end = start + chunk_size
        if end >= length:
            chunks.append(text[start:].strip())
            break

        chunk = text[start:end]

        # Prefer breaking at a code fence
        code_pos = chunk.rfind("```")
        if code_pos != -1 and code_pos > chunk_size * 0.3:
            end = start + code_pos
        elif "\n\n" in chunk:
            last_break = chunk.rfind("\n\n")
            if last_break > chunk_size * 0.3:
                end = start + last_break
        elif ". " in chunk:
            last_period = chunk.rfind(". ")
            if last_period > chunk_size * 0.3:
                end = start + last_period + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end

    return chunks


def extract_section_info(chunk: str) -> Dict[str, Any]:
    headers = re.findall(r"^(#+)\s+(.+)$", chunk, re.MULTILINE)
    return {
        "headers": "; ".join(f"{h[0]} {h[1]}" for h in headers),
        "char_count": len(chunk),
        "word_count": len(chunk.split()),
    }


# ---------------------------------------------------------------------------
# Code block extraction (optional, USE_AGENTIC_RAG=true) (adjusted to smaller context window and DITA specific prompt)
# ---------------------------------------------------------------------------

def extract_code_blocks(markdown_content: str, min_length: int = 1000) -> List[Dict[str, Any]]:
    content = markdown_content.strip()
    start_offset = 3 if content.startswith("```") else 0

    positions = []
    pos = start_offset
    while True:
        pos = markdown_content.find("```", pos)
        if pos == -1:
            break
        positions.append(pos)
        pos += 3

    blocks = []
    i = 0
    while i < len(positions) - 1:
        start_pos = positions[i]
        end_pos = positions[i + 1]
        section = markdown_content[start_pos + 3:end_pos]

        lines = section.split("\n", 1)
        if len(lines) > 1:
            first = lines[0].strip()
            if first and " " not in first and len(first) < 20:
                language = first
                code = lines[1].strip()
            else:
                language = ""
                code = section.strip()
        else:
            language = ""
            code = section.strip()

        if len(code) < min_length:
            i += 2
            continue

        ctx_before = markdown_content[max(0, start_pos - 1000):start_pos].strip()
        ctx_after = markdown_content[end_pos + 3:min(len(markdown_content), end_pos + 3 + 1000)].strip()

        blocks.append({
            "code": code,
            "language": language,
            "context_before": ctx_before,
            "context_after": ctx_after,
        })
        i += 2

    return blocks


def generate_code_example_summary(code: str, context_before: str, context_after: str) -> str:
    model = os.getenv("MODEL_CHOICE")
    prompt = f"""<context_before>
{context_before[-200:]}
</context_before>

<code_example>
{code[:50]}
</code_example>

<context_after>
{context_after[:200]}
</context_after>

Based on the DITA XML example and its surrounding context, write a 2-3 sentence summary that:
- Names the DITA element(s) and key attributes shown
- Explains the authoring scenario or output behavior this demonstrates
- Uses DITA terminology (topic type, map, specialization, etc.) where relevant"""

    try:
        response = openai.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides concise code example summaries."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=100,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Code summary generation failed: {e}.")
        return "Code example for demonstration purposes."


def _process_code_example(args: Tuple[str, str, str]) -> str:
    code, ctx_before, ctx_after = args
    return generate_code_example_summary(code, ctx_before, ctx_after)


# ---------------------------------------------------------------------------
# Source management
# ---------------------------------------------------------------------------

def extract_source_summary(source_id: str, content: str, max_length: int = 500) -> str:
    default = f"Content from {source_id}"
    if not content or not content.strip():
        return default

    model = os.getenv("MODEL_CHOICE")
    truncated = content[:25000]
    prompt = f"""<source_content>
{truncated}
</source_content>

The above content is from the documentation for '{source_id}'. Please provide a concise summary (3-5 sentences) describing what this library/tool/framework is about and its purpose."""

    try:
        response = openai.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides concise library/tool/framework summaries."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=150,
        )
        summary = response.choices[0].message.content.strip()
        return summary[:max_length] + "..." if len(summary) > max_length else summary
    except Exception as e:
        print(f"Source summary generation failed for {source_id}: {e}.")
        return default


def update_source_info(client: Client, source_id: str, summary: str, word_count: int) -> bool:
    try:
        result = client.table("sources").update({
            "summary": summary,
            "total_word_count": word_count,
            "updated_at": "now()",
        }).eq("source_id", source_id).execute()

        if not result.data:
            client.table("sources").insert({
                "source_id": source_id,
                "summary": summary,
                "total_word_count": word_count,
            }).execute()
            print(f"Created new source: {source_id}")
        else:
            print(f"Updated source: {source_id}")
        return True
    except Exception as e:
        print(f"Error updating source {source_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Supabase writes
# ---------------------------------------------------------------------------

def add_documents_to_supabase(
    client: Client,
    urls: List[str],
    chunk_numbers: List[int],
    contents: List[str],
    metadatas: List[Dict[str, Any]],
    url_to_full_document: Dict[str, str],
    batch_size: int = 20,
) -> Tuple[int, bool]:
    unique_urls = list(set(urls))
    delete_ok = True

    try:
        client.table("crawled_pages").delete().in_("url", unique_urls).execute()
    except Exception as e:
        print(f"Batch delete failed: {e}. Trying one-by-one.")
        for url in unique_urls:
            try:
                client.table("crawled_pages").delete().eq("url", url).execute()
            except Exception as ie:
                print(f"Delete failed for {url}: {ie}")
                delete_ok = False

    use_contextual = os.getenv("USE_CONTEXTUAL_EMBEDDINGS", "false") == "true"
    print(f"\nUse contextual embeddings: {use_contextual}\n")

    successful = 0

    for i in range(0, len(contents), batch_size):
        end = min(i + batch_size, len(contents))
        b_urls = urls[i:end]
        b_chunks = chunk_numbers[i:end]
        b_contents = contents[i:end]
        b_metas = list(metadatas[i:end])

        if use_contextual:
            args = [(url, content, url_to_full_document.get(url, ""))
                    for url, content in zip(b_urls, b_contents)]
            ctx_contents = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
                future_map = {ex.submit(_process_chunk_with_context, arg): idx
                              for idx, arg in enumerate(args)}
                results_map = {}
                for future in concurrent.futures.as_completed(future_map):
                    idx = future_map[future]
                    try:
                        text, success = future.result()
                        results_map[idx] = text
                        if success:
                            b_metas[idx]["contextual_embedding"] = True
                    except Exception as e:
                        print(f"Context processing failed for chunk {idx}: {e}")
                        results_map[idx] = b_contents[idx]
            ctx_contents = [results_map[j] for j in range(len(b_contents))]
        else:
            ctx_contents = b_contents

        embeddings = create_embeddings_batch(ctx_contents)

        batch_data = []
        for j, (url, chunk_num, content, meta, emb) in enumerate(
            zip(b_urls, b_chunks, ctx_contents, b_metas, embeddings)
        ):
            parsed = urlparse(url)
            source_id = parsed.netloc or parsed.path
            batch_data.append({
                "url": url,
                "chunk_number": chunk_num,
                "content": content,
                "metadata": {"chunk_size": len(content), **meta},
                "source_id": source_id,
                "embedding": emb,
            })

        delay = 1.0
        for attempt in range(3):
            try:
                client.table("crawled_pages").insert(batch_data).execute()
                successful += len(batch_data)
                break
            except Exception as e:
                if attempt < 2:
                    print(f"Insert attempt {attempt + 1}/3 failed: {e}. Retrying in {delay}s…")
                    time.sleep(delay)
                    delay *= 2
                else:
                    print(f"Batch insert failed after 3 attempts. Trying individually…")
                    for record in batch_data:
                        try:
                            client.table("crawled_pages").insert(record).execute()
                            successful += 1
                        except Exception as ie:
                            print(f"Individual insert failed for {record['url']}: {ie}")

    return successful, delete_ok


def add_code_examples_to_supabase(
    client: Client,
    urls: List[str],
    chunk_numbers: List[int],
    code_examples: List[str],
    summaries: List[str],
    metadatas: List[Dict[str, Any]],
    batch_size: int = 20,
) -> Tuple[int, bool]:
    if not urls:
        return 0, True

    delete_ok = True
    for url in set(urls):
        try:
            client.table("code_examples").delete().eq("url", url).execute()
        except Exception as e:
            print(f"Delete code examples failed for {url}: {e}")
            delete_ok = False

    successful = 0
    total = len(urls)

    for i in range(0, total, batch_size):
        end = min(i + batch_size, total)
        texts = [f"{code_examples[j]}\n\nSummary: {summaries[j]}" for j in range(i, end)]
        embeddings = create_embeddings_batch(texts)

        # Validate embeddings — replace zero vectors
        valid_embeddings = []
        for k, emb in enumerate(embeddings):
            if emb and not all(v == 0.0 for v in emb):
                valid_embeddings.append(emb)
            else:
                print(f"Zero embedding at index {k}, regenerating…")
                valid_embeddings.append(create_embedding(texts[k]))

        batch_data = []
        for j, emb in enumerate(valid_embeddings):
            idx = i + j
            parsed = urlparse(urls[idx])
            source_id = parsed.netloc or parsed.path
            batch_data.append({
                "url": urls[idx],
                "chunk_number": chunk_numbers[idx],
                "content": code_examples[idx],
                "summary": summaries[idx],
                "metadata": metadatas[idx],
                "source_id": source_id,
                "embedding": emb,
            })

        delay = 1.0
        for attempt in range(3):
            try:
                client.table("code_examples").insert(batch_data).execute()
                successful += len(batch_data)
                break
            except Exception as e:
                if attempt < 2:
                    print(f"Code examples insert attempt {attempt + 1}/3 failed: {e}. Retrying in {delay}s…")
                    time.sleep(delay)
                    delay *= 2
                else:
                    print(f"Batch insert failed after 3 attempts. Trying individually…")
                    for record in batch_data:
                        try:
                            client.table("code_examples").insert(record).execute()
                            successful += 1
                        except Exception as ie:
                            print(f"Individual insert failed for {record['url']}: {ie}")

        print(f"Inserted code examples batch {i // batch_size + 1} of {(total + batch_size - 1) // batch_size}")

    return successful, delete_ok
