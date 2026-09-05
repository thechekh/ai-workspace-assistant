# AI Workspace Assistant Platform (Agentic FastAPI + MCP + Vector DB)

**This is the project brief as received on or around 2026-09-01 — it
describes the target system that was asked for, not what shipped.** For the
original Ukrainian text, see
[description-original.md](description-original.md); for what was actually
built, see [implementation-plan.md](implementation-plan.md).

## 1. Idea

Build an internal AI assistant for engineers that can:

- answer questions about the codebase and documentation
- invoke internal tools
- integrate with various systems via MCP tools
- use a vector database for knowledge retrieval
- operate through a real-time WebSocket chat

In essence, it is a mini AI agent platform.

It gives the department:

- a demonstration of agentic architecture
- practical use of MCP (Model Context Protocol)
- an example of an AI-native backend

## 2. Architecture

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

## 3. Core Components

### 1. FastAPI WebSocket Chat Server

Real-time AI interaction.

```
Client
   ↓
WebSocket
   ↓
GenAI Session
   ↓
Agent
   ↓
LLM + Tools
```

Features:

- streaming responses
- multiple concurrent sessions
- tool invocation
- agent memory

### 2. Vector Knowledge Base

Candidate technologies:

- Qdrant
- Weaviate
- Chroma
- pgvector

Content to store:

- engineering documentation
- architecture documentation
- coding guidelines
- onboarding documents

Ingestion pipeline:

```
Docs
 ↓
Chunking
 ↓
Embedding
 ↓
Vector DB
```

The agent uses RAG (Retrieval-Augmented Generation) to pull relevant knowledge at query time.

### 3. MCP Tool Integration

Uses the Model Context Protocol.

Example tools:

- `search_code`
- `read_file`
- `jira_search`
- `github_prs`
- `run_sql`

Example agent workflow:

> User: "Find the service responsible for invoice generation."

The agent:

1. retrieves relevant documentation
2. calls the `search_code` tool
3. summarizes the result

### 4. Agent Runtime

A simple ReAct-style agent.

Pipeline:

```
User Query
    ↓
Context Retrieval
    ↓
LLM Reasoning
    ↓
Tool Call
    ↓
Result
    ↓
Final Answer
```

### 5. Memory Layer

- **Short-term memory:** Redis
- **Long-term memory:** Vector DB

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

**Infrastructure**

- Docker
- Redis
- Vector DB

**Tooling**

- MCP
- LangGraph or a custom agent implementation

## 5. Expected Outcome

The bench engineer is expected to deliver:

### 1. A working system

A chat endpoint at `ws://ai.local/chat` that can:

- answer questions
- search the documentation
- invoke tools

### 2. An MCP tool demo

For example:

> User: "Show the latest PRs in the repo."

The agent calls the GitHub tool.

### 3. A RAG demo

> User: "What is our deployment architecture?"

The agent retrieves the architecture documentation.

## 6. Department Workshop

After the bench task is complete, a workshop is held for the department:

### Part 1 — AI Architecture

Topics:

- RAG
- Agentic systems
- MCP
- Tool calling
- Memory

### Part 2 — Live Demo

The AI Engineer Assistant in action. Example queries:

- "How does the payment adapter work?"
- "Find the service responsible for authentication."
- "Search the code for the SQS integration."

### Part 3 — Implementation Walkthrough

Walk through:

- the FastAPI WebSocket architecture
- the agent runtime
- the vector DB pipeline
- the MCP tool registry

## 7. Why This Is a Strong Task

It covers the team's entire AI competency vector:

| Competency      | Covered |
| --------------- | ------- |
| Agentic backend | ✅      |
| Vector DB       | ✅      |
| MCP             | ✅      |
| Cloud AI        | ✅      |
| AI tooling      | ✅      |

## 8. Why This Is a Good Bench Project

1. It is genuinely useful in day-to-day work.
2. It demonstrates AI engineering maturity.
3. It produces a reusable platform.
4. It can be extended over time.

## 9. Related

- [description-original.md](description-original.md) — the same brief as received, in the original Ukrainian
- [implementation-plan.md](implementation-plan.md) — what actually shipped, phase by phase, against this brief
- [tech-stack.md](tech-stack.md) — the technology decisions made to deliver it
- [future-tools.md](future-tools.md) — what the brief asked for that was deliberately deferred
- [../handbook/01-project-overview.md](../handbook/01-project-overview.md) — the architecture that resulted
