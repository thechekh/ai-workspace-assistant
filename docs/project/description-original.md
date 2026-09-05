# AI Workspace Assistant Platform (Agentic FastAPI + MCP + Vector DB)

**This is the original brief, in Ukrainian, as received on or around
2026-09-01 — it describes the target system that was asked for, not what
shipped.** For the English translation, see
[description.md](description.md); for what was actually built, see
[implementation-plan.md](implementation-plan.md).

## 1. Ідея

Створити внутрішнього AI асистента для інженерів, який може:

- відповідати на питання по код-базі / документації
- викликати internal tools
- інтегруватися з різними системами через MCP tools
- використовувати Vector DB для knowledge retrieval
- працювати через WebSocket real-time chat

Фактично це mini AI agent platform.

Це дає департаменту:

- демонстрацію agentic architecture
- використання MCP
- приклад AI-native backend

## 2. Архітектура

```
User (Web UI / CLI)
        │
        │ WebSocket
        ▼
FastAPI AI Gateway
        │
        ├── Session Manager
        │
        ├── Agent Runtime
        │       │
        │       ├── LLM
        │       ├── Tool Router
        │       └── Memory
        │
        ├── Vector DB (knowledge retrieval)
        │
        └── MCP Tool Registry
                │
                ├── GitHub Tool
                ├── Jira Tool
                ├── Database Tool
                └── Code Search Tool
```

## 3. Основні компоненти

### 1️⃣ FastAPI WebSocket Chat Server

Реалтайм AI взаємодія.

```
Client
   ↓
WS
   ↓
GenAISession
   ↓
Agent
   ↓
LLM + Tools
```

Фічі:

- streaming responses
- multi-session
- tool invocation
- agent memory

### 2️⃣ Vector Knowledge Base

Наприклад:

- Qdrant
- Weaviate
- Chroma
- PgVector

Зберігаємо:

- engineering docs
- architecture docs
- coding guidelines
- onboarding docs

Pipeline:

```
Docs
 ↓
Chunking
 ↓
Embedding
 ↓
Vector DB
```

Agent використовує RAG retrieval.

### 3️⃣ MCP Tool Integration

Використання Model Context Protocol.

Tools приклади:

- search_code
- read_file
- jira_search
- github_prs
- run_sql

Agent workflow:

User: "Find service responsible for invoice generation"

Agent:

1. retrieve docs
2. search_code tool
3. summarize result

### 4️⃣ Agent Runtime

Simple ReAct agent.

Pipeline:

```
User Query
    ↓
Context Retrieval
    ↓
LLM reasoning
    ↓
Tool call
    ↓
Result
    ↓
Final answer
```

### 5️⃣ Memory Layer

Short-term memory:

- Redis

Long-term memory:

- Vector DB

## 4. Tech Stack

**Backend**

- FastAPI
- WebSockets
- asyncio
- Pydantic

**AI**

- OpenAI / Claude
- Embeddings
- RAG

**Infra**

- Docker
- Redis
- Vector DB

**Tools**

- MCP
- LangGraph / custom agent

## 5. Очікуваний результат

Bench engineer має зробити:

### 1️⃣ Working system

`ws://ai.local/chat`

який може:

- відповідати на питання
- шукати в документації
- викликати tools

### 2️⃣ MCP Tool demo

Наприклад:

User: "Show latest PRs in repo"

Agent: calls github tool

### 3️⃣ RAG demo

User: "What is our deployment architecture?"

Agent: retrieves architecture docs

## 6. Workshop для департаменту

Після bench-задачі робиться воркшоп:

### Part 1 — AI Architecture

Topics:

- RAG
- Agentic systems
- MCP
- Tool calling
- Memory

### Part 2 — Live Demo

AI Engineer Assistant

Examples:

- "How does payment adapter work?"
- "Find service responsible for authentication"
- "Search code for SQS integration"

### Part 3 — Implementation walkthrough

Показати:

- FastAPI WS architecture
- agent runtime
- vector DB pipeline
- MCP tool registry

## 7. Чому це сильна задача

Вона покриває ваш AI vector:

| Vector          | Covered |
| --------------- | ------- |
| Agentic backend | ✅      |
| Vector DB       | ✅      |
| MCP             | ✅      |
| Cloud AI        | ✅      |
| AI tooling      | ✅      |

## 8. Чому це хороший bench project

1. Реально корисний
2. Показує AI engineering maturity
3. Дає reusable platform
4. Можна розширювати

## 9. Related

- [description.md](description.md) — the English translation, for reference alongside this documentation
- [implementation-plan.md](implementation-plan.md) — what actually shipped, phase by phase, against this brief
- [tech-stack.md](tech-stack.md) — the technology decisions made to deliver it
- [future-tools.md](future-tools.md) — what was deliberately left out, and why
- [../handbook/01-project-overview.md](../handbook/01-project-overview.md) — the architecture that resulted
