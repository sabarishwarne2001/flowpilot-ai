"""
One-off: redistribute the flat Chroma collection into per-workspace ones.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from collections import defaultdict
from app.db.session import SessionLocal
from app.models.work_item import WorkItem
from app.services.embedding_service import embedding_service
from app.services.bm25_service import bm25_service
from app.core.config import settings

db = SessionLocal()
owner = {str(w.id): w.workspace_id for w in db.query(WorkItem).all()}

source = embedding_service._get_collection(settings.CHROMA_COLLECTION_NAME)
payload = source.get(include=["documents", "metadatas", "embeddings"])

buckets = defaultdict(lambda: {"ids": [], "documents": [],
                               "embeddings": [], "metadatas": []})
orphans = []

for i, chunk_id in enumerate(payload["ids"]):
    meta = payload["metadatas"][i] or {}
    workspace_id = owner.get(meta.get("work_item_id"))
    if workspace_id is None:
        orphans.append(chunk_id)
        continue

    bucket = buckets[workspace_id]
    bucket["ids"].append(chunk_id)
    bucket["documents"].append(payload["documents"][i])
    bucket["embeddings"].append(payload["embeddings"][i])
    bucket["metadatas"].append({**meta, "workspace_id": str(workspace_id)})

for workspace_id, bucket in buckets.items():
    embedding_service.get_workspace_collection(workspace_id).add(**bucket)
    bm25_service.rebuild_index(workspace_id=workspace_id)
    print(f"{workspace_id}: {len(bucket['ids'])} vector(s) migrated.")

print(f"orphaned vectors (left in place): {len(orphans)}")
