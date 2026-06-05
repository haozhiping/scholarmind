# 文档结构说明

## 一、文档目录总览

```
docs/
├── superpowers/           # 功能模块文档（规划、设计、评估）
│   ├── plans/             # 实现计划文档
│   ├── specs/             # 技术设计文档
│   └── *.md               # 完成度评估报告
├── architecture.md        # 架构总览
├── api.md                 # API 接口文档
├── data-contracts.md      # 数据契约定义
├── deploy.md              # 部署指南
├── rag-pipeline.md        # RAG 流程说明
└── DOCUMENTATION_STRUCTURE.md  # 本文档
```

```
check/                     # 验证报告目录（独立于 docs，便于CI/CD集成）
├── 任务完成情况检查报告.md           # 综合检查报告（已加 Mock 缺口修正说明）
├── 接口真实性核查与去Mock化路线.md   # 🔴 权威现状：API 层 Mock 矩阵 + 去 Mock 路线
├── 任务1-解析服务对接-验证报告.md
├── 任务2-切分与向量化入库验收报告.md
└── 后处理任务验证报告.md
```

---

## 二、文档分类说明

### 2.1 规划文档 (`docs/superpowers/plans/`)

**用途**: 记录功能模块的实现计划和任务分解

**命名规范**: `YYYY-MM-DD-{module}-{feature}.md`

**示例**:
- `2026-06-04-parsing-mineru.md` - MinerU 解析服务实现计划
- `2026-06-05-indexing-milvus.md` - Milvus 向量化入库计划

### 2.2 设计文档 (`docs/superpowers/specs/`)

**用途**: 记录技术设计细节、接口定义、数据结构

**命名规范**: `YYYY-MM-DD-{module}-{feature}-design.md`

**示例**:
- `2026-06-04-parsing-mineru-design.md` - MinerU 解析设计文档
- `2026-06-04-ingest-pipeline-wiring-design.md` - 入库流水线设计

### 2.3 评估报告 (`docs/superpowers/*.md`)

**用途**: 记录功能模块的完成度评估

**命名规范**: `{模块名称}完成度评估.md`

**示例**:
- `解析服务对接完成度评估.md`
- `切分与向量化入库完成度评估.md`

### 2.4 验证报告 (`check/`)

**用途**: 记录详细的验证检查报告、验收结果

**命名规范**:
- `任务完成情况检查报告.md` - 综合报告
- `任务{编号}-{任务名称}-验证报告.md` - 单项任务报告

### 2.5 核心文档 (`docs/`)

| 文件 | 用途 | 更新频率 |
|------|------|---------|
| `architecture.md` | 架构总览 | 低频（架构变更时） |
| `api.md` | API 接口文档 | 中高频（接口变更时） |
| `data-contracts.md` | 数据契约定义 | 中低频（schema变更时） |
| `deploy.md` | 部署指南 | 低频（部署方式变更时） |
| `rag-pipeline.md` | RAG 流程说明 | 中低频（流程优化时） |

---

## 三、文档同步机制

### 3.1 文档层级关系

```
规划文档 (plans/)
      ↓ 实现
设计文档 (specs/)
      ↓ 开发
评估报告 (*完成度评估.md)
      ↓ 验证
验证报告 (check/)
      ↓ 汇总
综合报告 (check/任务完成情况检查报告.md)
```

### 3.2 更新原则

1. **评估报告** 与 **验证报告** 保持内容同步
2. **验证报告** 包含详细技术验证、代码引用、测试结果
3. **评估报告** 包含摘要性完成度、待办事项、优先级
4. **综合报告** 汇总所有任务状态

---

## 四、文档管理最佳实践

### 4.1 版本控制

- 所有文档纳入 Git 版本控制
- 使用语义化提交信息（如 `docs: 更新任务2完成度评估`）

### 4.2 链接规范

使用绝对路径链接：
```markdown
[parser.py](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/backend/services/parsing/parser.py)
```

### 4.3 检查清单

| 检查项 | 说明 |
|--------|------|
| ✅ 文档与代码同步 | 确保文档引用的代码行号准确 |
| ✅ 链接有效性 | 定期检查所有链接是否可达 |
| ✅ 日期更新 | 每次修改更新文档日期 |
| ✅ 状态一致 | 各报告之间的状态保持一致 |

---

## 五、文档索引

### 5.1 任务相关文档

| 任务 | 规划 | 设计 | 评估 | 验证 |
|------|------|------|------|------|
| 任务1：解析服务对接 | [plans/2026-06-04-parsing-mineru.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/docs/superpowers/plans/2026-06-04-parsing-mineru.md) | [specs/2026-06-04-parsing-mineru-design.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/docs/superpowers/specs/2026-06-04-parsing-mineru-design.md) | [解析服务对接完成度评估.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/docs/superpowers/解析服务对接完成度评估.md) | [check/任务1-解析服务对接-验证报告.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/check/任务1-解析服务对接-验证报告.md) |
| 任务2：切分与向量化入库 | [plans/2026-06-05-indexing-milvus.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/docs/superpowers/plans/2026-06-05-indexing-milvus.md) | - | [切分与向量化入库完成度评估.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/docs/superpowers/切分与向量化入库完成度评估.md) | [check/任务2-切分与向量化入库验收报告.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/check/任务2-切分与向量化入库验收报告.md) |
| 任务3：混合检索服务 | - | - | - | - |
| 任务4：对话与Agent综述 | - | - | - | - |
| 任务5：前端页面与API联调 | - | - | - | - |

### 5.2 综合报告

| 报告 | 路径 |
|------|------|
| 项目综合检查报告 | [check/任务完成情况检查报告.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/check/任务完成情况检查报告.md) |
| 🔴 接口真实性核查与去 Mock 化路线（权威现状） | [check/接口真实性核查与去Mock化路线.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/check/接口真实性核查与去Mock化路线.md) |
| 接口去 Mock 化完成度评估 | [docs/superpowers/接口去Mock化完成度评估.md](file:///c:/Users/18308/Documents/re/vsfile/scholarmind/docs/superpowers/接口去Mock化完成度评估.md) |

---

## 六、文档维护流程

```
1. 功能开发完成
        ↓
2. 更新验证报告（check/）
        ↓
3. 更新评估报告（docs/superpowers/）
        ↓
4. 更新综合报告（check/任务完成情况检查报告.md）
        ↓
5. 提交代码审查
        ↓
6. 合并到主分支
```
