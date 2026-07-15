import logging

logger = logging.getLogger(__name__)

class IntentService:

    INTENT_ALIASES = {
        "resume": [
            "resume",
            "cv",
            "curriculum vitae",
            "candidate",
            "profile",
            "skills",
            "experience",
            "education",
            "employment",
            "email",
            "phone",
            "contact",
        ],
        "upsc": [
            "upsc",
            "civil service",
            "ias",
            "prelims",
            "mains",
            "gs",
            "optional",
            "syllabus",
        ],
        "invoice": [
            "invoice",
            "bill",
            "payment",
            "gst",
            "tax",
            "amount",
            "total",
        ],
        "contract": [
            "contract",
            "agreement",
            "clause",
            "legal",
            "party",
            "obligation",
            "term",
        ],
    }

    def detect_intent(
        self,
        query: str,
    ) -> str:

        # ← Replace your old logic from here

        query_lower = query.lower()

        best_intent = "unknown"
        best_score = 0

        for intent, keywords in self.INTENT_ALIASES.items():

            score = sum(
                1
                for keyword in keywords
                if keyword in query_lower
            )

            if score > best_score:
                best_score = score
                best_intent = intent

        logger.info(
            "Intent detected: %s (score=%d)",
            best_intent,
            best_score,
        )

        return best_intent
    
intent_service = IntentService()