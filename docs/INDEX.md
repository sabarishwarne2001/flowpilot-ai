# INDEX.md

# FlowPilot AI Documentation Index

## Purpose

This directory contains the complete engineering documentation for FlowPilot AI.

Before modifying the project, developers and AI assistants should understand the documentation structure and review the appropriate documents.

The documents below are listed in the recommended reading order.

---

# 1. PRD.md

Purpose

Defines the business vision of FlowPilot AI.

Contains:

- Product vision
- Target users
- Functional requirements
- Non-functional requirements
- MVP scope
- Future roadmap

Read this document first to understand what the product is intended to become.

---

# 2. SYSTEM_ARCHITECTURE.md

Purpose

Describes the overall software architecture.

Contains:

- High-level architecture
- Component interactions
- AI processing pipeline
- Database architecture
- Service responsibilities
- Infrastructure overview

Read this before implementing architectural changes.

---

# 3. MODULE_SPECIFICATION.md

Purpose

Defines each major module of the system.

Contains:

- Module responsibilities
- Inputs
- Outputs
- Dependencies
- Internal boundaries

Read before modifying an existing module.

---

# 4. API_SPECIFICATION.md

Purpose

Defines the public REST API.

Contains:

- Endpoints
- Request models
- Response models
- Authentication
- Status codes

Read before changing API behavior.

---

# 5. DEVELOPMENT_PLAN.md

Purpose

Describes the project roadmap.

Contains:

- Sprint planning
- Development phases
- Milestones
- Implementation sequence

Read before starting a new sprint.

---

# 6. AI_ENGINEERING_GUIDE.md

Purpose

Provides implementation standards for AI-assisted development.

Contains:

- Coding standards
- Naming conventions
- Project structure
- Engineering principles

All generated code should follow these standards.

---

# 7. PROJECT_CHANGELOG_FOR_GEMINI.md

Purpose

Documents architectural and implementation changes made after the original design.

Contains:

- Sprint-by-sprint changes
- Bug fixes
- Refactoring history
- Important implementation decisions

Read this before modifying existing code.

---

# 8. CURRENT_PROJECT_STATE.md

Purpose

Represents the current implementation of FlowPilot AI.

Contains:

- Current architecture
- Current technology stack
- Current pipeline
- Current services
- Verification status

This is the authoritative description of the current system.

---

# 9. AI_DEVELOPMENT_RULES.md

Purpose

Defines mandatory engineering rules that every AI assistant must follow.

Contains:

- Architecture rules
- Layering rules
- Service boundaries
- Database rules
- Logging standards
- Configuration standards
- Security rules

If generated code conflicts with these rules, the rules take precedence unless the project owner explicitly requests an architectural change.

---

# Recommended Reading Order

For a new developer:

1. PRD.md
2. SYSTEM_ARCHITECTURE.md
3. MODULE_SPECIFICATION.md
4. API_SPECIFICATION.md
5. DEVELOPMENT_PLAN.md

---

For an AI assistant modifying the codebase:

1. CURRENT_PROJECT_STATE.md
2. PROJECT_CHANGELOG_FOR_GEMINI.md
3. AI_DEVELOPMENT_RULES.md
4. AI_ENGINEERING_GUIDE.md

---

# Documentation Maintenance

Whenever the project evolves:

- Update CURRENT_PROJECT_STATE.md to reflect the latest architecture.
- Record significant implementation changes in PROJECT_CHANGELOG_FOR_GEMINI.md.
- Update AI_DEVELOPMENT_RULES.md if engineering standards change.
- Update DEVELOPMENT_PLAN.md when sprint planning changes.
- Update API_SPECIFICATION.md whenever public APIs change.
- Update SYSTEM_ARCHITECTURE.md only when the high-level architecture changes.

These documents should remain synchronized with the source code throughout the lifetime of the project.