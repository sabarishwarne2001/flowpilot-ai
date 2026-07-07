# AI_DEVELOPMENT_RULES.md

# FlowPilot AI
## AI Development Rules

**Project:** FlowPilot AI

**Purpose**

This document defines the mandatory engineering rules that every AI assistant (Gemini, ChatGPT, Claude, GitHub Copilot, Cursor, Windsurf, etc.) must follow when generating or modifying code for FlowPilot AI.

These rules preserve architectural consistency and prevent regressions introduced by AI-generated code.

This document has higher priority than generic coding suggestions.

---

# Primary Objective

When generating code:

1. Preserve the existing architecture.
2. Extend the project.
3. Never rewrite working systems unless explicitly instructed.

Architectural consistency is more important than shorter code.

---

# Rule 1 — Preserve Layered Architecture

The backend architecture is:

Client

↓

FastAPI Router

↓

Service Layer

↓

CRUD Layer

↓

SQLAlchemy ORM

↓

PostgreSQL

Never bypass layers.

---

## API Routes

Routes should only:

- validate requests
- call services
- return responses

Routes must never contain:

- business logic
- SQLAlchemy ORM operations
- AI processing
- notification logic

---

## Services

Services contain business logic only.

Services coordinate workflows.

Services should never become database access layers.

---

## CRUD

CRUD is the only layer allowed to communicate with SQLAlchemy.

Never execute ORM queries directly from:

- API routes
- AI services
- Notification services

---

# Rule 2 — Respect Single Responsibility

Each service owns exactly one responsibility.

Examples

Extraction Service

↓

Extract text

NOT

Generate embeddings.

---

Embedding Service

↓

Generate vectors

NOT

Call LLMs.

---

LLM Service

↓

Interact with AI providers

NOT

Persist database records.

---

Automation Service

↓

Evaluate rules

NOT

Send SMTP directly.

---

Document Processor

↓

Coordinate the pipeline

NOT

Implement OCR.

---

# Rule 3 — Never Bypass the Pipeline

Every uploaded document must follow:

Upload

↓

Extraction

↓

OCR

↓

Chunking

↓

Embeddings

↓

Vector Storage

↓

Classification

↓

Entity Extraction

↓

Summary

↓

Persistence

↓

Automation

↓

Notification

Never skip pipeline stages unless explicitly instructed.

---

# Rule 4 — Provider Abstraction

Never call external providers directly.

Current providers include:

LLM

- Groq
- Gemini

Notification

- Email

Future

- Resend
- SendGrid
- SES
- Slack
- Teams

All integrations must remain behind abstraction layers.

---

# Rule 5 — Configuration

Never hardcode:

- API keys
- passwords
- model names
- SMTP hosts
- database URLs
- ports

Everything must originate from Settings.

---

# Rule 6 — Database Safety

Always preserve transaction consistency.

If multiple database operations belong to one workflow:

- rollback on failure
- avoid partial updates
- preserve audit history

---

# Rule 7 — Logging

Every major operation should produce structured logs.

Log:

- start
- completion
- failures
- important execution statistics

Avoid print().

---

# Rule 8 — Pydantic

Use:

Pydantic v2

Follow existing schema patterns.

Avoid introducing deprecated v1 syntax.

---

# Rule 9 — SQLAlchemy

Use SQLAlchemy 2.0 typing.

Continue using:

Mapped

mapped_column

relationship

Avoid legacy declarative syntax.

---

# Rule 10 — Async

Preserve existing async patterns.

Long-running AI operations belong in background tasks.

Do not block HTTP requests.

---

# Rule 11 — Automation Engine

Automation rules must remain generic.

Current engine supports:

- ORM fields
- extracted entity fields
- nested JSON paths

Never hardcode logic for:

Resume

Invoice

Contract

Receipt

Purchase Order

Medical Forms

Future document types should work automatically.

---

# Rule 12 — Notification System

Automation

↓

Dispatcher

↓

Provider

↓

External Service

Future providers should register with the dispatcher.

Automation logic must remain unchanged.

---

# Rule 13 — LLM Service

All LLM communication must occur inside:

LLMService

Prompt templates remain centralized.

Never duplicate prompts across services.

---

# Rule 14 — ChromaDB

Vector storage remains independent from relational data.

Do not move embeddings into PostgreSQL.

Do not store document text inside ChromaDB beyond what is necessary for retrieval.

---

# Rule 15 — Backward Compatibility

When changing APIs:

Prefer extending schemas instead of breaking existing ones.

Avoid unnecessary breaking changes.

---

# Rule 16 — Security

Preserve:

JWT

OAuth2PasswordBearer

bcrypt hashing

Protected endpoints

Never expose secrets.

Never log sensitive credentials.

---

# Rule 17 — Code Style

Maintain consistency with the existing project.

Prefer:

clear naming

small services

typed functions

structured logging

descriptive docstrings

Avoid introducing inconsistent styles.

---

# Rule 18 — Documentation

Whenever architecture changes:

Update:

CURRENT_PROJECT_STATE.md

PROJECT_CHANGELOG_FOR_GEMINI.md

If engineering rules change:

Update:

AI_DEVELOPMENT_RULES.md

Documentation must evolve together with the code.

---

# Rule 19 — When Unsure

When multiple implementation approaches are possible:

Choose the option that:

- preserves architecture
- minimizes future rewrites
- improves maintainability
- follows existing project patterns

Never introduce shortcuts that compromise long-term design.

---

# Rule 20 — Project Philosophy

FlowPilot AI is intended to become a production-grade SaaS platform.

Future code should prioritize:

- maintainability
- scalability
- extensibility
- security
- observability
- provider independence
- clean architecture

over quick or temporary implementations.

---

# Mandatory Context for AI-Assisted Development

Before generating or modifying code, an AI assistant should review:

1. CURRENT_PROJECT_STATE.md
2. PROJECT_CHANGELOG_FOR_GEMINI.md
3. AI_DEVELOPMENT_RULES.md

These three documents together define the current architecture, its evolution, and the engineering standards for the project.

If any generated code conflicts with these documents, the documents take precedence unless the project owner explicitly requests an architectural change.