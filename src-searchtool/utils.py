"""
Utility functions for the DITA Specs MCP server.
"""
import os
import time
from typing import List, Dict, Any, Optional
from supabase import create_client, Client
import openai

# Load OpenAI API key for embeddings
openai.api_key = os.getenv("OPENAI_API_KEY")


def get_supabase_client() -> Client:
    """
    Get a Supabase client with the URL and key from environment variables.

    Returns:
        Supabase client instance
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment variables")

    return create_client(url, key)


def create_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Create embeddings for multiple texts in a single API call.

    Args:
        texts: List of texts to create embeddings for

    Returns:
        List of embeddings (each embedding is a list of floats)
    """
    if not texts:
        return []

    max_retries = 3
    retry_delay = 1.0

    for retry in range(max_retries):
        try:
            response = openai.embeddings.create(
                model="text-embedding-3-small",
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            if retry < max_retries - 1:
                print(f"Error creating batch embeddings (attempt {retry + 1}/{max_retries}): {e}")
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                print(f"Failed to create batch embeddings after {max_retries} attempts: {e}")
                print("Attempting to create embeddings individually...")
                embeddings = []
                successful_count = 0

                for i, text in enumerate(texts):
                    try:
                        individual_response = openai.embeddings.create(
                            model="text-embedding-3-small",
                            input=[text]
                        )
                        embeddings.append(individual_response.data[0].embedding)
                        successful_count += 1
                    except Exception as individual_error:
                        print(f"Failed to create embedding for text {i}: {individual_error}")
                        embeddings.append([0.0] * 1536)

                print(f"Successfully created {successful_count}/{len(texts)} embeddings individually")
                return embeddings


def create_embedding(text: str) -> List[float]:
    """
    Create an embedding for a single text using OpenAI's API.

    Args:
        text: Text to create an embedding for

    Returns:
        List of floats representing the embedding
    """
    try:
        embeddings = create_embeddings_batch([text])
        return embeddings[0] if embeddings else [0.0] * 1536
    except Exception as e:
        print(f"Error creating embedding: {e}")
        return [0.0] * 1536


def search_documents(
    client: Client,
    query: str,
    match_count: int = 10,
    filter_metadata: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Search for documents in Supabase using vector similarity.

    Args:
        client: Supabase client
        query: Query text
        match_count: Maximum number of results to return
        filter_metadata: Optional metadata filter

    Returns:
        List of matching documents
    """
    query_embedding = create_embedding(query)

    try:
        params = {
            'query_embedding': query_embedding,
            'match_count': match_count
        }

        if filter_metadata:
            params['filter'] = filter_metadata

        result = client.rpc('match_crawled_pages', params).execute()

        return result.data
    except Exception as e:
        print(f"Error searching documents: {e}")
        return []


def search_code_examples(
    client: Client,
    query: str,
    match_count: int = 10,
    filter_metadata: Optional[Dict[str, Any]] = None,
    source_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search for code examples in Supabase using vector similarity.

    Args:
        client: Supabase client
        query: Query text
        match_count: Maximum number of results to return
        filter_metadata: Optional metadata filter
        source_id: Optional source ID to filter results

    Returns:
        List of matching code examples
    """
    enhanced_query = f"Code example for {query}\n\nSummary: Example code showing {query}"
    query_embedding = create_embedding(enhanced_query)

    try:
        params = {
            'query_embedding': query_embedding,
            'match_count': match_count
        }

        if filter_metadata:
            params['filter'] = filter_metadata

        if source_id:
            params['source_filter'] = source_id

        result = client.rpc('match_code_examples', params).execute()

        return result.data
    except Exception as e:
        print(f"Error searching code examples: {e}")
        return []
