# scripts/init_milvus.py
# 创建 landscape_kb Collection（dense + sparse hybrid）
# 用法：python scripts/init_milvus.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)

from backend.config import get_settings


def init_collection() -> None:
    settings = get_settings()
    connections.connect(
        alias="default",
        host=settings.milvus_host,
        port=str(settings.milvus_port),
    )
    name = settings.milvus_collection
    if utility.has_collection(name):
        print(f"Collection '{name}' already exists, skip create.")
        col = Collection(name)
        print(f"  entities={col.num_entities}")
        return

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
        FieldSchema(name="dense", dtype=DataType.FLOAT_VECTOR, dim=settings.milvus_dense_dim),
        FieldSchema(name="sparse", dtype=DataType.SPARSE_FLOAT_VECTOR),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="page", dtype=DataType.INT64),
        FieldSchema(name="standard_code", dtype=DataType.VARCHAR, max_length=128),
    ]
    schema = CollectionSchema(fields=fields, description="Landscape planning knowledge base")
    col = Collection(name=name, schema=schema)

    col.create_index(
        "dense",
        {
            "index_type": "HNSW",
            "metric_type": "IP",
            "params": {"M": 16, "efConstruction": 200},
        },
    )
    col.create_index(
        "sparse",
        {
            "index_type": "SPARSE_INVERTED_INDEX",
            "metric_type": "IP",
            "params": {"drop_ratio_build": 0.2},
        },
    )
    col.load()
    print(f"Collection '{name}' created and loaded.")


if __name__ == "__main__":
    init_collection()
