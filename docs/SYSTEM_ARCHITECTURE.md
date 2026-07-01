# FlowPilot AI
# System Architecture

Version: 1.0

---

# 1. Architecture Overview

FlowPilot AI follows a modular service-oriented architecture.

The platform processes uploaded business documents asynchronously using AI services and stores both structured and semantic knowledge.

Core principles:

- Modular architecture
- Asynchronous processing
- Separation of concerns
- Independent AI services
- Generic Work Item model
- Production-ready design

---

# 2. High-Level Architecture

Frontend (React)

↓

REST API (FastAPI)

↓

Business Services

↓

AI Services

↓

Automation Engine

↓

Databases

- PostgreSQL
- ChromaDB

---

# 3. Core Components

- Frontend
- Backend API
- Authentication
- Work Item Service
- Processing Job Service
- AI Services
- Automation Engine
- Notification Service
- AI Assistant
- PostgreSQL
- ChromaDB

---

# 4. Work Item Architecture

Every uploaded file becomes a Work Item.

A Work Item represents the permanent business object.

Processing information is stored separately in Processing Jobs.

---

# 5. Processing Pipeline

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

# 6. AI Services

Independent services:

- OCR Service
- Classification Service
- Entity Extraction Service
- Summarization Service
- Embedding Service
- Assistant Service

Each service should be replaceable without affecting the rest of the platform.

---

# 7. Database Architecture

PostgreSQL

Stores:

- Users
- Work Items
- Processing Jobs
- Entities
- Notifications
- Automation Rules

ChromaDB

Stores:

- Chunk embeddings
- Metadata
- Semantic search index

---

# 8. Background Processing

FlowPilot AI uses asynchronous processing.

Each upload creates:

- Work Item
- Processing Job

Processing executes in the background.

Users can continue using the application while AI processing completes.

---

# 9. Security

- JWT Authentication
- Password Hashing
- Environment Variables
- File Validation
- UUID Primary Keys

---

# 10. Scalability

Architecture supports future upgrades:

- Celery
- Redis
- RabbitMQ
- Kubernetes
- Object Storage
- Multi-Tenant Organizations