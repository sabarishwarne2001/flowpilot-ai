#!/usr/bin/env python
"""ARCH-0V — idempotent anchored edits to files too large to rewrite safely.

WHY A PATCH SCRIPT AND NOT FULL REWRITES

`assistant_stream.py` is 660 lines of streaming state machine and
`executor.py` is a DAG walker. Retyping either to change nine lines is how a
transcription error enters a system that has no test for the path being
changed. Established precedent: ARCH-19 shipped 46 surgical changes this way.

CONTRACT (all three properties are asserted, not assumed)

  1. IDEMPOTENT — running twice changes nothing the second time. Every edit
     checks for its own post-state first and reports SKIP.
  2. LOUD ON ANCHOR MISS — an anchor that does not appear exactly once is a
     hard failure naming the file and the anchor. It never guesses, never
     falls back to a fuzzy match, and never partially applies a file.
  3. ATOMIC PER FILE — edits are staged in memory and written once. A file
     with any failing anchor is left untouched on disk.

USAGE

    python scripts/patch_arch0v.py --check    # report only, exit 1 if pending
    python scripts/patch_arch0v.py --apply
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


class AnchorMiss(Exception):
    """An anchor did not appear exactly once."""


@dataclass
class Edit:
    """One anchored replacement."""

    edit_id: str
    anchor: str
    replacement: str
    rationale: str
    #: Text whose presence means this edit is already applied.
    applied_marker: str

    def apply(self, source: str, *, path: str) -> tuple[str, str]:
        if self.applied_marker in source:
            return source, "SKIP"

        occurrences = source.count(self.anchor)
        if occurrences != 1:
            raise AnchorMiss(
                f"{path}: edit {self.edit_id} expected its anchor exactly once, "
                f"found {occurrences}.\n"
                f"        anchor: {self.anchor.strip().splitlines()[0][:88]!r}\n"
                f"        The file has drifted from the ARCH-0V baseline "
                f"(commit 1b04068). Re-read it before forcing this through."
            )
        return source.replace(self.anchor, self.replacement), "APPLIED"


# =====================================================================
# Tranche 5 — the timing floor (D09)
# =====================================================================

CONFIG_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-5.1",
        applied_marker="AUTH_LOGIN_MIN_DURATION_MS: int = 250",
        anchor=(
            "    #: Optional floor on how long a login attempt takes, in milliseconds.\n"
            "    AUTH_LOGIN_MIN_DURATION_MS: int = 0\n"
        ),
        replacement=(
            "    #: Floor on how long a login attempt takes, in milliseconds.\n"
            "    #:\n"
            "    #: ARCH-0V Tranche 5. This was 0 through ARCH-22, which meant the\n"
            "    #: floor existed in code and did nothing. The population is mixed:\n"
            "    #: SEC-1 introduced Argon2id but `pwd_context` still verifies bcrypt\n"
            "    #: for dormant accounts, and the two families have very different\n"
            "    #: verification costs. A nonexistent user takes the `_DUMMY_HASH`\n"
            "    #: path (Argon2id); an existing bcrypt user takes a measurably\n"
            "    #: different one. That difference is a user-enumeration oracle.\n"
            "    #:\n"
            "    #: 250 ms is set from measurement, not from a guess — run\n"
            "    #: `scripts/calibrate_auth_timing.py` on target hardware and raise\n"
            "    #: this above the reported p99 if it comes back higher. A floor\n"
            "    #: below the real p99 leaks exactly the distinction it exists to\n"
            "    #: hide, which is worse than no floor because it looks handled.\n"
            "    #:\n"
            "    #: Gate 0V-G8 refuses a value below AUTH_LOGIN_MIN_DURATION_FLOOR_MS.\n"
            "    AUTH_LOGIN_MIN_DURATION_MS: int = 250\n"
            "\n"
            "    #: The lowest value 0V-G8 will accept. Not independently tunable:\n"
            "    #: lowering this is how the floor quietly becomes decorative again.\n"
            "    AUTH_LOGIN_MIN_DURATION_FLOOR_MS: int = 200\n"
        ),
        rationale="Timing-attack floor set from measurement; guarded by 0V-G8.",
    ),
]


# =====================================================================
# Tranche 6 — A13 resume: never claim currency you cannot prove (D10/D11)
# =====================================================================

STREAM_SERVICE_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-6.1",
        applied_marker="_LASTSEQ_KEY",
        anchor=(
            '_FRAMES_KEY = "stream:frames:{message_id}"\n'
            '_STATE_KEY = "stream:state:{message_id}"\n'
        ),
        replacement=(
            '_FRAMES_KEY = "stream:frames:{message_id}"\n'
            '_STATE_KEY = "stream:state:{message_id}"\n'
            '\n'
            '#: ARCH-0V Tranche 6. The last sequence number this turn emitted,\n'
            '#: written at close. Without it, `replay` cannot distinguish "you are\n'
            '#: current" from "I lost the frames you are missing" — both present as\n'
            '#: an empty read against a CLOSED state, and the second one was being\n'
            '#: reported to the client as `already_current`.\n'
            '#:\n'
            '#: A value of _LASTSEQ_UNKNOWN means the buffer was disabled mid-turn\n'
            '#: (overflow, or a Redis write failure) and no completeness claim can\n'
            '#: be made about it.\n'
            '_LASTSEQ_KEY = "stream:lastseq:{message_id}"\n'
            '_LASTSEQ_UNKNOWN = -1\n'
        ),
        rationale="Records the turn's final seq so completeness is provable.",
    ),
    Edit(
        edit_id="0V-6.2",
        applied_marker="class ReplayIncompleteError",
        anchor=(
            'class ReplayUnavailableError(RuntimeError):\n'
            '    """No buffered frames exist for this message."""\n'
        ),
        replacement=(
            'class ReplayUnavailableError(RuntimeError):\n'
            '    """No buffered frames exist for this message."""\n'
            '\n'
            '\n'
            'class ReplayIncompleteError(ReplayUnavailableError):\n'
            '    """Frames exist, but not the ones this client is missing.\n'
            '\n'
            '    ARCH-0V Tranche 6. Distinct from its parent because the caller\n'
            '    must not treat it as "nothing to send". Deriving from\n'
            '    ReplayUnavailableError keeps every existing `except` clause\n'
            '    correct; the API layer catches this one first to return a\n'
            '    different status and an explicit `resume_unavailable` code.\n'
            '\n'
            '    Raised when the buffer is CLOSED and either the final sequence\n'
            '    number is unknown, or fewer frames survive than the client is\n'
            '    missing. Both happen in production: the frames list has its TTL\n'
            '    refreshed on every append while the state key does not, Redis\n'
            '    evicts the large list before the small string under memory\n'
            '    pressure, and ARCH-19 Sentinel failover replicates the two keys\n'
            '    asynchronously.\n'
            '    """\n'
        ),
        rationale="A distinguishable failure for 'cannot prove completeness'.",
    ),
    Edit(
        edit_id="0V-6.3",
        applied_marker="def close(self, *, last_seq: int) -> None:",
        anchor=(
            "    def close(self) -> None:\n"
            "        if not self.enabled:\n"
            "            return\n"
            "        try:\n"
            "            self._client.set(self._state_key, _STATE_CLOSED, "
            "ex=REPLAY_TTL_SECONDS)\n"
            "        except Exception:  # noqa: BLE001\n"
            "            self._disable(\"close\")\n"
        ),
        replacement=(
            "    def close(self, *, last_seq: int) -> None:\n"
            "        \"\"\"Seal the buffer, recording how many frames the turn emitted.\n"
            "\n"
            "        `last_seq` is what makes a later resume provable. It is written\n"
            "        in the same pipeline as the state flip so a reader never sees\n"
            "        CLOSED without a companion sequence number.\n"
            "        \"\"\"\n"
            "        if not self.enabled:\n"
            "            return\n"
            "        try:\n"
            "            pipe = self._client.pipeline()\n"
            "            pipe.set(self._lastseq_key, int(last_seq), "
            "ex=REPLAY_TTL_SECONDS)\n"
            "            pipe.set(self._state_key, _STATE_CLOSED, "
            "ex=REPLAY_TTL_SECONDS)\n"
            "            pipe.execute()\n"
            "        except Exception:  # noqa: BLE001\n"
            "            self._disable(\"close\")\n"
        ),
        rationale="close() now records the final sequence number.",
    ),
    Edit(
        edit_id="0V-6.4",
        applied_marker="def _lastseq_key(self) -> str:",
        anchor=(
            "    @property\n"
            "    def _state_key(self) -> str:\n"
            "        return _STATE_KEY.format(message_id=self.message_id)\n"
        ),
        replacement=(
            "    @property\n"
            "    def _state_key(self) -> str:\n"
            "        return _STATE_KEY.format(message_id=self.message_id)\n"
            "\n"
            "    @property\n"
            "    def _lastseq_key(self) -> str:\n"
            "        return _LASTSEQ_KEY.format(message_id=self.message_id)\n"
        ),
        rationale="Key accessor for the sequence marker.",
    ),
    Edit(
        edit_id="0V-6.5",
        # Must match text that actually appears in `replacement`. An
        # earlier draft used "pipe.set(self._lastseq_key, ..." which the
        # replacement writes across three lines, so the marker never
        # matched its own output: run 1 applied, run 2 could not find the
        # marker, fell through to the consumed anchor, and failed loudly.
        # That is the contract working, but the marker was still wrong.
        applied_marker="# UNKNOWN until close().",
        anchor=(
            "    def open(self) -> None:\n"
            "        if not self.enabled:\n"
            "            return\n"
            "        try:\n"
            "            pipe = self._client.pipeline()\n"
            "            pipe.delete(self._frames_key)\n"
            "            pipe.set(self._state_key, _STATE_OPEN, ex=REPLAY_TTL_SECONDS)\n"
            "            pipe.execute()\n"
        ),
        replacement=(
            "    def open(self) -> None:\n"
            "        if not self.enabled:\n"
            "            return\n"
            "        try:\n"
            "            pipe = self._client.pipeline()\n"
            "            pipe.delete(self._frames_key)\n"
            "            # UNKNOWN until close(). A turn that dies mid-flight leaves\n"
            "            # this sentinel behind, and a resume against it correctly\n"
            "            # refuses rather than claiming the client is current.\n"
            "            pipe.set(\n"
            "                self._lastseq_key, _LASTSEQ_UNKNOWN, ex=REPLAY_TTL_SECONDS\n"
            "            )\n"
            "            pipe.set(self._state_key, _STATE_OPEN, ex=REPLAY_TTL_SECONDS)\n"
            "            pipe.execute()\n"
        ),
        rationale="Seed the sequence marker as UNKNOWN at open.",
    ),
    Edit(
        edit_id="0V-6.6",
        applied_marker="# ARCH-0V Tranche 6 — a disabled buffer can prove nothing",
        anchor=(
            "        self.enabled = False\n"
            "        logger.warning(\n"
            "            \"stream.replay_buffer_disabled\",\n"
            "            extra={\"message_id\": str(self.message_id), \"phase\": phase},\n"
            "        )\n"
            "        try:\n"
            "            self._client.set(self._state_key, _STATE_CLOSED, "
            "ex=REPLAY_TTL_SECONDS)\n"
        ),
        replacement=(
            "        self.enabled = False\n"
            "        logger.warning(\n"
            "            \"stream.replay_buffer_disabled\",\n"
            "            extra={\"message_id\": str(self.message_id), \"phase\": phase},\n"
            "        )\n"
            "        try:\n"
            "            # ARCH-0V Tranche 6 — a disabled buffer can prove nothing\n"
            "            # about completeness, so the sequence marker is forced back\n"
            "            # to UNKNOWN even if a close() had already written a real\n"
            "            # one. Refusing a resume is correct here; claiming currency\n"
            "            # would be a silent truncation in the client.\n"
            "            self._client.set(\n"
            "                self._lastseq_key, _LASTSEQ_UNKNOWN, ex=REPLAY_TTL_SECONDS\n"
            "            )\n"
            "            self._client.set(self._state_key, _STATE_CLOSED, "
            "ex=REPLAY_TTL_SECONDS)\n"
        ),
        rationale="A disabled buffer never claims completeness.",
    ),
    Edit(
        edit_id="0V-6.7",
        applied_marker="buffer.close(last_seq=seq_counter[\"value\"])",
        anchor="                buffer.close()\n",
        replacement="                buffer.close(last_seq=seq_counter[\"value\"])\n",
        rationale="Pass the emitted frame count through to the buffer seal.",
    ),
    Edit(
        edit_id="0V-6.8",
        applied_marker="ARCH-0V Tranche 6: prove completeness before replaying",
        anchor=(
            "        frames_key = _FRAMES_KEY.format(message_id=message_id)\n"
            "        state_key = _STATE_KEY.format(message_id=message_id)\n"
            "\n"
            "        try:\n"
            "            state = client.get(state_key)\n"
            "            buffered = client.llen(frames_key)\n"
            "        except Exception as exc:  # noqa: BLE001\n"
            "            raise ReplayUnavailableError(\n"
            "                \"Stream resumption is unavailable. Refetch the message "
            "instead.\"\n"
            "            ) from exc\n"
            "\n"
            "        if state is None and not buffered:\n"
            "            raise ReplayUnavailableError(\n"
            "                \"No buffered frames for this message. It may have "
            "expired.\"\n"
            "            )\n"
            "\n"
            "        if isinstance(state, bytes):\n"
            "            state = state.decode(\"utf-8\", \"replace\")\n"
        ),
        replacement=(
            "        frames_key = _FRAMES_KEY.format(message_id=message_id)\n"
            "        state_key = _STATE_KEY.format(message_id=message_id)\n"
            "        lastseq_key = _LASTSEQ_KEY.format(message_id=message_id)\n"
            "\n"
            "        try:\n"
            "            state = client.get(state_key)\n"
            "            buffered = client.llen(frames_key)\n"
            "            raw_last_seq = client.get(lastseq_key)\n"
            "        except Exception as exc:  # noqa: BLE001\n"
            "            raise ReplayUnavailableError(\n"
            "                \"Stream resumption is unavailable. Refetch the message "
            "instead.\"\n"
            "            ) from exc\n"
            "\n"
            "        if state is None and not buffered:\n"
            "            raise ReplayUnavailableError(\n"
            "                \"No buffered frames for this message. It may have "
            "expired.\"\n"
            "            )\n"
            "\n"
            "        if isinstance(state, bytes):\n"
            "            state = state.decode(\"utf-8\", \"replace\")\n"
            "\n"
            "        # ARCH-0V Tranche 6: prove completeness before replaying.\n"
            "        #\n"
            "        # Through ARCH-22 this method returned an empty generator\n"
            "        # whenever the state was CLOSED and no frames came back, and\n"
            "        # the API turned that into `finish_reason: already_current`.\n"
            "        # That is a lie in three reachable situations: the buffer was\n"
            "        # disabled mid-turn, Redis evicted the frames list before the\n"
            "        # state key, or a Sentinel failover replicated them out of\n"
            "        # step. In each, the client is told it holds the whole turn\n"
            "        # while holding a truncated one — and truncated model output\n"
            "        # that looks complete is worse than a visible failure.\n"
            "        last_seq = _LASTSEQ_UNKNOWN\n"
            "        if raw_last_seq is not None:\n"
            "            if isinstance(raw_last_seq, bytes):\n"
            "                raw_last_seq = raw_last_seq.decode(\"utf-8\", \"replace\")\n"
            "            try:\n"
            "                last_seq = int(raw_last_seq)\n"
            "            except (TypeError, ValueError):\n"
            "                last_seq = _LASTSEQ_UNKNOWN\n"
            "\n"
            "        cursor_start = max(int(from_seq), 0)\n"
            "\n"
            "        if state != _STATE_OPEN:\n"
            "            if last_seq == _LASTSEQ_UNKNOWN:\n"
            "                raise ReplayIncompleteError(\n"
            "                    \"This turn's frame buffer was sealed without a \"\n"
            "                    \"sequence marker, so there is no way to prove you \"\n"
            "                    \"have the whole response. Refetch the message.\"\n"
            "                )\n"
            "            if cursor_start < last_seq and buffered < (\n"
            "                last_seq - cursor_start\n"
            "            ):\n"
            "                raise ReplayIncompleteError(\n"
            "                    f\"Frames {cursor_start + 1}..{last_seq} were \"\n"
            "                    f\"requested but only {buffered} remain buffered. \"\n"
            "                    \"The replay window has partially expired. Refetch \"\n"
            "                    \"the message.\"\n"
            "                )\n"
        ),
        rationale=(
            "The core D-0V.2 fix: CLOSED + empty no longer means 'already current'."
        ),
    ),
    Edit(
        edit_id="0V-6.9",
        applied_marker="cursor = cursor_start",
        anchor=(
            "        cursor = max(int(from_seq), 0)\n"
            "        idle_since = time.monotonic()\n"
        ),
        replacement=(
            "        cursor = cursor_start\n"
            "        idle_since = time.monotonic()\n"
        ),
        rationale="Reuse the cursor computed during the completeness check.",
    ),
    Edit(
        edit_id="0V-6.10",
        applied_marker='"ReplayIncompleteError",',
        anchor=(
            "class AssistantStreamService:\n"
        ),
        replacement=(
            "#: ARCH-0V: exported so the API layer can distinguish 'nothing to send'\n"
            "#: from 'I cannot prove there is nothing to send'.\n"
            "REPLAY_ERRORS = (\n"
            "    \"ReplayIncompleteError\",\n"
            "    \"ReplayUnavailableError\",\n"
            ")\n"
            "\n"
            "\n"
            "class AssistantStreamService:\n"
        ),
        rationale="Name the two failure modes for the API layer and the gate.",
    ),
]


# =====================================================================
# Tranche 6 — API layer: an explicit resume_unavailable signal
# =====================================================================

STREAM_API_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-6.11",
        applied_marker="ReplayIncompleteError",
        anchor=(
            "    try:\n"
            "        frames = assistant_stream_service.replay(\n"
            "            message_id=message_id, from_seq=from_seq, "
            "request_id=request_id\n"
            "        )\n"
            "        first = await frames.__anext__()\n"
            "    except ReplayUnavailableError as exc:\n"
        ),
        replacement=(
            "    try:\n"
            "        frames = assistant_stream_service.replay(\n"
            "            message_id=message_id, from_seq=from_seq, "
            "request_id=request_id\n"
            "        )\n"
            "        first = await frames.__anext__()\n"
            "    except ReplayIncompleteError as exc:\n"
            "        # ARCH-0V Tranche 6. Distinct from the 404 below: the message\n"
            "        # exists and frames may exist, but not the ones this client is\n"
            "        # missing. 409 rather than 404 so the resumable client in FE-1\n"
            "        # can tell 'gone' from 'incomplete' and refetch instead of\n"
            "        # rendering a truncated answer as a finished one.\n"
            "        raise HTTPException(\n"
            "            status_code=status.HTTP_409_CONFLICT,\n"
            "            detail={\n"
            "                \"code\": \"resume_unavailable\",\n"
            "                \"message\": str(exc),\n"
            "                \"action\": \"refetch_message\",\n"
            "                \"billed\": True,\n"
            "            },\n"
            "        ) from exc\n"
            "    except ReplayUnavailableError as exc:\n"
        ),
        rationale="Surface incompleteness as 409 + resume_unavailable, never as done.",
    ),
    Edit(
        edit_id="0V-6.12",
        applied_marker="ReplayIncompleteError,",
        anchor=(
            "from app.services.assistant_stream import (\n"
        ),
        replacement=(
            "from app.services.assistant_stream import (\n"
            "    ReplayIncompleteError,\n"
        ),
        rationale="Import the new exception in the router.",
    ),
    Edit(
        edit_id="0V-6.13",
        # Must be unique to THIS edit. `"resume_unavailable"` alone would be
        # shadowed by 0V-6.11's replacement, which lands in the same file
        # earlier in the same staging pass, and this edit would then report
        # SKIP forever without ever having been applied. Anchor markers are
        # checked against staged text, not the original.
        applied_marker="        409: {\n            \"description\": (",
        anchor=(
            "    responses={\n"
            "        404: {\"description\": \"Message not found, or no buffered frames "
            "remain.\"},\n"
            "    },\n"
        ),
        replacement=(
            "    responses={\n"
            "        404: {\"description\": \"Message not found, or no buffered frames "
            "remain.\"},\n"
            "        409: {\n"
            "            \"description\": (\n"
            "                \"`resume_unavailable` — the replay window has partially \"\n"
            "                \"expired and completeness cannot be proven. Refetch the \"\n"
            "                \"message; do not re-send the query, it was already \"\n"
            "                \"billed.\"\n"
            "            )\n"
            "        },\n"
            "    },\n"
        ),
        rationale="Document the 409 in OpenAPI so SDK generation picks it up.",
    ),
]


# =====================================================================
# Tranche 7 — R33 tenant scope at the selector boundary (D12)
# =====================================================================

EXECUTOR_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-7.1",
        applied_marker="tenant=_tenant_scope_for(state)",
        anchor=(
            "    selector = TOOL_SELECTORS[selector_name]\n"
            "    spec = selector(node_config=node_config, facts=state.facts)\n"
        ),
        replacement=(
            "    selector = TOOL_SELECTORS[selector_name]\n"
            "    # ARCH-0V Tranche 7. Selectors previously received a node config and\n"
            "    # a fact set and had no way to state which tenant they were acting\n"
            "    # for. The R33 boundary proved a document could not choose *what*\n"
            "    # happened; it could not prove anything about *whose* data it\n"
            "    # happened to. TenantScope closes that, and asserts the rule and\n"
            "    # the work item agree before any action is selected.\n"
            "    spec = selector(\n"
            "        node_config=node_config,\n"
            "        facts=state.facts,\n"
            "        tenant=_tenant_scope_for(state),\n"
            "    )\n"
        ),
        rationale="Thread tenant scope into every selector invocation.",
    ),
    Edit(
        edit_id="0V-7.2",
        applied_marker="def _tenant_scope_for(",
        anchor=(
            "def _run_action_node(\n"
        ),
        replacement=(
            "def _tenant_scope_for(state: \"_WalkState\") -> TenantScope:\n"
            "    \"\"\"The tenant this execution is acting for, cross-checked.\n"
            "\n"
            "    ARCH-0V Tranche 7. The assertion is the point: a rule belonging to\n"
            "    workspace A must never select an action against a work item in\n"
            "    workspace B. Nothing structurally prevented that before — the\n"
            "    executor received both objects and never compared them — and an\n"
            "    automation that mutates the wrong tenant's records is a\n"
            "    cross-tenant write with an audit trail that blames the wrong rule.\n"
            "    \"\"\"\n"
            "    scope = TenantScope(\n"
            "        workspace_id=state.rule.workspace_id,\n"
            "        rule_id=state.rule.id,\n"
            "        execution_id=state.execution.id,\n"
            "    )\n"
            "\n"
            "    # The live call site for TenantScope.assert_owns. Shipping that\n"
            "    # method with the comparison inlined here instead would leave it\n"
            "    # exported and uncalled, which is the orphaned-guard defect\n"
            "    # (invariant I4) this repository has now found five times.\n"
            "    if state.work_item is not None:\n"
            "        scope.assert_owns(\n"
            "            workspace_id=getattr(state.work_item, \"workspace_id\", None)\n"
            "        )\n"
            "\n"
            "    return scope\n"
            "\n"
            "\n"
            "def _run_action_node(\n"
        ),
        rationale="Build and cross-check the tenant scope from rule + work item.",
    ),
    Edit(
        edit_id="0V-7.3",
        # Extends the EXISTING contracts import block. An earlier draft added a
        # second `from app.services.automation.contracts import TenantScope`
        # line near the top — it worked, but two import statements against one
        # module is the kind of thing that survives forever once shipped.
        applied_marker="    TenantScope,\n    ToolContractViolation,",
        anchor=(
            "from app.services.automation.contracts import (\n"
            "    ActionNodeConfig,\n"
            "    ActionSpec,\n"
            "    FactSet,\n"
            "    ToolContractViolation,\n"
            ")\n"
        ),
        replacement=(
            "from app.services.automation.contracts import (\n"
            "    ActionNodeConfig,\n"
            "    ActionSpec,\n"
            "    FactSet,\n"
            "    TenantScope,\n"
            "    ToolContractViolation,\n"
            ")\n"
        ),
        rationale="Import the scope type into the executor.",
    ),
]


# =====================================================================
# Tranche 4 — frontend type lies (D07, D08)
# =====================================================================

AUTOMATION_TS_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-4.1",
        applied_marker="/* ARCH-0V: `user_id` removed",
        anchor=(
            "  readonly created_by_user_id?: string | null;\n"
            "  readonly user_id?: string;\n"
        ),
        replacement=(
            "  /* ARCH-0V: `user_id` removed. It was declared here and had no\n"
            "     backend source — AutomationRuleResponse carries\n"
            "     `created_by_user_id` (app/schemas/automation.py) and the model\n"
            "     column is `created_by_user_id` (app/models/automation.py). An\n"
            "     optional field that never arrives type-checks perfectly and\n"
            "     reads `undefined` forever. Gate 0V-G7 now enforces that every\n"
            "     field declared here exists on the response_model. */\n"
            "  readonly created_by_user_id?: string | null;\n"
        ),
        rationale="Delete the phantom user_id field.",
    ),
]

UPLOAD_DROPZONE_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-4.2",
        applied_marker="ARCH-0V: the `work_item` wrapper branch",
        anchor=(
            "  const wrapped = record.work_item;\n"
            "  if (\n"
            "    typeof wrapped === \"object\" &&\n"
            "    wrapped !== null &&\n"
            "    typeof (wrapped as Record<string, unknown>).id === \"string\"\n"
            "  ) {\n"
            "    return (wrapped as Record<string, unknown>).id as string;\n"
            "  }\n"
            "\n"
            "  return null;\n"
        ),
        replacement=(
            "  /* ARCH-0V: the `work_item` wrapper branch was removed. The upload\n"
            "     route is `@router.post(\"\", response_model=WorkItemResponse)` in\n"
            "     app/api/v1/work_items.py and has always returned the flat object.\n"
            "     The branch below it was defending against a shape the server has\n"
            "     never sent, which is how a phantom field survives four phases. */\n"
            "  return null;\n"
        ),
        rationale="Remove the dead branch that read a field the server never sends.",
    ),
]

PACKAGE_JSON_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-3.1",
        applied_marker='"lint": "eslint . --report-unused-disable-directives',
        anchor=(
            '    "lint": "eslint src --ext .ts,.tsx '
            '--report-unused-disable-directives --max-warnings=0",\n'
        ),
        replacement=(
            '    "lint": "eslint . --report-unused-disable-directives '
            '--max-warnings=0",\n'
        ),
        rationale=(
            "--ext is a no-op under flat config; file selection now lives in "
            "eslint.config.js."
        ),
    ),
]


# =====================================================================
# Tranche 7 — TenantScope enters the R33 contract vocabulary (D12)
# =====================================================================

CONTRACTS_EDITS: list[Edit] = [
    Edit(
        edit_id="0V-7.4",
        applied_marker="class TenantScope:",
        anchor=(
            "@dataclass(frozen=True)\n"
            "class FactSet:\n"
        ),
        replacement=(
            "@dataclass(frozen=True)\n"
            "class TenantScope:\n"
            "    \"\"\"Whose data a selector is acting on. ARCH-0V Tranche 7.\n"
            "\n"
            "    ARCH-13's R33 boundary proved that a retrieved document could not\n"
            "    choose *what* an automation did. It proved nothing about *whose*\n"
            "    records it did it to, because a selector received only a node\n"
            "    config and a fact set and had no tenant identity at all. A\n"
            "    selector could not have asserted isolation even if it wanted to.\n"
            "\n"
            "    A frozen dataclass rather than a dict, for the same reason\n"
            "    `FactSet` is: `fenced_context.check_callable` refuses a parameter\n"
            "    annotated `dict` or `Mapping` because such a parameter is\n"
            "    indistinguishable from a chunk. This type is annotatable, so a\n"
            "    selector taking `tenant: TenantScope` still passes the R33 check\n"
            "    at import time.\n"
            "\n"
            "    It carries identifiers only. No document text, no fact values, no\n"
            "    author-supplied strings — nothing that could be *emitted*. It\n"
            "    exists to be asserted against, not rendered.\n"
            "    \"\"\"\n"
            "\n"
            "    workspace_id: uuid.UUID\n"
            "    rule_id: uuid.UUID\n"
            "    execution_id: uuid.UUID\n"
            "\n"
            "    def assert_owns(self, *, workspace_id: Optional[uuid.UUID]) -> None:\n"
            "        \"\"\"Refuse if a resource belongs to a different workspace.\n"
            "\n"
            "        `None` is permitted and means 'this resource is not\n"
            "        workspace-scoped'. It is not treated as a match — a caller\n"
            "        passing None for something that *should* be scoped has a bug\n"
            "        upstream, and this method is not the place to guess.\n"
            "        \"\"\"\n"
            "        if workspace_id is None:\n"
            "            return\n"
            "        if workspace_id != self.workspace_id:\n"
            "            raise ToolContractViolation(\n"
            "                f\"Execution {self.execution_id} is scoped to workspace \"\n"
            "                f\"{self.workspace_id} but a selector was handed a \"\n"
            "                f\"resource in {workspace_id}. Refusing to act across a \"\n"
            "                f\"tenant boundary.\"\n"
            "            )\n"
            "\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class FactSet:\n"
        ),
        rationale="Introduce the tenant identity type the R33 boundary lacked.",
    ),
    Edit(
        edit_id="0V-7.5",
        applied_marker="import uuid",
        anchor=(
            "import hashlib\n"
            "import json\n"
            "import re\n"
        ),
        replacement=(
            "import hashlib\n"
            "import json\n"
            "import re\n"
            "import uuid\n"
        ),
        rationale="TenantScope annotates uuid.UUID fields.",
    ),
    Edit(
        edit_id="0V-7.6",
        applied_marker='    "TenantScope",',
        anchor=(
            '    "FactSet",\n'
            '    "ToolContractViolation",\n'
        ),
        replacement=(
            '    "FactSet",\n'
            '    "TenantScope",\n'
            '    "ToolContractViolation",\n'
        ),
        rationale="Export TenantScope.",
    ),
]


TARGETS: list[tuple[str, list[Edit]]] = [
    ("backend/app/services/automation/contracts.py", CONTRACTS_EDITS),
    ("backend/app/core/config.py", CONFIG_EDITS),
    ("backend/app/services/assistant_stream.py", STREAM_SERVICE_EDITS),
    ("backend/app/api/v1/assistant_stream.py", STREAM_API_EDITS),
    ("backend/app/services/automation/executor.py", EXECUTOR_EDITS),
    ("frontend/src/types/automation.ts", AUTOMATION_TS_EDITS),
    ("frontend/src/components/upload/UploadDropzone.tsx", UPLOAD_DROPZONE_EDITS),
    ("frontend/package.json", PACKAGE_JSON_EDITS),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-0V anchored edits.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    total_applied = 0
    total_skipped = 0
    failures: list[str] = []

    for rel_path, edits in TARGETS:
        absolute = REPO_ROOT / rel_path
        if not absolute.exists():
            failures.append(f"{rel_path}: file not found")
            continue

        source = absolute.read_text(encoding="utf-8-sig")
        staged = source
        outcomes: list[tuple[str, str]] = []

        try:
            for edit in edits:
                staged, outcome = edit.apply(staged, path=rel_path)
                outcomes.append((edit.edit_id, outcome))
        except AnchorMiss as exc:
            failures.append(str(exc))
            print(f"\n{rel_path}\n  FAILED — file left untouched\n  {exc}")
            continue

        applied = sum(1 for _, o in outcomes if o == "APPLIED")
        skipped = sum(1 for _, o in outcomes if o == "SKIP")
        total_applied += applied
        total_skipped += skipped

        status = "would apply" if args.check else "applied"
        print(f"\n{rel_path}")
        for edit_id, outcome in outcomes:
            print(f"  {edit_id:<10} {outcome}")

        if args.apply and staged != source:
            absolute.write_text(staged, encoding="utf-8", newline="\n")
            print(f"  -> written ({applied} {status}, {skipped} already present)")
        elif staged == source:
            print("  -> no change needed (idempotent)")

    print("\n" + "=" * 70)
    print(
        f"{total_applied} edit(s) pending/applied, "
        f"{total_skipped} already present, {len(failures)} failure(s)"
    )
    print("=" * 70)

    if failures:
        return 2
    if args.check and total_applied:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())