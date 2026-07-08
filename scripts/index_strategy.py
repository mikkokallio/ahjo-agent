"""Index Helsinki City Strategy 2025-2029 PDF into Azure AI Search.

Uses:
- pdfplumber for text extraction
- Cohere embed-v-4-0 via Azure AI Foundry for embeddings
- Azure AI Search Free tier with Entra auth (no keys)
"""

import os
import sys
import json
import pdfplumber
import httpx
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# Configuration
SEARCH_ENDPOINT = "https://search-ahjo-agent.search.windows.net"
INDEX_NAME = "strategy-2025-2029"
AI_ENDPOINT = "https://ai-account-mapx4sxw6thvg.services.ai.azure.com"
EMBEDDING_DEPLOYMENT = "embed-v-4-0"
PDF_PATH = os.path.join(os.path.dirname(__file__), "..", "grounding", "Kaupunkistrategia-2025-2029.pdf")

# Chunk settings
CHUNK_SIZE = 800  # tokens ~= words for Finnish
CHUNK_OVERLAP = 100


def extract_text(pdf_path: str) -> list[dict]:
    """Extract text from PDF, one entry per page."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"page": i + 1, "text": text.strip()})
    return pages


def chunk_pages(pages: list[dict]) -> list[dict]:
    """Split pages into overlapping chunks."""
    chunks = []
    chunk_id = 0

    for page_info in pages:
        words = page_info["text"].split()
        page_num = page_info["page"]

        start = 0
        while start < len(words):
            end = start + CHUNK_SIZE
            chunk_text = " ".join(words[start:end])
            chunks.append({
                "id": f"strategy-p{page_num}-c{chunk_id}",
                "content": chunk_text,
                "page": page_num,
                "source": "Kaupunkistrategia 2025-2029",
            })
            chunk_id += 1
            start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


def get_embeddings(texts: list[str], credential) -> list[list[float]]:
    """Get embeddings from Cohere embed-v-4-0 via Azure AI Foundry."""
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    token = token_provider()

    # Azure AI model inference endpoint for embeddings
    url = f"{AI_ENDPOINT}/models/embeddings?api-version=2024-05-01-preview"

    all_embeddings = []
    batch_size = 8  # Smaller batches to stay within rate limits

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]

        for attempt in range(3):
            resp = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "input": batch,
                    "input_type": "document",
                    "model": EMBEDDING_DEPLOYMENT,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                break
            if resp.status_code == 429 or "DeploymentNotFound" in resp.text:
                import time
                wait = 10 * (attempt + 1)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                token = token_provider()  # Refresh token
            else:
                print(f"Embedding error {resp.status_code}: {resp.text}")
                sys.exit(1)

        if resp.status_code != 200:
            print(f"Embedding failed after retries: {resp.status_code}: {resp.text}")
            sys.exit(1)

        data = resp.json()
        for item in data["data"]:
            all_embeddings.append(item["embedding"])

        print(f"  Embedded batch {i // batch_size + 1} ({len(batch)} chunks)")

    return all_embeddings


def create_index(credential):
    """Create the search index with vector field."""
    token_provider = get_bearer_token_provider(credential, "https://search.azure.com/.default")
    token = token_provider()

    index_def = {
        "name": INDEX_NAME,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "fi.lucene"},
            {"name": "page", "type": "Edm.Int32", "filterable": True, "sortable": True},
            {"name": "source", "type": "Edm.String", "filterable": True},
            {
                "name": "content_vector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "dimensions": 1536,
                "vectorSearchProfile": "default-profile",
            },
        ],
        "vectorSearch": {
            "algorithms": [{"name": "hnsw-algo", "kind": "hnsw"}],
            "profiles": [{"name": "default-profile", "algorithm": "hnsw-algo"}],
        },
        "semantic": {
            "configurations": [
                {
                    "name": "default-semantic",
                    "prioritizedFields": {
                        "prioritizedContentFields": [{"fieldName": "content"}],
                    },
                }
            ],
            "defaultConfiguration": "default-semantic",
        },
    }

    resp = httpx.put(
        f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}?api-version=2024-07-01",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=index_def,
        timeout=30,
    )

    if resp.status_code in (200, 201, 204):
        print(f"Index '{INDEX_NAME}' created/updated successfully")
    else:
        print(f"Index creation failed {resp.status_code}: {resp.text}")
        sys.exit(1)


def upload_documents(chunks: list[dict], embeddings: list[list[float]], credential):
    """Upload documents with vectors to the search index."""
    token_provider = get_bearer_token_provider(credential, "https://search.azure.com/.default")
    token = token_provider()

    # Build documents
    documents = []
    for chunk, embedding in zip(chunks, embeddings):
        doc = {
            "@search.action": "upload",
            "id": chunk["id"],
            "content": chunk["content"],
            "page": chunk["page"],
            "source": chunk["source"],
            "content_vector": embedding,
        }
        documents.append(doc)

    # Upload in batches of 100
    batch_size = 100
    for i in range(0, len(documents), batch_size):
        batch = documents[i:i + batch_size]
        resp = httpx.post(
            f"{SEARCH_ENDPOINT}/indexes/{INDEX_NAME}/docs/index?api-version=2024-07-01",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"value": batch},
            timeout=60,
        )

        if resp.status_code in (200, 207):
            results = resp.json().get("value", [])
            ok = sum(1 for r in results if r.get("status"))
            print(f"  Uploaded batch {i // batch_size + 1}: {ok}/{len(batch)} succeeded")
        else:
            print(f"Upload failed {resp.status_code}: {resp.text}")
            sys.exit(1)


def main():
    print("=== Helsinki City Strategy 2025-2029 Indexing ===\n")

    credential = DefaultAzureCredential()

    # 1. Extract text
    print("1. Extracting text from PDF...")
    pages = extract_text(PDF_PATH)
    print(f"   Extracted {len(pages)} pages with text\n")

    # 2. Chunk
    print("2. Chunking text...")
    chunks = chunk_pages(pages)
    print(f"   Created {len(chunks)} chunks\n")

    # 3. Create index
    print("3. Creating search index...")
    create_index(credential)
    print()

    # 4. Generate embeddings
    print("4. Generating embeddings...")
    texts = [c["content"] for c in chunks]
    embeddings = get_embeddings(texts, credential)
    print(f"   Generated {len(embeddings)} embeddings\n")

    # 5. Upload
    print("5. Uploading documents to index...")
    upload_documents(chunks, embeddings, credential)
    print(f"\nDone! {len(chunks)} chunks indexed in '{INDEX_NAME}'")


if __name__ == "__main__":
    main()
