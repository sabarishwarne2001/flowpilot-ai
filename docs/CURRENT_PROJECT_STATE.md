# CURRENT_PROJECT_STATE.md

# FlowPilot AI
## Current Project State

**Project:** FlowPilot AI

**Version:** End of Sprint 4

**Status:** Backend Verified

**Document Purpose**

This document describes the current architecture of FlowPilot AI.

Unlike the project changelog, this document does not describe historical evolution.

It represents the project exactly as it exists today and serves as the authoritative reference for future development.

Whenever architectural changes are introduced, this document should be updated to reflect the new current state.

---

# System Overview

FlowPilot AI is an AI-powered document intelligence and workflow automation platform.

The system accepts business documents, extracts structured information, stores semantic knowledge, and executes configurable business automation workflows.

Current supported document categories include:

- Resume
- Invoice
- Contract
- Purchase Order
- Receipt
- Other

---

# Current Technology Stack

## Backend

- FastAPI
- Python 3.12
- SQLAlchemy 2.0
- Alembic
- Pydantic v2

---

## Database

- PostgreSQL

---

## Vector Database

- ChromaDB

---

## Embedding Model

- SentenceTransformers

---

## OCR

- PaddleOCR

---

## LLM Providers

Current supported providers:

- Groq
- Gemini

Provider selection is configuration-driven.

---

## Authentication

- JWT
- OAuth2PasswordBearer
- bcrypt password hashing

---

## Notification

Current architecture supports provider-based notifications.

Implemented:

- Email Provider

Future:

- Resend
- SendGrid
- Mailgun
- Amazon SES
- Slack
- Teams
- Discord
- SMS

---

# Current Processing Pipeline

Document Upload

↓

Extraction

↓

OCR (when required)

↓

Chunking

↓

Embedding Generation

↓

ChromaDB Storage

↓

Classification

↓

Entity Extraction

↓

Summarization

↓

Database Persistence

↓

Automation Rules

↓

Notification Dispatcher

↓

Audit Logging

---

# Current Backend Architecture

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

Every layer has a single responsibility.

API routes must never contain business logic.

---

# Service Responsibilities

## Document Processor

Coordinates the complete AI pipeline.

Responsible for:

- workflow orchestration
- status updates
- persistence
- automation execution

---

## Extraction Service

Responsible only for extracting text from supported document types.

---

## OCR Service

Responsible only for OCR processing.

---

## Chunking Service

Responsible only for chunk generation.

---

## Embedding Service

Responsible only for vector generation and ChromaDB interaction.

---

## LLM Service

Responsible only for communication with LLM providers.

Supports:

- Classification
- Entity Extraction
- Summarization

---

## Automation Service

Responsible only for evaluating automation rules and triggering configured actions.

Supports nested JSON field resolution.

---

## Notification Dispatcher

Routes notification requests to registered providers.

Business logic never communicates directly with notification providers.

---

# Database Models

Current major entities include:

- User
- WorkItem
- ProcessingJob
- AutomationRule
- AutomationExecutionLog
- Notification

Relationships remain normalized.

---

# Current AI Output

Each processed Work Item stores:

- extracted text
- extracted entities
- classification details
- summary

Semantic vectors remain stored separately inside ChromaDB.

---

# Automation Engine

Automation supports:

- ORM fields
- extracted entity fields
- nested JSON field paths

Example:

classification_details.document_classification

Automation remains completely document-type independent.

---

# Notification Architecture

Automation

↓

Notification Dispatcher

↓

Registered Provider

↓

External Provider

Current implementation:

Email

Future providers can be added without modifying automation logic.

---

# Configuration

Application configuration is entirely environment driven.

No infrastructure values should be hardcoded.

Settings include:

- Database
- JWT
- LLM
- SMTP
- ChromaDB
- Chunking
- Embeddings
- Logging

---

# Logging

All services produce structured logs.

Each pipeline stage records:

- start
- completion
- failure
- execution statistics

---

# Verification Status

Verified:

✓ Authentication

✓ JWT

✓ Work Items

✓ Upload

✓ OCR

✓ Classification

✓ Entity Extraction

✓ Summary

✓ ChromaDB

✓ Processing Jobs

✓ Automation Rules

✓ Notification Dispatcher

✓ REST APIs

✓ CRUD Operations

✓ Background Processing

---

# Deferred Work

Production Email Provider

Production Docker Optimization

Production Deployment

Infrastructure Monitoring

---

# Current Project Health

Architecture: Stable

Backend: Verified

AI Pipeline: Verified

Automation: Verified

Notification Dispatcher: Verified

Database: Verified

API Layer: Verified

Deployment: Pending

---

This document represents the current authoritative architecture of FlowPilot AI.

Future architectural changes should update this document immediately after implementation.