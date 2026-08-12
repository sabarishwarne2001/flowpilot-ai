"""
ARCH-06 Step 1c regression suite — A.2.8 / exit criterion E14.

    "`import app.services.user_service` does not import `sentence_transformers`."

WHY THIS IS A TEST AND NOT A ONE-OFF SHELL COMMAND
---------------------------------------------------
The verification gate for Step 1c is a `python -c` invocation, which checks the
property once, on the day of the fix, in one shell. The property is a
regression risk forever: `app/services/__init__.py` is on the import path of
the entire application, so any future editor adding a convenience import to it
re-charges the whole ML stack to every module that touches any service. The
failure is silent — nothing breaks, everything just gets slower, until a
network-restricted environment turns it into a hard failure at collection
time, which is exactly how ARCH-05 verification lost a day.

WHY IT RUNS IN A SUBPROCESS
----------------------------
`sys.modules` is process-global and pytest has already imported large parts of
the application by the time any test body runs — `tests/conftest.py` imports
`app.main`, which pulls in every router and therefore the whole service layer,
ML stack included. Asserting `"chromadb" not in sys.modules` inside the test
process would fail against correct code, for reasons that have nothing to do
with the property under test.

A clean interpreter is the only honest measurement. The subprocess also means
these tests report the true cost: if the heavy import returns, the subprocess
takes seconds rather than milliseconds and the assertion names which chain
brought it back.

WHY BOTH chromadb AND sentence_transformers
--------------------------------------------
A.2.8 quotes the `sentence_transformers` import, but `embedding_service` also
imports `chromadb` at module level, and `chromadb` is the heavier of the two
to reach in a sandbox. Removing only the line the finding quoted leaves the
chain intact through `chromadb`, which is precisely the trap Step 1c fell into
the first time — the plan's stated one-line fix does not close it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest


#: Modules that must not be reachable from the tenancy and identity services.
#: Both are pulled in by app/services/embedding_service.py at module level.
_HEAVY = ("sentence_transformers", "chromadb")


#: Every module a router or service imports for tenancy, identity, invitation,
#: ownership, or notification work. None of them has anything to do with
#: embeddings, and each one was paying for the ML stack before Step 1c.
_MUST_STAY_CLEAN = [
    "app.services",
    "app.services.user_service",
    "app.services.organization_service",
    "app.services.organization_member_service",
    "app.services.organization_invitation_service",
    "app.services.ownership_mail",
    "app.services.workspace_service",
    "app.services.auth_service",
    "app.services.notification.dispatcher",
]


def _heavy_modules_after_importing(target: str) -> list[str]:
    """
    Imports `target` in a fresh interpreter and reports which heavy modules
    landed in sys.modules.

    Returns the list rather than a boolean so a failure message can name what
    actually got pulled in instead of only asserting that something did.
    """
    program = textwrap.dedent(
        f"""
        import json, sys
        import {target}  # noqa: F401
        found = [m for m in {_HEAVY!r} if m in sys.modules]
        print(json.dumps(found))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if completed.returncode != 0:
        pytest.fail(
            f"Importing {target} in a clean interpreter failed:\n"
            f"{completed.stderr}"
        )

    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("module", _MUST_STAY_CLEAN)
def test_service_import_does_not_load_ml_stack(module: str) -> None:
    """
    E14, generalized past the single module the finding named.

    If this fails, the fix is NOT to add the module to an exclusion list. It is
    to find the eager import in `app/services/__init__.py` — or in whichever
    module in the chain acquired one — and move it to the call site. Trace it
    with:

        python -X importtime -c "import app.services.user_service" 2>&1 \\
            | sort -k2 -n -r | head -30
    """
    loaded = _heavy_modules_after_importing(module)

    assert loaded == [], (
        f"Importing {module} pulled in {', '.join(loaded)}. Something in "
        f"app/services/__init__.py — or in a module it imports — is eagerly "
        f"importing the embedding stack again. Removing only the "
        f"`embedding_service` line is not sufficient: assistant_service and "
        f"document_processor each reach it independently, via "
        f"retrieval_service and bm25_service respectively."
    )


def test_embedding_service_is_still_importable_directly() -> None:
    """
    The counterpart assertion, and the reason this file is not just a ban list.

    Step 1c decouples the package from the ML stack; it does not remove the ML
    stack. `app.services.embedding_service` must still import cleanly for
    document processing, retrieval, and the evaluation scripts. A "fix" that
    achieved E14 by breaking the embedding import would pass every test above
    and ship a broken product.

    Skipped rather than failed when the dependency is absent, so this suite
    stays runnable in a network-restricted sandbox — which is the environment
    A.2.8 exists because of.
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import chromadb, sentence_transformers"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if probe.returncode != 0:
        pytest.skip(
            "Embedding dependencies are not installed in this environment."
        )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.services.embedding_service import embedding_service; "
            "print('ok')",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert completed.returncode == 0, (
        "app.services.embedding_service no longer imports:\n"
        f"{completed.stderr}"
    )


def test_removed_names_are_absent_from_the_package_namespace() -> None:
    """
    Pins the three removals against a well-meaning re-export.

    A lazy `__getattr__` on the package would restore `from app.services import
    assistant_service` while keeping the import graph clean, and would look
    like an improvement. It is not one for this codebase: it hides the cost at
    the call site, and the two call sites that needed updating
    (`app/api/v1/assistant.py`, `app/api/v1/work_items.py`) are updated. One
    import style, visible in the source.
    """
    program = textwrap.dedent(
        """
        import json
        import app.services as s
        present = [
            n for n in ("embedding_service", "assistant_service",
                        "process_document_pipeline")
            if hasattr(s, n)
        ]
        print(json.dumps(present))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr

    present = json.loads(completed.stdout.strip().splitlines()[-1])

    assert present == [], (
        f"{', '.join(present)} is reachable from app.services again. Import "
        f"the submodule directly at the call site instead."
    )