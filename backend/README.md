FlowPilot AI — Backend Core Foundation

Architectural Purpose

The README.md serves as the developer onboard documentation. It establishes the technical blueprint, explains the architectural responsibilities of each directory layer, and provides unambiguous commands to initialize, run, and verify the backend environment under both bare-metal local setups and containerized runtimes.

README.md

FlowPilot AI is a production-ready, modular document intelligence and workflow automation SaaS platform. This repository contains the ASGI backend API services engine built with FastAPI, Python 3.12, and Pydantic v2.

This foundation establishes the core scaffolding, settings validation engines, centralized logging handlers, base health check routes, and Docker deployment layers.



1. Directory Structure

The project utilizes a modular, decoupled architecture where code responsibilities are isolated by concern domain:

backend/
├── app/
│   ├── __init__.py
│   ├── main.py # ASGI application bootstrap
│   ├── api/ # Request routing and dependency controllers
│   │   ├── __init__.py
│   │   ├── deps.py # Dependency injection gateway (Auth/DB placeholders)
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── health.py # Observability health endpoint
│   │       └── router.py # Centralized API Version 1 endpoint mapper
│   ├── core/ # Configurations, log handlers, and global constants
│   │   ├── __init__.py
│   │   ├── config.py # Pydantic Settings configuration manager
│   │   ├── constants.py # Read-only metadata and system-wide constraints
│   │   └── logging_config.py # Standardized dictConfig console log formatter
│   ├── models/ # SQLAlchemy 2.0 ORM DB schemas (Scaffold)
│   ├── schemas/ # Pydantic parameter payload validation layers (Scaffold)
│   ├── services/ # Discrete domain execution engines (Scaffold)
│   ├── utils/ # Reusable auxiliary functional helpers (Scaffold)
│   └── workers/ # Long-running background task definitions (Scaffold)
├── .dockerignore # Docker image build file context exclusions
├── .env.example # Environmental configuration variable template
├── .gitignore # Git staging control file exclusions
├── Dockerfile # Production-grade multi-stage container build spec
├── docker-compose.yml # Local multi-service orchestrator mapping configuration
├── requirements.txt # Static production-pinned application packages
└── README.md # Operations documentation

2. Directory & Component Responsibilities


    app/main.py: Boots the ASGI server, maps CORS configurations dynamically from the environments, registers top-level routers, and binds global resource setups via non-blocking lifespan hooks.
    app/api/: The controller layer. Directs incoming HTTP paths to specific handlers. Contains deps.py for standard FastAPI dependency injection patterns.
    app/core/: The orchestrator engine. config.py enforces fail-fast validations on configurations on boot, constants.py manages immutable application metadata, and logging_config.py unifies system console formatting.
    app/models/: Houses persistent data structure relationships representing relational database tables.
    app/schemas/: Enforces strict payload validation rules for inputs (requests) and serialization rules for outputs (responses).
    app/services/: The domain layer. Holds isolated, state-free logic functions separating business rules from router inputs.
    app/workers/: Manages definitions for asynchronous background queues and parallel task worker cycles.


3. Local Installation & Setup

Prerequisites


    Python 3.12+
    pip (Python package installer)


Step 1: Environment Initialization

Clone the repository, enter the backend directory, and copy the default environment configurations file:

cd backend
cp .env.example .env

(Optionally modify .env to match your local host networking setup.)

Step 2: Establish Python Virtual Environment

Initialize and activate a virtual environment to isolate dependencies:

python -m venv .venv
source .venv/bin/activate # On Windows: .venv\Scripts\activate

Step 3: Install Package Requirements

pip install --upgrade pip
pip install -r requirements.txt

Step 4: Run the Application Server

Run the local Uvicorn ASGI server with dynamic hot-reloading active:

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

4. Local Deployment with Docker

To compile and execute the system in a standardized container environment mimicking cloud deployments:

Build and Launch Container Services

docker-compose up --build -d

The local codebase maps to /app/app within the container context, enabling immediate host updates to trigger uvicorn’s reload detector inside the container automatically.

View Runtime Console Logs

docker logs -f flowpilot-backend

Terminate Container Services

docker-compose down

5. API Observability & Verification

Once your service has launched locally or within container spaces, you can verify operational status using these target paths:

Interactive OpenAPI Documentation (Swagger UI)


    URL: http://localhost:8000/docs
    Provides structured test tools to execute manual endpoint request validation directly within your web browser.


Baseline Health Diagnostic Route


    Endpoint: GET /api/v1/health
    Raw Output Schema:


{
    "status": "healthy",
    "service": "FlowPilot AI",
    "version": "1.0.0",
    "environment": "development",
    "timestamp": "2024-03-24T12:00:00.000000"
}

6. Project Roadmap & Upcoming Milestones


    Module 1B (Database Foundation): Connect PostgreSQL database container networks, establish SQLAlchemy 2.0 transaction contexts, and set up database migrations via Alembic.
    Module 1C (Authentication): Design robust User data structures, parse password hashing logic with passlib (bcrypt), and implement secure JWT credentials handlers.
    Sprint 2 (Work Item Management): Implement smart file ingestion, status queues, and background pipeline processing configurations.




All files requested for Sprint 1: Module 1A – Backend Foundation are now completely generated. Below are the operational execution guidelines, complete folder tree verification, and a manual checklist to verify your implementation.



Part 5: Operational & Verification Artifacts

Complete Folder Tree

backend/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── health.py
│   │       └── router.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── constants.py
│   │   └── logging_config.py
│   ├── models/
│   │   └── __init__.py
│   ├── schemas/
│   │   └── __init__.py
│   ├── services/
│   │   └── __init__.py
│   ├── utils/
│   │   └── __init__.py
│   └── workers/
│       └── __init__.py
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── README.md
└── requirements.txt

Execution Commands

Local Host Commands

# 1. Enter source path
cd backend

# 2. Duplicate environment profile
cp .env.example .env

# 3. Create virtual runtime and activate
python -m venv .venv
source .venv/bin/activate # On Windows: .venv\Scripts\activate

# 4. Install requirements
pip install -r requirements.txt

# 5. Boot ASGI development server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

Docker Commands

# 1. Build and run containers in background mode
docker-compose up --build -d

# 2. Monitor execution log outputs
docker logs -f flowpilot-backend

# 3. Shut down and clean container configurations
docker-compose down


## Folder Responsibilities

| Folder | Responsibility |
|----------|----------------|
| api | API endpoints and routing |
| core | Configuration, constants, logging |
| models | Database models |
| schemas | Request/Response validation |
| services | Business logic |
| workers | Background processing |
| utils | Shared helper functions |