# 任务 2：切分与向量化入库 (Indexing + Milvus) 实现计划

> **前置条件**：任务 1 解析服务对接已完成并验证通过（21 个测试用例全部通过）。

> **目标**：把 `doc_blocks` 变成可检索的 Milvus chunk（双语 + 向量），完成 ingest 管道的最后一环。

---

## 一、任务概述

### 任务要求（来自 README.md L68-71）

| 步骤 | 任务 | 说明 |
|---|---|---|
| 1 | **智能切分** | 读取 `doc_blocks` 文本内容并按章节/语义切分（重叠度 15-20%），大表格和公式块整体保留不切分 |
| 2 | **双语增强** | 调用 LLM，配合 `prompts/enrich_zh_summary.md` 提示词为英文文本块生成中文摘要及关键词，写入 `content_zh` |
| 3 | **向量化写入 Milvus** | 调用 Embedding 获取 dense+sparse 向量。使用 Milvus 客户端初始化 collection（配置 HNSW 索引与 Partition Key），将分块批量（Bulk）写入 Milvus |

### 设计约束（来自 CLAUDE.md）

- **每个 chunk 必带**：`user_id/paper_id/folder_id/page_num/block_id/image_key`
- **维度一致**：向量维度 == `EMBEDDING_DIM` == Milvus dense_vec 维度（1024）
- **collection/索引**：启动时确保已创建 HNSW 索引 + sparse 索引 + `user_id` partition_key 并 load
- **幂等**：chunk id = `xxhash(content_en + paper_id)`，重复入库覆盖不重复
- **批量入库**：用 Milvus bulk insert，别逐条

---

## 二、架构设计

### 2.1 流程架构

```
doc_blocks (MySQL)
  ↓
1. 智能切分 → Chunk 列表
  ↓
2. 双语增强 → content_zh（LLM 生成）
  ↓
3. 向量化 → dense_vec + sparse_vec
  ↓
4. 批量写入 Milvus → scholarmind_chunks
  ↓
5. 更新 papers.chunk_count
```

### 2.2 文件结构

| 文件 | 职责 | 操作 |
|---|---|---|
| `backend/services/indexing/__init__.py` | 模块入口 | Create |
| `backend/services/indexing/indexer.py` | 核心索引逻辑 | Create |
| `backend/common/clients/milvus.py` | Milvus 客户端封装 | Create |
| `backend/tests/test_indexing.py` | 索引模块测试 | Create |
| `backend/common/config.py` | Milvus 配置项 | Modify |

### 2.3 Milvus Collection 结构（来自 data-contracts.md）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(主键) | `xxhash64(content_en + paper_id)`，幂等去重 |
| `dense_vec` | FLOAT_VECTOR(1024) | 稠密向量 |
| `sparse_vec` | SPARSE_FLOAT_VECTOR | 稀疏向量 |
| `content_en` | VARCHAR | 原文（英文） |
| `content_zh` | VARCHAR | 中文摘要+关键词 |
| `user_id` | INT64 | **partition_key**，租户物理隔离 |
| `paper_id` | INT64 | scalar 过滤 |
| `folder_id` | INT64 | scalar 过滤 |
| `acl` | VARCHAR | 可见性/角色标签 |
| `chunk_type` | VARCHAR | text \| table \| figure \| formula |
| `section` | VARCHAR | 所属章节 |
| `page_num` | INT64 | 溯源定位页码 |
| `bbox` | JSON | 高亮原文坐标框 |
| `block_id` | INT64 | → MySQL `doc_blocks.id` |
| `image_key` | VARCHAR | → MinIO，答案回显原图 |

### 2.4 索引配置

| 字段 | 索引类型 | 参数 |
|---|---|---|
| `dense_vec` | HNSW | metric=COSINE, M=16, efConstruction=200, ef=64 |
| `sparse_vec` | SPARSE_INVERTED_INDEX | metric=IP |
| `user_id` | PARTITION_KEY | - |

---

## 三、实现步骤

### Task 1：添加 Milvus 配置项

**目标**：在 `common/config.py` 中添加 Milvus 相关配置

**文件**:
- Modify: `backend/common/config.py`

**步骤**:

1. **确认现有配置**（已存在）：
   ```python
   # Milvus
   MILVUS_URI: str = "http://milvus:19530"
   MILVUS_TOKEN: str = ""
   MILVUS_COLLECTION: str = "scholarmind_chunks"
   MILVUS_INDEX_TYPE: str = "HNSW"
   MILVUS_METRIC: str = "COSINE"
   ```

2. **新增配置项**：
   ```python
   # Milvus 索引参数
   MILVUS_M: int = 16                    # HNSW M 参数
   MILVUS_EF_CONSTRUCTION: int = 200     # 构建时 ef
   MILVUS_EF_SEARCH: int = 64            # 查询时 ef
   MILVUS_BATCH_SIZE: int = 100          # bulk insert 批次大小
   ```

---

### Task 2：创建 Milvus 客户端封装

**目标**：创建 `common/clients/milvus.py` 封装 Milvus 操作

**文件**:
- Create: `backend/common/clients/milvus.py`
- Test: `backend/tests/test_milvus_client.py`

**步骤**:

1. **创建客户端封装**：
   ```python
   """Milvus vector store client."""
   from typing import List, Dict, Any, Optional
   from pymilvus import (
       MilvusClient,
       Collection,
       FieldSchema,
       CollectionSchema,
       DataType,
       IndexType,
       MetricType,
   )
   from common.config import settings
   from common.logging import logger
   
   _client: Optional[MilvusClient] = None
   _collection: Optional[Collection] = None
   
   def get_milvus_client() -> MilvusClient:
       """Get singleton Milvus client."""
       global _client
       if _client is None:
           _client = MilvusClient(
               uri=settings.MILVUS_URI,
               token=settings.MILVUS_TOKEN,
           )
       return _client
   
   def ensure_collection() -> Collection:
       """Ensure collection exists with proper schema and indexes."""
       global _collection
       if _collection is not None:
           return _collection
       
       client = get_milvus_client()
       collection_name = settings.MILVUS_COLLECTION
       
       # Check if collection exists
       if client.has_collection(collection_name):
           logger.info(f"Collection {collection_name} already exists")
       else:
           # Create schema
           fields = [
               FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
               FieldSchema(name="dense_vec", dtype=DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIM),
               FieldSchema(name="sparse_vec", dtype=DataType.SPARSE_FLOAT_VECTOR),
               FieldSchema(name="content_en", dtype=DataType.VARCHAR, max_length=8192),
               FieldSchema(name="content_zh", dtype=DataType.VARCHAR, max_length=8192),
               FieldSchema(name="user_id", dtype=DataType.INT64),
               FieldSchema(name="paper_id", dtype=DataType.INT64),
               FieldSchema(name="folder_id", dtype=DataType.INT64),
               FieldSchema(name="acl", dtype=DataType.VARCHAR, max_length=64),
               FieldSchema(name="chunk_type", dtype=DataType.VARCHAR, max_length=16),
               FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=256),
               FieldSchema(name="page_num", dtype=DataType.INT64),
               FieldSchema(name="bbox", dtype=DataType.VARCHAR, max_length=512),
               FieldSchema(name="block_id", dtype=DataType.INT64),
               FieldSchema(name="image_key", dtype=DataType.VARCHAR, max_length=256),
           ]
           schema = CollectionSchema(fields=fields, enable_dynamic_field=True)
           
           client.create_collection(
               collection_name=collection_name,
               schema=schema,
               partition_key_field="user_id",
           )
           logger.info(f"Created collection {collection_name}")
       
       # Create HNSW index for dense_vec if not exists
       client.create_index(
           collection_name=collection_name,
           field_name="dense_vec",
           index_params={
               "index_type": "HNSW",
               "metric_type": "COSINE",
               "params": {
                   "M": settings.MILVUS_M,
                   "efConstruction": settings.MILVUS_EF_CONSTRUCTION,
               },
           },
       )
       
       # Create sparse index if not exists
       client.create_index(
           collection_name=collection_name,
           field_name="sparse_vec",
           index_params={"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP"},
       )
       
       # Load collection
       _collection = Collection(collection_name)
       _collection.load()
       logger.info(f"Loaded collection {collection_name}")
       
       return _collection
   
   def bulk_insert(chunks: List[Dict[str, Any]]) -> None:
       """Bulk insert chunks into Milvus."""
       collection = ensure_collection()
       collection.insert(chunks)
       collection.flush()
       logger.info(f"Inserted {len(chunks)} chunks")
   ```

2. **创建测试文件**：
   ```python
   """Milvus client tests."""
   from common.clients.milvus import get_milvus_client, ensure_collection, bulk_insert
   from common.config import settings
   
   def test_milvus_config():
       assert settings.MILVUS_URI == "http://milvus:19530"
       assert settings.MILVUS_COLLECTION == "scholarmind_chunks"
       assert settings.MILVUS_M == 16
   
   def test_milvus_client_singleton():
       client1 = get_milvus_client()
       client2 = get_milvus_client()
       assert client1 is client2
   ```

---

### Task 3：实现智能切分器

**目标**：创建切分逻辑，读取 `doc_blocks` 并按章节/语义切分

**文件**:
- Create: `backend/services/indexing/indexer.py`
- Test: `backend/tests/test_indexing.py`

**步骤**:

1. **创建切分器类**：
   ```python
   """Indexer service: chunking + bilingual enrichment + vectorization."""
   from __future__ import annotations
   from dataclasses import dataclass, field
   from typing import List, Dict, Any
   import xxhash
   from sqlalchemy.ext.asyncio import AsyncSession
   from sqlalchemy import text
   
   from common.clients.llm import embed_texts
   from common.clients.milvus import bulk_insert
   from common.config import settings
   from common.logging import logger
   
   @dataclass
   class Chunk:
       id: str = ""
       content_en: str = ""
       content_zh: str = ""
       user_id: int = 0
       paper_id: int = 0
       folder_id: int = 0
       chunk_type: str = "text"
       section: str = ""
       page_num: int = 0
       bbox: str = ""
       block_id: int = 0
       image_key: str = ""
       dense_vec: List[float] = field(default_factory=list)
       sparse_vec: Dict[int, float] = field(default_factory=dict)
   
   class Chunker:
       """智能切分器：按章节/语义切分，大表/公式整块保留。"""
       
       def __init__(self, chunk_size: int = 512, overlap_ratio: float = 0.15):
           self.chunk_size = chunk_size
           self.overlap_ratio = overlap_ratio
           self.overlap_size = int(chunk_size * overlap_ratio)
       
       def _split_text(self, text: str) -> List[str]:
           """按语义切分文本，保持句子完整性。"""
           sentences = re.split(r'(?<=[.!?])\s+', text)
           chunks = []
           current_chunk = []
           current_length = 0
           
           for sentence in sentences:
               sentence_len = len(sentence)
               if current_length + sentence_len > self.chunk_size and current_chunk:
                   chunks.append(" ".join(current_chunk))
                   # 保留重叠部分
                   overlap_count = max(1, int(len(current_chunk) * self.overlap_ratio))
                   current_chunk = current_chunk[-overlap_count:] + [sentence]
                   current_length = sum(len(s) for s in current_chunk)
               else:
                   current_chunk.append(sentence)
                   current_length += sentence_len
           
           if current_chunk:
               chunks.append(" ".join(current_chunk))
           
           return chunks
       
       async def chunk_paper(self, db: AsyncSession, paper_id: int, user_id: int) -> List[Chunk]:
           """切分单篇论文的所有 doc_blocks。"""
           # 读取 doc_blocks
           result = await db.execute(
               text("""
                   SELECT id, block_type, content, page_num, bbox, image_key
                   FROM doc_blocks
                   WHERE paper_id = :paper_id AND user_id = :user_id
                   ORDER BY page_num, id
               """),
               {"paper_id": paper_id, "user_id": user_id}
           )
           blocks = result.fetchall()
           
           chunks = []
           for block in blocks:
               block_id, block_type, content, page_num, bbox, image_key = block
               
               if block_type in ("table", "figure", "formula"):
                   # 大表/图/公式整块保留
                   chunk = Chunk(
                       content_en=content[:8192],
                       chunk_type=block_type,
                       page_num=page_num or 0,
                       bbox=bbox or "",
                       block_id=block_id,
                       image_key=image_key or "",
                       user_id=user_id,
                       paper_id=paper_id,
                   )
                   chunks.append(chunk)
               else:
                   # 文本块智能切分
                   text_chunks = self._split_text(content)
                   for i, text_chunk in enumerate(text_chunks):
                       chunk = Chunk(
                           content_en=text_chunk[:8192],
                           chunk_type="text",
                           page_num=page_num or 0,
                           block_id=block_id,
                           user_id=user_id,
                           paper_id=paper_id,
                       )
                       chunks.append(chunk)
           
           # 生成 chunk id（幂等）
           for chunk in chunks:
               chunk.id = xxhash.xxh64(f"{chunk.content_en}{chunk.paper_id}").hexdigest()
           
           return chunks
   ```

---

### Task 4：实现双语增强

**目标**：调用 LLM 为英文文本块生成中文摘要及关键词

**文件**:
- Modify: `backend/services/indexing/indexer.py`
- Create: `prompts/enrich_zh_summary.md`（检查是否存在）

**步骤**:

1. **检查提示词文件**（已存在于 prompts/ 目录）

2. **添加双语增强逻辑**：
   ```python
   async def enrich_bilingual(chunks: List[Chunk]) -> None:
       """为英文 chunks 生成中文摘要和关键词。"""
       from common.clients.llm import chat_complete_json
       from pathlib import Path
       
       # 加载提示词模板
       prompt_path = Path(__file__).parents[3] / "prompts" / "enrich_zh_summary.md"
       prompt_template = prompt_path.read_text(encoding="utf-8")
       
       # 仅处理文本块
       text_chunks = [c for c in chunks if c.chunk_type == "text" and c.content_en]
       
       for chunk in text_chunks:
           try:
               prompt = prompt_template.format(content=chunk.content_en)
               result = await chat_complete_json(prompt, system="你是学术论文翻译和摘要助手。")
               
               if isinstance(result, dict):
                   chunk.content_zh = result.get("summary", "") + "\n" + result.get("keywords", "")
               else:
                   chunk.content_zh = str(result)[:8192]
           except Exception as e:
               logger.warning(f"Bilingual enrichment failed for chunk {chunk.id}: {e}")
               chunk.content_zh = ""
   ```

---

### Task 5：实现向量化与写入 Milvus

**目标**：调用 Embedding 获取向量，批量写入 Milvus

**文件**:
- Modify: `backend/services/indexing/indexer.py`

**步骤**:

```python
async def vectorize_chunks(chunks: List[Chunk]) -> None:
    """为 chunks 获取 dense + sparse 向量。"""
    texts = [chunk.content_en + " " + chunk.content_zh for chunk in chunks]
    embeddings = await embed_texts(texts)
    
    for i, chunk in enumerate(chunks):
        chunk.dense_vec = embeddings[i]
        # 稀疏向量（如果模型支持）
        # chunk.sparse_vec = ...

async def index_paper(
    user_id: int,
    paper_id: int,
    db: AsyncSession,
) -> int:
    """完整索引流程：切分 → 增强 → 向量化 → 写入 Milvus。"""
    logger.info(f"[index] paper_id={paper_id} user_id={user_id}")
    
    # Step 1: 智能切分
    chunker = Chunker(chunk_size=512, overlap_ratio=0.15)
    chunks = await chunker.chunk_paper(db, paper_id, user_id)
    logger.info(f"[index] Chunked into {len(chunks)} chunks")
    
    # Step 2: 双语增强
    await enrich_bilingual(chunks)
    logger.info(f"[index] Bilingual enrichment completed")
    
    # Step 3: 向量化
    await vectorize_chunks(chunks)
    logger.info(f"[index] Vectorization completed")
    
    # Step 4: 批量写入 Milvus
    records = [
        {
            "id": chunk.id,
            "dense_vec": chunk.dense_vec,
            "sparse_vec": chunk.sparse_vec,
            "content_en": chunk.content_en,
            "content_zh": chunk.content_zh,
            "user_id": chunk.user_id,
            "paper_id": chunk.paper_id,
            "folder_id": chunk.folder_id,
            "acl": "user",
            "chunk_type": chunk.chunk_type,
            "section": chunk.section,
            "page_num": chunk.page_num,
            "bbox": chunk.bbox,
            "block_id": chunk.block_id,
            "image_key": chunk.image_key,
        }
        for chunk in chunks
    ]
    
    bulk_insert(records)
    logger.info(f"[index] Written {len(chunks)} chunks to Milvus")
    
    # Step 5: 更新 papers.chunk_count
    await db.execute(
        text("UPDATE papers SET chunk_count = :cnt WHERE id = :pid AND user_id = :uid"),
        {"cnt": len(chunks), "pid": paper_id, "uid": user_id}
    )
    await db.commit()
    
    return len(chunks)
```

---

### Task 6：创建测试文件

**目标**：创建索引模块的单元测试

**文件**:
- Create: `backend/tests/test_indexing.py`

**步骤**:

```python
"""Indexing module tests."""
import pytest
from services.indexing.indexer import Chunker, Chunk
from unittest.mock import AsyncMock, MagicMock
import xxhash

def test_chunker_split_text():
    chunker = Chunker(chunk_size=100, overlap_ratio=0.2)
    text = "This is a test. This is another test. This is a third test. " * 10
    chunks = chunker._split_text(text)
    assert len(chunks) > 1
    # 验证重叠
    for i in range(len(chunks) - 1):
        overlap = set(chunks[i][-20:].split()) & set(chunks[i+1][:20].split())
        assert len(overlap) > 0

def test_chunk_id_is_deterministic():
    chunk1 = Chunk(content_en="test content", paper_id=1)
    chunk1.id = xxhash.xxh64(f"{chunk1.content_en}{chunk1.paper_id}").hexdigest()
    
    chunk2 = Chunk(content_en="test content", paper_id=1)
    chunk2.id = xxhash.xxh64(f"{chunk2.content_en}{chunk2.paper_id}").hexdigest()
    
    assert chunk1.id == chunk2.id

@pytest.mark.asyncio
async def test_chunk_paper_preserves_tables():
    db = AsyncMock()
    db.execute.return_value.fetchall.return_value = [
        (1, "table", "<table>...</table>", 1, "[1,2,3,4]", None),
        (2, "text", "Regular text here", 2, None, None),
    ]
    
    chunker = Chunker()
    chunks = await chunker.chunk_paper(db, paper_id=1, user_id=999)
    
    assert len(chunks) == 2
    assert chunks[0].chunk_type == "table"
    assert "<table>" in chunks[0].content_en
    assert chunks[1].chunk_type == "text"
```

---

## 四、执行顺序

| 序号 | 任务 | 文件 | 依赖 |
|---|---|---|---|
| 1 | 添加 Milvus 配置 | `common/config.py` | - |
| 2 | 创建 Milvus 客户端 | `common/clients/milvus.py` | 配置 |
| 3 | 编写 Milvus 测试 | `tests/test_milvus_client.py` | 客户端 |
| 4 | 创建索引器主文件 | `services/indexing/indexer.py` | Milvus 客户端 |
| 5 | 编写索引器测试 | `tests/test_indexing.py` | 索引器 |
| 6 | 运行测试验证 | - | 所有 |

---

## 五、测试运行约定

在 `backend/` 目录运行：

```bash
# 运行所有索引相关测试
python -m pytest tests/test_milvus_client.py tests/test_indexing.py -v

# 运行单个测试
python -m pytest tests/test_indexing.py::test_chunker_split_text -v

# 查看测试覆盖率
python -m pytest tests/ --cov=services/indexing --cov-report=term-missing
```

---

## 六、关键注意事项

### 6.1 维度一致性

- `EMBEDDING_DIM`(.env) == Milvus `dense_vec` 维度 == 实际模型输出维度（1024）
- 在 `config.py` 中验证：`assert settings.EMBEDDING_DIM == 1024`

### 6.2 幂等性

- chunk id 使用 `xxhash64(content_en + paper_id)` 确保重复入库覆盖不重复
- 写入前检查是否已存在（可选优化）

### 6.3 性能优化

- 使用 Milvus bulk insert，避免逐条写入
- 设置合理的批次大小（默认 100）
- 异步执行，避免阻塞主流程

### 6.4 错误处理

| 失败点 | 处理策略 |
|---|---|
| Milvus 连接失败 | 抛异常，worker 标 failed |
| Embedding 调用失败 | 记录 warning，跳过该 chunk |
| LLM 增强失败 | 记录 warning，content_zh 留空 |
| 单条写入失败 | 记录 warning，继续其余 |

---

## 七、检查清单

- [ ] Milvus 客户端创建成功
- [ ] Collection 自动初始化（含索引）
- [ ] 智能切分器实现（保留表格/公式整块）
- [ ] 双语增强（LLM 生成中文摘要）
- [ ] 向量化（dense + sparse）
- [ ] 批量写入 Milvus
- [ ] 更新 papers.chunk_count
- [ ] 单元测试覆盖核心功能

---

**规划完成日期**: 2026-06-05  
**预计开发周期**: 2-3 天  
**依赖**: 任务 1 解析服务（已完成）
