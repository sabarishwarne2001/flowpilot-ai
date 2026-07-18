"""
Golden retrieval evaluation dataset.

These queries are used to measure retrieval quality and detect regressions
after retrieval changes.
"""

from __future__ import annotations

from app.services.retrieval_evaluator import RetrievalEvaluationCase

# ============================================================================
# Phase 1
# Basic Retrieval
# ============================================================================
# ============================================================================
# Phase 1
# Basic Retrieval
# ============================================================================
# ============================================================================
# Phase 1
# Basic Retrieval
# ============================================================================
BASIC_RETRIEVAL_CASES: list[RetrievalEvaluationCase] = [
    RetrievalEvaluationCase(
        query="Summarize my resume",
        expected_documents=[
            "Sabarish's Resume.pdf",
        ],
        expected_chunks=[],
        intent="basic_retrieval",
        category="resume",
        difficulty="easy",
        must_retrieve=[
            "Sabarish's Resume.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Sabarish's Resume.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    RetrievalEvaluationCase(
        query="employee handbook",
        expected_documents=[
            "Employee_Handbook_2026.pdf",
        ],
        expected_chunks=[],
        intent="exact_filename_search",
        category="hr",
        difficulty="easy",
        must_retrieve=[
            "Employee_Handbook_2026.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Employee_Handbook_2026.pdf",
        },
        minimum_confidence=0.90,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    RetrievalEvaluationCase(
        query="leave policy",
        expected_documents=[
            "Company_Leave_Policy_v4.pdf",
        ],
        expected_chunks=[],
        intent="topic_search",
        category="policy",
        difficulty="medium",
        must_retrieve=[
            "Company_Leave_Policy_v4.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Company_Leave_Policy_v4.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    RetrievalEvaluationCase(
        query="How many vacation days do employees receive?",
        expected_documents=[
            "Company_Leave_Policy_v4.pdf",
        ],
        expected_chunks=[],
        intent="natural_language_question",
        category="policy",
        difficulty="medium",
        must_retrieve=[
            "Company_Leave_Policy_v4.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Company_Leave_Policy_v4.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.60,
        minimum_mrr=1.0,
    ),
    RetrievalEvaluationCase(
        query="vacation",
        expected_documents=[
            "Company_Leave_Policy_v4.pdf",
        ],
        expected_chunks=[],
        intent="keyword_search",
        category="policy",
        difficulty="easy",
        must_retrieve=[
            "Company_Leave_Policy_v4.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Company_Leave_Policy_v4.pdf",
        },
        minimum_confidence=0.65,
        minimum_recall=1.0,
        minimum_precision=0.50,
        minimum_mrr=1.0,
    ),
]



# ============================================================================
# Phase 2
# Multi-document Retrieval
# ============================================================================
MULTI_DOCUMENT_CASES: list[
    RetrievalEvaluationCase
] = [

    #
    # Resume + Cover Letter
    #
    RetrievalEvaluationCase(

        query="Summarize my professional profile and experience.",

        expected_documents=[
            "Sabarish's Resume.pdf",
            "Cover_Letter.pdf",
        ],

        expected_chunks=[],

        intent="multi_document_summary",

        category="career",

        difficulty="medium",

        must_retrieve=[
            "Sabarish's Resume.pdf",
            "Cover_Letter.pdf",
        ],

        must_not_retrieve=[],

        expected_metadata={
            "original_filename": [
                "Sabarish's Resume.pdf",
                "Cover_Letter.pdf",
            ]
        },

        minimum_confidence=0.75,

        minimum_recall=1.0,

        minimum_precision=0.70,

        minimum_mrr=0.90,

    ),

    #
    # Employee Policies
    #
    RetrievalEvaluationCase(

        query="Summarize employee leave policies and workplace rules.",

        expected_documents=[
            "Company_Leave_Policy_v4.pdf",
            "Employee_Handbook_2026.pdf",
        ],

        expected_chunks=[],

        intent="policy_summary",

        category="policy",

        difficulty="medium",

        must_retrieve=[
            "Company_Leave_Policy_v4.pdf",
            "Employee_Handbook_2026.pdf",
        ],

        must_not_retrieve=[],

        expected_metadata={
            "original_filename": [
                "Company_Leave_Policy_v4.pdf",
                "Employee_Handbook_2026.pdf",
            ]
        },

        minimum_confidence=0.75,

        minimum_recall=1.0,

        minimum_precision=0.70,

        minimum_mrr=0.90,

    ),

    #
    # Benefits + Leave
    #
    RetrievalEvaluationCase(

        query="Explain employee benefits and vacation policies.",

        expected_documents=[
            "Employee_Benefits.pdf",
            "Company_Leave_Policy_v4.pdf",
        ],

        expected_chunks=[],

        intent="cross_document_question",

        category="benefits",

        difficulty="hard",

        must_retrieve=[
            "Employee_Benefits.pdf",
            "Company_Leave_Policy_v4.pdf",
        ],

        must_not_retrieve=[],

        expected_metadata={
            "original_filename": [
                "Employee_Benefits.pdf",
                "Company_Leave_Policy_v4.pdf",
            ]
        },

        minimum_confidence=0.70,

        minimum_recall=1.0,

        minimum_precision=0.65,

        minimum_mrr=0.85,

    ),

]

# ============================================================================
# Phase 3
# Query Expansion
# ============================================================================
QUERY_EXPANSION_CASES: list[RetrievalEvaluationCase] = [

    #
    # Overview -> Summarize
    #
    RetrievalEvaluationCase(
        query="overview my resume",
        expected_documents=[
            "Sabarish's Resume.pdf",
        ],
        expected_chunks=[],
        intent="linguistic_query_expansion",
        category="resume",
        difficulty="medium",
        must_retrieve=[
            "Sabarish's Resume.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Sabarish's Resume.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Locate -> Find
    #
    RetrievalEvaluationCase(
        query="locate employee handbook",
        expected_documents=[
            "Employee_Handbook_2026.pdf",
        ],
        expected_chunks=[],
        intent="linguistic_query_expansion",
        category="hr",
        difficulty="easy",
        must_retrieve=[
            "Employee_Handbook_2026.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Employee_Handbook_2026.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Search -> Find
    #
    RetrievalEvaluationCase(
        query="search leave policy",
        expected_documents=[
            "Company_Leave_Policy_v4.pdf",
        ],
        expected_chunks=[],
        intent="linguistic_query_expansion",
        category="policy",
        difficulty="medium",
        must_retrieve=[
            "Company_Leave_Policy_v4.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Company_Leave_Policy_v4.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Display -> List
    #
    RetrievalEvaluationCase(
        query="display employee benefits",
        expected_documents=[
            "Employee_Benefits.pdf",
        ],
        expected_chunks=[],
        intent="linguistic_query_expansion",
        category="benefits",
        difficulty="medium",
        must_retrieve=[
            "Employee_Benefits.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Employee_Benefits.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Describe -> Explain
    #
    RetrievalEvaluationCase(
        query="describe privacy policy",
        expected_documents=[
            "Privacy_Policy.pdf",
        ],
        expected_chunks=[],
        intent="linguistic_query_expansion",
        category="policy",
        difficulty="medium",
        must_retrieve=[
            "Privacy_Policy.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Privacy_Policy.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

]

# ============================================================================
# Phase 4
# Metadata Retrieval
# ============================================================================
METADATA_CASES: list[RetrievalEvaluationCase] = [
    RetrievalEvaluationCase(
        query="summarize my resume",
        expected_documents=[
            "Sabarish's Resume.pdf",
        ],
        expected_chunks=[],
        intent="metadata_validation",
        category="resume",
        difficulty="easy",
        must_retrieve=[
            "Sabarish's Resume.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Sabarish's Resume.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    RetrievalEvaluationCase(
        query="privacy policy",
        expected_documents=[
            "Privacy_Policy.pdf",
        ],
        expected_chunks=[],
        intent="metadata_validation",
        category="policy",
        difficulty="easy",
        must_retrieve=[
            "Privacy_Policy.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Privacy_Policy.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    RetrievalEvaluationCase(
        query="travel reimbursement",
        expected_documents=[
            "Travel_Reimbursement_Policy.pdf",
        ],
        expected_chunks=[],
        intent="metadata_validation",
        category="travel",
        difficulty="easy",
        must_retrieve=[
            "Travel_Reimbursement_Policy.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "Travel_Reimbursement_Policy.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    RetrievalEvaluationCase(
        query="IT security policy",
        expected_documents=[
            "IT_Security_Policy.pdf",
        ],
        expected_chunks=[],
        intent="metadata_validation",
        category="security",
        difficulty="easy",
        must_retrieve=[
            "IT_Security_Policy.pdf",
        ],
        must_not_retrieve=[],
        expected_metadata={
            "original_filename": "IT_Security_Policy.pdf",
        },
        minimum_confidence=0.75,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
]

# ============================================================================
# Phase 5
# Confidence Evaluation
# ============================================================================
CONFIDENCE_CASES: list[RetrievalEvaluationCase] = [
    RetrievalEvaluationCase(
        query="employee handbook",
        expected_documents=[
            "Employee_Handbook_2026.pdf",
        ],
        expected_chunks=[],
        intent="confidence_validation",
        category="handbook",
        difficulty="easy",
        must_retrieve=[
            "Employee_Handbook_2026.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    #
    # Resume Confidence
    #
    RetrievalEvaluationCase(
        query="summarize my resume",
        expected_documents=[
            "Sabarish's Resume.pdf",
        ],
        expected_chunks=[],
        intent="confidence_validation",
        category="resume",
        difficulty="easy",
        must_retrieve=[
            "Sabarish's Resume.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Privacy Policy Confidence
    #
    RetrievalEvaluationCase(
        query="privacy policy",
        expected_documents=[
            "Privacy_Policy.pdf",
        ],
        expected_chunks=[],
        intent="confidence_validation",
        category="policy",
        difficulty="easy",
        must_retrieve=[
            "Privacy_Policy.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Travel Reimbursement Confidence
    #
    RetrievalEvaluationCase(
        query="travel reimbursement",
        expected_documents=[
            "Travel_Reimbursement_Policy.pdf",
        ],
        expected_chunks=[],
        intent="confidence_validation",
        category="travel",
        difficulty="easy",
        must_retrieve=[
            "Travel_Reimbursement_Policy.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

]

# ============================================================================
# Phase 6
# Negative Retrieval
# ============================================================================
NEGATIVE_CASES: list[RetrievalEvaluationCase] = []

# ============================================================================
# Phase 7
# Ranking
# ============================================================================
RANKING_CASES: list[RetrievalEvaluationCase] = [
    RetrievalEvaluationCase(
        query="employee handbook",
        expected_documents=[
            "Employee_Handbook_2026.pdf",
        ],
        expected_chunks=[],
        intent="ranking_validation",
        category="ranking",
        difficulty="easy",
        must_retrieve=[
            "Employee_Handbook_2026.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
    #
    # Resume Ranking
    #
    RetrievalEvaluationCase(
        query="summarize my resume",
        expected_documents=[
            "Sabarish's Resume.pdf",
        ],
        expected_chunks=[],
        intent="ranking_validation",
        category="ranking",
        difficulty="easy",
        must_retrieve=[
            "Sabarish's Resume.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Privacy Policy Ranking
    #
    RetrievalEvaluationCase(
        query="privacy policy",
        expected_documents=[
            "Privacy_Policy.pdf",
        ],
        expected_chunks=[],
        intent="ranking_validation",
        category="ranking",
        difficulty="easy",
        must_retrieve=[
            "Privacy_Policy.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),

    #
    # Travel Reimbursement Ranking
    #
    RetrievalEvaluationCase(
        query="travel reimbursement",
        expected_documents=[
            "Travel_Reimbursement_Policy.pdf",
        ],
        expected_chunks=[],
        intent="ranking_validation",
        category="ranking",
        difficulty="easy",
        must_retrieve=[
            "Travel_Reimbursement_Policy.pdf",
        ],
        must_not_retrieve=[],
        minimum_confidence=0.70,
        minimum_recall=1.0,
        minimum_precision=0.70,
        minimum_mrr=1.0,
    ),
]

# ============================================================================
# Combined Production Evaluation Dataset Matrix
# ============================================================================
# EVALUATION_DATASET = (
#     BASIC_RETRIEVAL_CASES
#     + MULTI_DOCUMENT_CASES
#     + QUERY_EXPANSION_CASES
#     + METADATA_CASES
#     + CONFIDENCE_CASES
#     + NEGATIVE_CASES
#     + RANKING_CASES
# )
EVALUATION_DATASET = [
    BASIC_RETRIEVAL_CASES[0],
]