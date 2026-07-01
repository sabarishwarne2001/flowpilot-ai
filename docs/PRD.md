# FlowPilot AI
## Product Requirements Document (PRD)

Version: 1.0

Author: Sabarish

---

# 1. Product Vision

FlowPilot AI is an AI-powered document intelligence and workflow automation platform.

Businesses can upload documents such as PDFs and images.

The platform automatically understands the document, extracts meaningful information, stores structured and semantic data, and triggers configurable business workflows.

The goal is to eliminate repetitive manual document processing.

---

# 2. Target Users

- HR Teams
- Finance Teams
- Legal Teams
- Customer Support Teams
- Operations Teams
- Small & Medium Businesses

---

# 3. Supported Documents (MVP)

- PDF
- PNG
- JPG
- JPEG

Future

- DOCX
- XLSX
- Emails

---

# 4. Core Features

## Authentication

- Login
- Logout
- JWT Authentication

---

## Dashboard

The dashboard provides a real-time overview of business activity and document processing.

Dashboard Metrics:

- Total Work Items
- Documents Processed Today
- Documents Processing
- Pending Review
- Failed Processing
- Automation Success Rate

Dashboard Widgets:

- Recent Activity
- Processing Queue
- Document Type Distribution
- Recent Notifications
- Quick Upload

---

## Processing Queue

The platform provides a real-time processing queue that displays the status of every uploaded work item.

Each work item progresses through the following stages:

- Queued
- Extracting Text
- OCR Processing (if required)
- AI Classification
- Entity Extraction
- Generating Summary
- Creating Embeddings
- Saving Data
- Running Automation
- Completed
- Failed

The queue provides users with complete visibility into document processing and system activity.

Features:

- Real-time status updates
- Progress indicator
- Processing duration
- Success and failure status
- Error messages
- Retry failed processing

## Smart Upload

Users can upload documents.

The system automatically processes them.

---

## AI Processing

The AI processing pipeline converts uploaded documents into structured business knowledge.

Capabilities

- OCR (Images only)
- Text Extraction
- Intelligent Chunking
- Document Classification
- Entity Extraction
- Hierarchical Summarization
- Embedding Generation
- Metadata Extraction

---

## Semantic Knowledge Store

Store semantic embeddings.

Support semantic search.

---

## AI Assistant

The AI Assistant supports two operating modes.

Global Assistant

Searches across all processed work items using Retrieval-Augmented Generation (RAG).

Document Assistant

Answers questions about a single selected work item using document-specific semantic search.

Both assistants provide source references when applicable.

## Automation Engine

The platform executes rule-based workflows after AI processing.

Example:

Resume
→ Notify HR

Invoice
→ Notify Finance

Contract
→ Notify Legal

Unknown
→ Needs Review

---

## Notifications

In-App Notifications

Future:

- Email
- Slack
- Microsoft Teams

---

## Work Items

Every uploaded file becomes a Work Item.

A Work Item represents the central business object within FlowPilot AI.

Each Work Item contains:

- Document Metadata
- AI Summary
- Extracted Entities
- Automation Status
- Processing History
- Notification History

All platform features operate on Work Items rather than raw uploaded files.

---

# 5. AI Processing Pipeline

Upload File
      │
      ▼
Create Work Item
      │
      ▼
Create Processing Job
      │
      ▼
Job Queue
      │
      ▼
AI Processing Pipeline
      │
 ┌────┴─────────────────────────────┐
 │ OCR (if image)                   │
 │ Text Extraction                  │
 │ Chunking                         │
 │ Classification                   │
 │ Entity Extraction                │
 │ Chunk Summarization              │
 │ Embedding Generation             │
 │ Metadata Extraction              │
 └──────────────────────────────────┘
      │
      ▼
Merge AI Results
      │
 ┌────┴────────────┐
 ▼                 ▼
PostgreSQL     ChromaDB
      │             │
      └──────┬──────┘
             ▼
      Automation Engine
             │
             ▼
     Notification Center
             │
             ▼
        AI Assistant

### Pipeline Explanation

1. Detect uploaded file type.
2. Create work item / Create processing jobs.
3. Extract text (OCR only for images).
4. Split large documents into chunks.
5. Run AI tasks in parallel where possible:
   - Classify the document.
   - Extract structured entities.
   - Summarize chunks.
   - Generate embeddings.
   - Extract metadata.
6. Merge all AI outputs into a unified document object.
7. Store structured data in PostgreSQL.
8. Store embeddings in ChromaDB.
9. Execute automation rules.
10. Generate notifications.
11. Make the document searchable through the AI Assistant.

# 6. Technology Stack

Frontend

- React
- TypeScript
- Vite
- Tailwind CSS
- React Router
- TanStack Query

Backend

- FastAPI
- SQLAlchemy
- Pydantic

Database

- PostgreSQL
- ChromaDB
- Alembic

Authentication

- JWT
- bcrypt

OCR

- PaddleOCR

Embeddings

- Sentence Transformers

LLM

- Groq
- Gemini

Deployment

- Netlify
- Railway
- Supabase

---

# 7. Project Goals

- Production Ready
- Commercial UI
- Modular Architecture
- AI-First
- Scalable
- Deployable
- Portfolio Quality
- Client Ready

---

# 8. Design Principles

FlowPilot AI follows these engineering principles:

- AI should assist users, not replace business logic.
- Every uploaded file becomes a Work Item.
- Long-running operations execute asynchronously.
- Structured data and semantic data are stored separately.
- Every AI module should be independently replaceable.
- The platform should remain modular and scalable.
- User experience should prioritize responsiveness over blocking operations.

---

# 9. Non-Functional Requirements

- Responsive Design
- Secure Authentication
- Fast API Response Times
- Background Processing
- Production Logging
- Error Handling
- Modular Codebase
- API Documentation
- Easy Deployment
- Scalable Architecture

---
# 10. Out of Scope (Version 1)

- Multi-tenant Organizations
- Billing
- Subscription Plans
- RBAC
- Mobile App
- Webhooks
- Third-party Integrations