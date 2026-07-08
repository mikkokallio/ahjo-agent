"""Quick script to list cached PDF blobs."""
import json
import subprocess
import sys

from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential

cred = DefaultAzureCredential()
svc = BlobServiceClient("https://stpdfprocahjo.blob.core.windows.net", credential=cred)
container = svc.get_container_client("pdf-cache")

blobs = list(container.list_blobs())
print(f"Cached blobs: {len(blobs)}")
for b in blobs:
    raw = container.get_blob_client(b.name).download_blob().readall()
    data = json.loads(raw)
    name = data.get("name", "?")
    tokens = data.get("tokens", 0)
    pages = data.get("pages", "?")
    print(f"  {b.name} ({b.size}B) -> {name} | {tokens} tokens, {pages} pages")
