# Agent Communication RPC Framework

一种基于 gRPC 的高性能 AI Agent 通信框架，支持多 Agent 协作、MCP 工具调用和 RAG 智能工具选择。

## 项目概述

Agent Communication RPC Framework 是一个专为多 Agent 协作场景设计的通信框架。

### 核心能力

| 能力 | 说明 |
|------|------|
| gRPC 通信 | 基于 gRPC 的高性能远程过程调用 |
| A2A 协议 | Agent-to-Agent 通信协议支持 |
| MCP 集成 | Model Context Protocol 工具调用 |
| RAG-MCP | 基于检索增强生成的智能工具选择 |
| 服务发现 | 支持内存注册中心 |
| 多 Agent 协作 | Orchestrator 协调多个专业 Agent |



---

## 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 用户应用层                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                             RPC Client                              │    │
│  │                      (build/client/rpc_client)                      │    │
│  └──────────────────────────────┬──────────────────────────────────────┘    │
└─────────────────────────────────┼───────────────────────────────────────────┘
                                  │ gRPC/Protobuf
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 RPC 服务层                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                             RPC Server                              │    │
│  │                      (build/server/rpc_server)                      │    │
│  │  ┌───────────────┐   ┌───────────────┐   ┌───────────────┐          │    │
│  │  │AIQueryService │   │  A2A Adapter  │   │ HealthService │          │    │
│  │  └───────┬───────┘   └───────┬───────┘   └───────────────┘          │    │
│  └──────────┼───────────────────┼──────────────────────────────────────┘    │
└─────────────┼───────────────────┼───────────────────────────────────────────┘
              │                   │ A2A Protocol (HTTP/JSON-RPC)
              │                   ▼
┌─────────────┼───────────────────────────────────────────────────────────────┐
│             │                  Agent 协调层                                 │
│  ┌──────────┴──────────────────────────────────────────────────────────┐    │
│  │                         Orchestrator Agent                          │    │
│  │                    (ai_orchestrator, 端口 5000)                     │    │
│  │  ┌───────────────┐   ┌───────────────┐   ┌───────────────┐          │    │
│  │  │   意图识别     │   │  Agent 路由   │   │   MCP 工具    │          │    │
│  │  │  (Qwen API)   │   │               │   │               │          │    │
│  │  └───────────────┘   └───────┬───────┘   └───────────────┘          │    │
│  └──────────────────────────────┼──────────────────────────────────────┘    │
│                                 │                                           │
│           ┌─────────────────────┼─────────────────────┐                     │
│           ▼                     ▼                     ▼                     │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐            │
│  │   Math Agent    │   │  Weather Agent  │   │   Other Agent   │            │
│  │ (ai_math_agent) │   │    (可扩展)     │    │    (可扩展)     │            │
│  │    端口 5001    │   │    端口 5002    │    │                 │            │
│  │   + MCP Tools   │   │   + MCP Tools   │   │   + MCP Tools   │            │
│  └─────────────────┘   └─────────────────┘   └─────────────────┘            │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                  Registry Server（返回对应Agent的地址）               │    │
│  │    协调器识别出意图后，会向注册中心查找具备相应能力的在线 Agent          │    │
│  │                            (端口 8500)                               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 1. 这个项目到底在做什么

它是一个基于多 Agent 通信与工具接入框架：接收问题，判断问题类型，交给对应 Agent；需要计算时调用独立工具进程，最后调用 LLM 生成中文回答，并保存聊天历史、任务状态。

它把一次问答拆成可以独立运行、通过协议通信的组件，展示的是“用户 → 服务入口 → 协调器 → 专业 Agent → 工具 → 模型 → 用户”的工程链路。

接入千问后，推理在阿里云执行。这里没有训练模型，也不需要把大模型下载到本机。

Agent 是带有职责、处理流程和外部能力的程序。LLM 是 Agent 调用的模型服务。协调器、数学与天气 Agent 都使用同一个 ModelClient 实现；千问本身不算独立的本地 Agent。

## 2. 先认清进程、端口和协议

| 组件 | 默认地址 | 做什么 | 不负责什么 |
|---|---|---|---|
| CLI 客户端 | 无监听端口 | 接收终端输入，通过 gRPC 发请求 | 不做分类或计算 |
| RPC Server | 127.0.0.1:50051 | 转换 Protobuf 与 A2A 请求，返回结果/流事件/任务状态 | 不直接调用计算器 |
| Orchestrator | 127.0.0.1:5000 | 读取历史、分类、路由、写聊天记录 | 不启动 MCP 工具进程 |
| Math Agent | 127.0.0.1:5001 | 检索候选、调用计算器、让模型组织数学答案 | 不保存重复的聊天历史 |
| Weather Agent | 127.0.0.1:5002 | 城市定位、逐日天气、穿衣建议 | 不凭模型记忆生成实时天气 |
| Registry | 127.0.0.1:8500 | 注册地址、心跳、按标签选择在线 Agent | 不保存聊天记录、不调用 LLM |
| MCP Server | stdin/stdout，无网络端口 | 提供 14 个工具，执行工具函数；天气 Agent 只开放 3 个环境工具 | 不负责用户意图分类 |
| SQLite | data/agent_comm.sqlite3 | 保存 messages、tasks | 不存模型权重，不是向量数据库 |
| 千问 API | 远程 HTTPS | 分类、通用问答、根据工具结果组织答案 | 不代替本地 calculator 执行算式 |

启动脚本管理 5 个服务进程；Math Agent 与 Weather Agent 各启动 1 个 MCP 子进程。客户端和启动监督脚本还有各自的进程。当前有 3 个 Agent（协调器、数学、天气）。

如下流程图仅仅展示子math Agent的工作原理，其余子Agent工作原理同流程。


```text
CLI
 │ gRPC + Protobuf(gRPC 负责远程方法调用，Protobuf 负责其中的数据结构与编码)
 ▼
RPC Server :50051
 │ A2A 风格 HTTP + JSON-RPC(通过 HTTP 发送采用 JSON-RPC 格式的请求)
 ▼
Orchestrator :5000 ───────────────→ SQLite（历史、根任务）
 │
 ├─ HTTPS → 千问：对输入问题进行意图分类（目前简单测试三种问题，数学、天气、代码/普通对话）： math / weather / code / general
 │
 ├─ general/code → 千问：生成回答 ───────────────────┐
 │                                                  │
 └─ math                                            │
     │ HTTP → Registry :8500：查在线 math 地址       │
     ▼                                              │
   Math Agent :5001 ────────────→ SQLite（子任务）   │
     │                                              │
     ├─ Retriever：检索候选工具                      │
     ├─ 千问根据候选 Schema 选择工具并构造参数         │
     │ MCP / JSON-RPC / stdin+stdout                │
     ▼                                              │
   MCP Server → 被选中的工具 → 返回计算结果           │
     │                                              │
   Math Agent → 千问：结合计算结果生成回答             │
     │ A2A 返回                                      │
     ▼                                               │
   Orchestrator ←────────────────────────────────────┘
     │ 保存最终回答、更新任务
     ▼
   RPC Server → CLI
```

gRPC、A2A、MCP 是不同层的通信约定：gRPC 连接用户入口；A2A 连接 Agent 服务；MCP 连接 Agent 与工具进程。本项目没有使用完整 A2A SDK，而是实现了文档化的 HTTP/JSON-RPC 子集。

## 3. 服务启动时，发生了什么

入口是 scripts/start_system.py。

1. Settings.from_env() 读取配置。操作系统环境变量优先于项目根目录 .env；未配置的项使用默认值。
2. 检查五个端口是否互不相同且未被占用。
3. 启动 Registry，轮询 /health。
4. 启动 Math Agent：创建存储、ModelClient、MCPClient；MCPClient 用当前 Python 解释器启动agent_comm.mcp_server。
5. MCP 完成 initialize → notifications/initialized → tools/list，取得工具定义。
6. 如开启 RAG，对工具名、描述、inputSchema 建立内存向量索引。默认使用本地哈希向量。
7. Math Agent 向 Registry 注册，并启动心跳；启动脚本还检查注册中心已有 math 标签。
8. 启动 Weather Agent（5002），建立独立 MCP/天气工具索引并登记；再启动 Orchestrator，创建存储与模型客户端，开始注册和心跳。
9. 启动 RPC Server，检查 gRPC Health。

默认每 5 秒心跳一次，TTL 为 20 秒。注册中心在相关查询/心跳时清除过期记录，按标签选择时支持轮询。目前默认只有一个 math 实例。

注意：health 中 status=ok 或 provider=qwen 只表示服务就绪及配置选择；没有发送真实模型探测请求。MCP 或 RAG 启动失败时也可能保留 status=ok，要同时看 mcp_enabled、tools、rag_enabled、warning。

## 4. 跟着一次“请计算 (1+7)*2”走完全部链路

### 第一步：CLI 构造 gRPC 请求

agent_comm/rpc_client.py 中的 RpcClient.query() 创建 AIQueryRequest：

```text
request_id          本次外部请求编号
question            请计算 (1+7)*2
context_id          lesson-qwen-01
history_length 10  （消息条数，不是 10 轮）
timeout_seconds     客户端指定时间
```

合同定义在 proto/ai_query.proto。generated 下的 Python 文件由生成脚本产生，一般不直接修改。

### 第二步：RPC Server 转换为 A2A 消息

rpc_server.py 的 QueryService.params() 校验非空、长度、历史条数、超时范围，然后创建 Message 和 SendParams。请求携带 contextId、parts、historyLength、deadline 等字段。

QueryService.Query() 调用 A2AClient.request()，向协调器 POST JSON-RPC，method 为 message/send。

### 第三步：协调器创建根任务、读取历史

agents.py 的 AgentEngine.process()：

1. 创建 running 状态的根任务。
2. 按 contextId 读取最近的历史消息。
3. 保存本次用户消息。
4. 进入 _orchestrate()。

先读历史后保存输入，避免当前问题重复进入模型 prompt。

### 第四步：第一次真实 LLM API 调用——分类

ModelClient.classify() 请求千问，要求返回 math、weather、code、general 中的一种意图。分类不带聊天历史，只看当前输入。

### 第五步：服务发现与 Agent 转发

协调器 GET Registry 的 /v1/agent/select/math，得到 Math Agent 地址。随后通过 A2A 再发 message/send，传递同一个会话和截止时间。

Math Agent 创建自己的子任务；它读取共享 SQLite 的该会话历史，再按 messageId 过滤当前用户消息，避免重复。

### 第六步：工具检索与模型工具决策（RAG-MCP）

_math() 可调用 Retriever.retrieve()，按余弦相似度返回工具候选。

qwen 模式将候选工具的名称、描述和 inputSchema 转成 API 原生 tools，调用 ModelClient.answer_with_tools()。千问选择工具并生成参数，例如 calculator(expression="(1+7)*2")，或 multiply(a=6,b=5478)。程序限制工具名必须属于本次候选，并校验参数 Schema。

模型可以为自然语言数学输入生成工具参数，并支持分步工具调用。mock 模式仍使用 extract_expression() 的规则提取与 calculator 演示。问题能否完成仍取决于分类、检索到的工具及模型决策。

### 第七步：跨进程执行工具

MCPClient.call_tool() 发给 MCP 子进程：

```json
{"jsonrpc":"2.0","id":"mcp-...","method":"tools/call","params":{"name":"calculator","arguments":{"expression":"(1+7)*2"}}}
```

ToolRegistry 先按 JSON Schema 校验参数，之后执行 calculator handler。evaluate() 使用 Python AST 白名单求值，返回类似：

```json
{"isError":false,"content":[{"type":"text","text":"(1+7)*2 = 16"}]}
```

它没有使用 eval，也不会执行任意 Python。除零或非法表达式返回 isError=true，Math Agent 将其转成业务失败。

### 第八步：工具结果回传模型并生成答案

工具执行结果以 role=tool 结构化消息回传千问，并用 tool_call_id 对应模型的调用。循环中的消息大致为：

```text
system：   数学助手的工具使用说明
历史：     最近若干条 user/assistant 消息
user：     请计算 (1+7)*2
assistant：tool_calls=[calculator(expression="(1+7)*2")]
tool：     {"isError":false,"content":[{"type":"text","text":"(1+7)*2 = 16"}]}
```

模型可以继续返回新的工具调用，或生成最终答案；最多 4 轮工具调用、累计 8 次执行，并受整次请求的 deadline 限制。工具失败立即返回业务错误，不自动重试。

llm.py 用 httpx POST {QWEN_BASE_URL}/chat/completions，Bearer 鉴权；参数包含 model、messages、temperature=0.1、max_tokens=1000。工具阶段增加 tools、tool_choice、parallel_tool_calls；判断 choices[0].message 中的 tool_calls 或最终 content。

### 第九步：保存并返回

Math Agent 将子任务更新为 completed，把回答和 tools_used 等元数据交回协调器。

协调器保存一条最终 agent 回答，根任务也更新为 completed。metadata 中保留 child_task_id。RPC 转成 AIQueryResponse，CLI 显示答案、agent_id、provider、tools_used、根 task_id 和 context_id。

协调器不会在收到数学 Agent 的回答后再额外调用一次模型。一次单轮工具问答通常有 3 次 LLM 请求：分类、工具决策、结果总结；多轮工具调用会增加请求。Embedding 请求另计。

## 5. 普通问答、代码、历史与流式走哪条分支

| 输入/能力 | 实际路径与边界                                                                        |
|---|--------------------------------------------------------------------------------|
| 你好、解释一个概念 | 协调器分类 → 协调器调用模型回答，共两次 LLM 请求                                                   |
| 编程问题 | 分类为 code，但没有 Code Agent（后续可扩展），仍由协调器通用回答                                       |
| 数学问题 | 分类 → Registry → Math → RAG 候选 → 千问工具决策 → MCP → 模型组织答案                          |
| 查询天气 | 分类 weather → Weather Agent → RAG候选 → 千问工具决策 → MCP get_weather → 城市定位与预报 → 穿衣建议 |
                                                         |
| --stream | 先返回 processing 状态，完整答案生成后每 32 个 Unicode 字符分块，最后 complete                       |
| --task-id | gRPC → 协调器 tasks/get → 存储；不调用 LLM                                              |
| --health | gRPC 检查协调器 health；不验证模型 Key 和实际推理                                              |

默认每次读取最近 10 条消息，通常相当于 5 轮成功问答；每个会话最多保留最近 1000 条消息。分类已读取历史，支持天气“那上海呢”“明天呢”等追问；工具检索仍主要使用当前输入。

request_id 是外部请求编号；messageId 是消息编号；contextId 是整段会话编号；taskId 是一次处理任务编号。一次数学请求包含一个根任务和一个子任务。

## 6. RAG、Embedding、存储分别是什么

这里的 RAG 检索对象是工具定义，不是 PDF 或业务文档。工具描述向量存在 Retriever.index 的内存列表中，查询采用精确遍历和余弦排序；没有部署独立向量数据库。

默认 EMBEDDING_PROVIDER=local：词元和哈希特征，演示检索流程，不具备训练后语义模型的能力。缓存为 LRU + TTL，默认最多 1000 项、3600 秒。改成 dashscope 才调用真实 Embedding。

建议先只接通千问对话，保留 local Embedding、SQLite 和 mock 天气。对话模型与 Embedding 是两套独立请求配置：QWEN_API_KEY 用于本项目对话；DASHSCOPE_API_KEY 用于本项目远程 Embedding。只填后者不会自动满足千问对话配置。

只有协调器写聊天 messages，各个 Agent 都写各自 tasks。SQLite 使用 WAL；Redis 是可选后端，切换后不会自动迁移旧数据。

## 7. 本机接入真实千问：按顺序操作快速开始

### 7.1 准备模型服务信息

在阿里云百炼获取 API Key，确认模型、Key 与地域匹配。官方入口：[获取与配置 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)。此处需要百炼模型服务 Key，不是阿里云 AccessKey ID/Secret。

项目已有 qwen-plus 默认配置。2026-10-30 核对的官方文档仍列出北京旧域名可用，同时建议使用业务空间专属域名；请以控制台中与你的业务空间和地域匹配的地址为准。参考：[OpenAI 兼容 Chat API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)。

### 7.2 确认 Python，无需激活环境

打开PowerShell：

```powershell
Set-Location -LiteralPath "D:\代码存放\A2A"
.\.venv\Scripts\python.exe scripts/start_system.py --check
```

它会启动全部服务，检查完整调用链、任务状态和分块返回，最后自动关闭服务。预期看到：

```text
[OK] registry
[OK] math
[OK] orchestrator
[OK] rpc
冒烟测试通过：gRPC -> A2A -> Math Agent -> MCP -> calculator
所有服务已停止。
```
端口被占用时会清楚报错，不会关闭别的程序。

### 7.3 正式开始演练

```powershell
.\.venv\Scripts\python.exe scripts/start_system.py
```

保持这个窗口打开。看到 5 个 `[OK]` 后，在另一个 PowerShell 窗口运行：

```powershell
Set-Location -LiteralPath "D:\代码存放\A2A"
.\.venv\Scripts\python.exe -m agent_comm.rpc_client --question "1+7" --context lesson-01
```

预期回答包含：

```text
[本地演示模式] 已通过 MCP 调用 calculator，计算结果：1+7 = 8
[agent=math-1, provider=mock, tools=['calculator']]
[task_id=task-..., context_id=lesson-01, ...ms]
```

### 直接输入问题

先启动系统，另开 PowerShell 在项目目录执行：

```powershell
.\chat.cmd
```

看到 `>` 后直接输入 `帮我算一下6×5478`、`请用阶乘工具求5的阶乘`。不需要每次写 `-q`。会话编号自动生成，单次超时默认读取 `.env` 的 `REQUEST_TIMEOUT`；也可用 `.\chat.cmd --context my-qwen-chat` 继续指定会话。



## 8. 推荐源码阅读顺序

| 顺序 | 文件 / 核心函数 | 要回答的问题 |
|---|---|---|
| 1 | proto/ai_query.proto | 请求、响应有哪些字段？ |
| 2 | rpc_client.py / RpcClient.query | 命令行如何发出请求？ |
| 3 | rpc_server.py / Query、params | gRPC 如何转成 A2A？ |
| 4 | agents.py / process、_orchestrate | 谁创建任务、读历史、分类和路由？ |
| 5 | llm.py / classify、complete、answer | 真实 API 发往哪里，prompt 如何组装？ |
| 6 | registry.py / register、select | 专业 Agent 地址从何而来？ |
| 7 | agents.py / _math | 怎样选择工具、提取参数、生成答案？ |
| 8 | mcp_client.py、mcp_server.py | 工具如何跨进程执行，响应如何关联？ |
| 9 | tools/base.py、tools/calculator.py | Schema 校验、AST 计算与错误如何实现？ |
| 10 | rag.py | 候选工具怎样向量化、排序、缓存？ |
| 11 | storage.py | 历史与任务怎样保存，怎样避免重复？ |
| 12 | scripts/start_system.py、tests/ | 多进程怎样启动、验收、停止？ |

