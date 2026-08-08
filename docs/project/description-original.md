# AI Workspace Assistant Platform (Agentic FastAPI + MCP + Vector DB)

## Ідея

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

## Архітектура

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

## Основні компоненти

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

## Tech Stack

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

## Очікуваний результат

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

## Workshop для департаменту

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

## Чому це сильна задача

Вона покриває ваш AI vector:

| Vector          | Covered |
| --------------- | ------- |
| Agentic backend | ✅      |
| Vector DB       | ✅      |
| MCP             | ✅      |
| Cloud AI        | ✅      |
| AI tooling      | ✅      |

## Чому це хороший bench project

1. Реально корисний
2. Показує AI engineering maturity
3. Дає reusable platform
4. Можна розширювати
