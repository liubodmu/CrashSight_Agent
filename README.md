# CrashSight Analysis Agent

基于 LangGraph 的崩溃分析 Agent，通过自然语言对话驱动 CrashSight 数据查询与分析。

## 核心设计

- **LLM 最小使用原则**：LLM 仅用于历史问题堆栈对比，其余全是确定性逻辑
- **三层渐进式路由**：Layer1 关键词(0ms) → Layer2 Memory → Layer3 LLM 兜底，79.5% 请求零成本解决
- **自适应恢复**：Observe 节点检测异常模式（堆栈全空/历史全新/趋势异常），自动规划恢复策略

## 架构

```
用户输入 → Route(意图识别) → Act(工具执行) → Observe(结果评估+反思) → Report(数据报告)
                ↓                                    ↓
            Clarify(追问)                    recover → Act(恢复执行)
```

**5 个 LangGraph 节点**：Route / Clarify / Act / Observe / Report

## 评估指标

| 指标 | 数值 |
|------|------|
| 意图识别准确率 | 88.2% (51条) |
| E2E 成功率 | 80% |
| 平均响应时间 | 9.8s |
| 鲁棒率 | 100% (15种异常输入) |
| 路由零成本占比 | 79.5% |
| 并行优化 | 65s → 17s |
| 单次成本 | ~¥0.005 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 CrashSight/LLM API Key

# 启动 Web 服务
python app.py

# 或 CLI 模式
python cli.py
```

## 项目结构

```
crashsight_agent/
├── orchestration/          # Agent 编排层（LangGraph）
│   ├── nodes/              # 5 个图节点
│   │   ├── route.py        # 三层意图路由
│   │   ├── clarify.py      # 参数追问
│   │   ├── act.py          # 工具执行 + 恢复策略
│   │   ├── observe.py      # 结果评估 + 异常检测 + 自适应恢复
│   │   └── report.py       # 纯数据报告生成
│   ├── graph.py            # 状态机定义
│   └── state.py            # GraphState
├── tools/                  # 工具层
│   ├── stack_tool.py       # 堆栈获取
│   ├── history_tool.py     # 历史问题判定（LLM）
│   ├── keyframe.py         # 关键帧四轮打分算法
│   ├── trend_tool.py       # 崩溃率趋势
│   ├── top_issues_tool.py  # TOP 问题列表
│   ├── tapd_tool.py        # TAPD 缺陷单
│   ├── parallel_executor.py # 并行执行器
│   ├── circuit_breaker.py  # 熔断器
│   ├── rate_limiter.py     # 令牌桶限流
│   └── feedback.py         # 自进化反馈
├── memory/                 # 三层记忆（Episodic/Skill/Rule）
├── context/                # Token 管理 + 上下文压缩
├── streaming/              # SSE 流式事件
├── logging/                # 结构化日志
└── config.py               # 统一配置
```

## 配置

所有运行参数集中在 `config.py`，支持环境变量覆盖：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| MAX_TOOL_RETRIES | 3 | 工具最大重试次数 |
| MAX_CONCURRENT_ISSUES | 3 | 并行处理并发数 |
| RATE_LIMIT_PER_MINUTE | 22 | API 限流（次/分） |
| MAX_STACK_TOKENS | 1500 | 堆栈截断 Token 数 |
| JACCARD_THRESHOLD | 0.3 | 快速排除阈值 |
| API_TIMEOUT | 15 | API 超时秒数 |

## 测试

```bash
# 单元测试
pytest

# 评估流水线
python -m tests.eval.runner route        # 意图路由
python -m tests.eval.runner e2e          # 端到端
python -m tests.eval.runner robustness   # 鲁棒性
python -m tests.eval.runner all          # 全面评估
```

## 技术栈

- **Agent 框架**: LangGraph + SQLite Checkpoint
- **LLM**: DeepSeek-Chat（可切换 OpenAI 兼容模型）
- **Web**: FastAPI + SSE
- **限流**: asyncio.Semaphore + 令牌桶
- **日志**: 结构化 JSONL + 按天分文件
