# 任务1：解析服务对接（MinerU + 参考文献 + VLM + 归一化入库）设计

> 日期：2026-06-04 ｜ 目标文件：`backend/services/parsing/parser.py` + `backend/common/config.py`
> 范围决策（已与用户确认）：仅 parser.py 核心；MinerU 走云端 `MineruKIEClient` SDK；同步 SDK 用 `asyncio.to_thread` 包裹；归一化用容错适配层。

## 1. 背景与现状

- `parser.py` 已有骨架，但 `_call_mineru` 是对接虚构 HTTP 接口（`POST {MINERU_BASE_URL}/parse`），与任务书要求的 `mineru-kie-sdk` 的 `MineruKIEClient`（云端 `https://mineru.net/api/kie` + `pipeline_id`）不符，需重写。
- `mineru-kie-sdk==0.1.1` 已在 `requirements.txt`（仅在 Docker 内安装，本地未装）。
- SDK 为**同步阻塞**（基于 `requests`），项目其余部分全异步。
- 依赖的基础设施 `common/db/mysql.py`、`common/clients/minio.py`、`common/clients/redis.py` 均不存在；worker 与 upload 路由未接线 —— **本次不接线**，超出范围。
- `parse_paper(user_id, paper_id, pdf_key, db, *, pdf_bytes=None)` 现有签名保持不变（由未来 worker 传入 `AsyncSession`）。

## 2. 范围

### 做
1. **MinerU SDK 对接**：用 `MineruKIEClient` 上传 PDF + 轮询取结果，`asyncio.to_thread` 包裹。
2. **容错归一化**：把 MinerU `parse` 结果适配为 `Block` 列表，容忍未知字段名与 type 取值。
3. **VLM 图描述**：复用现有逻辑，确保图先落 MinIO 再描述。
4. **图片落 MinIO**：抠图上传 `figures` bucket，key=`{user_id}/{paper_id}/{block_id}.png`，回填 `image_key`。
5. **数据归一化入库**：写 `doc_blocks`、`citations`，更新 `papers.status`。
6. 新增 MinerU 云端配置项到 `config.py`。
7. 验证文档写入 `check/`。

### 不做（YAGNI / 越界）
- 不接 worker（`handle_ingest_job`）、不改 `papers.py` upload 路由。
- 不建 `common/db/mysql.py` 单例（沿用传入的 `db: AsyncSession`）。
- 不建 `common/clients/minio.py` 全局封装（parser 内就地最小封装，用已装的 `minio` 包）。
- GROBID 路径（`_extract_refs_grobid`/`_parse_tei_references`）保留不动。

## 3. 架构（5 个单元）

### 单元 1：MinerU SDK 对接 — `_call_mineru(pdf_bytes: bytes) -> dict`
- 同步内层 `_sync_mineru_call(pdf_bytes) -> dict`：
  - `MineruKIEClient(base_url=MINERU_KIE_BASE_URL, pipeline_id=MINERU_PIPELINE_ID, timeout=MINERU_TIMEOUT)`。
  - SDK `upload_file` 接收文件路径 → 用 `tempfile.NamedTemporaryFile(suffix=".pdf")` 落临时文件再传，`finally` 删除。
  - `get_result(timeout=MINERU_TIMEOUT, poll_interval=MINERU_POLL_INTERVAL)` → 返回 `results["parse"]`（取不到则返回 `{}`）。
- 外层 `async def _call_mineru`：`return await asyncio.to_thread(_sync_mineru_call, pdf_bytes)`。
- SDK 鉴权：若构造函数/环境需要 API key，通过 `MINERU_API_KEY`（实测以 SDK 实际签名为准，预留配置项）。
- 失败（`requests.RequestException`/`TimeoutError`/`ValueError`）向上抛 → worker 标 failed。

**前置条件变更**：`parse_paper` 现按 `pdf_key` 走（HTTP stub 用 key）；SDK 需要 PDF 字节。本次让 MinerU 分支依赖 `pdf_bytes`；`pdf_bytes is None` 时记 warning 并抛 `ValueError`（worker 未来负责从 MinIO 取字节传入）。这是范围内对契约的最小、明确的收紧，记录在 check 文档。

### 单元 2：容错归一化 — `_mineru_to_blocks(parse_result: dict) -> list[Block]`（最关键）
SDK 文档未给 block 结构，故写**容错适配层**，并把假设写进 check 文档供真实联调核对：
- `_find_block_list(obj)`：递归在 dict/list 中寻找 block 列表，命中常见键 `blocks` / `items` / `elements` / `content_list`，或 `pages[*].blocks` 聚合。
- type 归一 `_norm_type(raw)`：`image|img|figure → figure`；`equation|formula|latex → formula`；`table → table`；其余 → `text`。
- 字段容错（按优先级取第一个非空）：
  - content：`content` → `text` → `html` → `latex` → `markdown` → `caption` → `""`
  - page：`page_num` → `page_idx`(+1 归一为 1-based) → `page` → `None`
  - bbox：`bbox` → `box` → `poly` → `None`
  - image：`image_key` → `image_url` → `img_path`；二进制 `image`/`image_base64` 暂存 `_raw_image` 供单元4上传
- 未识别 type/字段 → `logger.warning`，**不抛错**；空列表返回 `[]`（上游据此标 failed）。

### 单元 3：图片落 MinIO — `_upload_figures(user_id, paper_id, blocks, db) -> None`
- 仅处理 figure 且持有原始图字节/base64（`_raw_image`）的 block。
- **写库顺序调整**：先 `_write_blocks` 插入拿到 `block_id`（`cursor.lastrowid`），再据 `block_id` 拼 key 上传，最后 `UPDATE doc_blocks SET image_key=... WHERE id=...`。
- 就地最小 MinIO 封装 `_minio_client()`（`minio.Minio`，配置取自 settings），bucket 不存在则建。
- 单图失败 → warning，不中断整篇。
- 若 MinerU 已自传图并返回 `image_key`（云端常见）→ 跳过上传，直接用其 key。

### 单元 4：VLM 图描述 — `_describe_figures(blocks)`（复用，微调）
- 现有逻辑保留：figure block 并发调 VLM 填 `content_zh`。
- 调整：图片 URL 改用 MinIO presigned URL（`_minio_presigned(image_key)`），保证 VLM 可访问；失败 warning。
- 依赖单元3已回填 `image_key`，故顺序：写库+传图 → VLM 描述。

### 单元 5：参考文献 + 落库
- `_extract_refs_llm`（已可用）保留。
- `_write_blocks`：改为逐条 INSERT 后回收 `lastrowid` 写回 `Block.block_id`（新增字段）。
- `_write_citations`（已有）保留。
- 末尾 `UPDATE papers SET status='done', num_pages=?, abstract=? WHERE id=? AND user_id=?`（带 user_id，多租户铁律）。失败由 worker 兜底标 failed。

## 4. 数据流

```
pdf_bytes
  → [to_thread] MineruKIE upload + poll → parse_result(dict)
  → _mineru_to_blocks (容错归一化) → blocks[]
  → _write_blocks (INSERT, 回填 block_id)
  → _upload_figures (传图 figures bucket, UPDATE image_key)
  → _describe_figures (VLM 填 content_zh, 用 presigned URL)
  → _extract_refs_llm → _write_citations
  → UPDATE papers.status='done'
  → db.commit() → ParseResult
```

## 5. 错误处理矩阵

| 失败点 | 处理 |
|---|---|
| MinerU 上传/轮询/超时 | 抛异常 → worker 标 failed |
| `pdf_bytes is None`（SDK 模式） | warning + 抛 ValueError |
| 归一化遇未知字段/type | warning，尽力填充，不抛 |
| 归一化返回空 blocks | 返回 `[]`，上游标 failed |
| 单张图上传 MinIO 失败 | warning，跳过该图，继续 |
| 单个 figure VLM 失败 | warning，content_zh 留空，继续 |
| 单条参考文献写入失败 | warning，继续其余 |

## 6. 配置新增（config.py + .env.example）

```python
MINERU_KIE_BASE_URL: str = "https://mineru.net/api/kie"
MINERU_PIPELINE_ID: str = ""          # 必填，云端 pipeline
MINERU_API_KEY: str = ""              # 预留，按 SDK 实际鉴权方式使用
MINERU_POLL_INTERVAL: int = 5         # 轮询间隔(秒)
MINERU_TIMEOUT: int = 300             # 轮询总超时(秒)，-1 表示直到完成
```
（保留现有 `MINERU_BASE_URL` 不删，供未来本地 HTTP 模式。）

## 7. 验收标准（对应 implement-parsing.md）

- [ ] 用 `MineruKIEClient` 完成上传 + 轮询，同步调用经 `asyncio.to_thread` 不阻塞事件循环。
- [ ] 公式→LaTeX、表格→HTML、文本带阅读序 归一化进 `doc_blocks`。
- [ ] 图片落 MinIO `figures/{user_id}/{paper_id}/{block_id}.png`，`image_key` 正确回填。
- [ ] 每个 block 保留 `page_num` 与 `bbox`，不丢（MEMORY.md 头号陷阱）。
- [ ] 参考文献经 LLM 提取写入 `citations`（src_paper_id/dst_title/raw_ref）。
- [ ] 所有 DB 写入/更新带 `user_id` 或 `paper_id` 归属，多租户隔离。
- [ ] `papers.status` 在成功后置 `done`。
- [ ] 容错归一化的字段映射假设写入 `check/` 文档，供真实联调核对。

## 8. 测试策略

本地无 MinerU 凭据、无 DB 连接，故以**离线单元测试**为主：
- 构造若干形态各异的 mock `parse_result`（不同键名/type 别名/缺字段），断言 `_mineru_to_blocks` 输出 Block 字段正确、不抛错、page/bbox 不丢。
- `_norm_type`、字段取值优先级的纯函数测试。
- `asyncio.to_thread` 包裹路径用 monkeypatch 假 `MineruKIEClient` 验证不阻塞、异常透传。
- 真实 MinerU/MinIO/DB 联调留待 Docker 环境（在 check 文档中标注待验证项）。
