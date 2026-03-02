## This query is saved on the supabase dita-specs db to get the url groups
It can be used by the get_available_tool:

```python
def get_available_sources():
    # We call the 'shortcut' we created in the database
    response = supabase.rpc('get_spec_collections').execute()
    
    if response.data:
        # response.data will look like: 
        # [{'source_id': 'dita-lang.org', 'url_prefix': 'https://dita-lang.org/dita', 'file_count': 850}, ...]
        return response.data
    return []
```

```SQL    
CREATE OR REPLACE FUNCTION get_spec_collections()
RETURNS TABLE (
    source_id TEXT,
    url_prefix TEXT,
    file_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        c.source_id::TEXT, 
        COALESCE(
            -- This regex captures up to TWO levels if they exist (e.g., /dita/langRef)
            -- It stops before the 3rd slash to keep categories broad but useful
            substring(url from '^(?:[a-z]+://[^/]+)?(/[^/?#]+(?:/[^/?#]+)?)'),
            '/'
        ) AS url_prefix,
        COUNT(*) AS file_count
    FROM public.crawled_pages c
    GROUP BY c.source_id, url_prefix
    HAVING COUNT(*) > 0
    ORDER BY c.source_id, url_prefix;
END;
$$ LANGUAGE plpgsql;
```