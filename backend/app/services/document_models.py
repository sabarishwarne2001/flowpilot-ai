"""
Document domain models for FlowPilot AI.

These models represent the canonical data structures flowing through
the document processing pipeline.

Pipeline:

PDF/Image
    ↓
DocumentPage
    ↓
DocumentChunk
    ↓
Embedding
    ↓
Vector Database
    ↓
Assistant
"""

from __future__ import annotations

from dataclasses import dataclass


# ============================================================================
# Page
# ============================================================================


@dataclass(slots=True)
class DocumentPage:
    """
    Represents one logical page extracted from a document.

    Page numbers are always 1-based.
    """

    page_number: int

    text: str


# ============================================================================
# Chunk
# ============================================================================


@dataclass(slots=True)
class DocumentChunk:
    """
    Represents one semantic chunk generated from a page.

    Chunk indices are assigned after chunking and are globally
    unique within a document.
    """

    text: str

    page_number: int

    chunk_index: int