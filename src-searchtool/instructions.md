# Instruction for claude code
## Goal
I have a supabase db with dita specs. it contains many small files; right now, 6.300 (113MB), but there will be more.
I want to build an mcp server to query this database.
I want to take the files in the current folder as a partial example for this mcp server.

### tools
The new mcp server should have the tools:
**`get_available_sources`**: Get a list of all available sources (domains) in the database
**`perform_rag_query`**: Search for relevant content using semantic search with optional source filtering
**`retrieve-content-by-links`** (not yet existing): When Claude uses its dita SKILL and finds out that it needs info from the dita-specs server it has the option to search with free word query or it can read the list with all files present in the db (equivalent to url) and retrieve the content of these files. 

## TODO
[ ] Read the files in the current folder:
 /home/hpz440/projects/mcp_dita-specs/src-searchtool/crawl4ai_mcp.py
 /home/hpz440/projects/mcp_dita-specs/src-searchtool/utils.py
 /home/hpz440/projects/mcp_dita-specs/src-searchtool/dita-specs-db_schema.md

[ ] explain what `get_available_sources` and `perform_rag_query` do in detail, take the referenced configs into consideration:
# USE_HYBRID_SEARCH: Combines vector similarity search with keyword search for better results
USE_HYBRID_SEARCH=true

# USE_AGENTIC_RAG: Enables code example extraction, storage, and specialized code search functionality
USE_AGENTIC_RAG=true

# USE_RERANKING: Applies cross-encoder reranking to improve search result relevance
USE_RERANKING=true

[ ] suggest a plan on how to rewrite the files crawl4ai_mcp.py and utils.py for them to only serve the mentioned tools and not do any crawling or other stuff. Also suggest anything that you find important for the retrieval of spec information in the context of dita authoring, such as queries to retrieve information that could help to build the dita skill for Claude code.

[ ] Think about your plan and if necessary ask for additional information. Ammend the plan if necessary. Mark the steps that you are done with in this document.

[ ] Write the plan in the current directory as .md.
