# CrashSight Analysis Agent

自然语言驱动的崩溃分析工具。基于 LLM Function Calling，用对话方式完成崩溃数据查询和报告生成。

## 快速开始

### 1. 安装依赖

```bash
cd CrashSight-Agent
pip install -r requirements.txt
```

### 2. 配置 LLM

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek/OpenAI API Key
```

### 3. 运行

```bash
python cli.py
```

### 4. 使用示例

```
你: 安卓体验服 3.7 昨天的崩溃
助手: [自动调用工具获取数据，生成报告]

你: Top1 正式服有没有
助手: [获取 Top1 堆栈，用 LLM 对比正式服候选，判断是否为历史问题]

你: TAPD 单什么状态
助手: [查询关联的 TAPD 缺陷单详情]
```

## 架构

```
用户自然语言
    │
    ▼
CrashSightAgent (ReAct 循环)
    │
    ├── LLM 理解意图 + 解析参数
    │
    ├── Function Calling → 选择工具
    │   ├── get_crash_trend      (崩溃率趋势)
    │   ├── get_top_issues       (TOP 问题列表)
    │   ├── get_issue_full_stack (完整堆栈)
    │   ├── check_history_issue  (LLM 历史问题判定)
    │   ├── get_tapd_bug_detail  (TAPD 详情)
    │   └── generate_crash_report(报告生成)
    │
    ├── 工具结果 → 反馈给 LLM
    │
    └── LLM 生成最终回答/报告
```

## 项目结构

```
CrashSight-Agent/
├── crashsight_agent/
│   ├── config.py          # 项目配置 + 鉴权
│   ├── auth.py            # OpenAPI 签名
│   ├── api_client.py      # CrashSight API 封装
│   ├── llm_client.py      # LLM 调用封装
│   ├── tools/             # 工具层
│   │   ├── trend_tool.py
│   │   ├── top_issues_tool.py
│   │   ├── stack_tool.py
│   │   ├── history_tool.py
│   │   ├── tapd_tool.py
│   │   └── report_tool.py
│   └── orchestration/     # Agent 编排层
│       ├── agent.py       # ReAct 循环
│       └── prompts.py     # System Prompt
├── cli.py                 # CLI 入口
├── .env.example           # 环境变量模板
├── requirements.txt
└── README.md
```

## 相比原工具的优势

| 原工具 | Agent 版 |
|--------|----------|
| 下拉框选参数 | 自然语言输入 |
| 手写 1000 行匹配算法判断历史问题 | LLM 语义理解判断 |
| 一次性全量执行 60s | 按需分步，可中途追问 |
| 规则模板生成分析 | LLM 生成有价值的分析 |
| 单轮无状态 | 多轮对话 |
