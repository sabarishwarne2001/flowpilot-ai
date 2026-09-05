#!/usr/bin/env python
"""ARCH-28 wiring — anchored, idempotent patches to five existing files.

    python scripts/patch_arch28_wiring.py
    python scripts/patch_arch28_wiring.py --check

The ARCH-19 precedent, reused by ARCH-23, ARCH-25, ARCH-26 and ARCH-27: for a
large shared file receiving a surgical change, an anchored patch that fails
loudly on a missing anchor is safer to review and safer to re-run than a full
rewrite, because a full rewrite silently reverts anything that landed in the
file between the two phases.

Every patch checks for its own marker first, so running this twice is a no-op
and running it after a partial failure completes the rest.

FILES TOUCHED
=============
    app/core/config.py                    four declared SAML Settings fields
    app/main.py                           register DeprecationMiddleware
    app/services/identity/saml_gateway.py call the ARCH-28 defences
    app/api/v1/saml.py                    bind the verified issuer to the config
    scripts/verify_arch16_full_compatibility.py   stale head assertion

WHY THE GATEWAY PATCH IS THE WHOLE POINT
========================================

`app/services/auth/saml_security.py` is correct and, until this script runs,
has zero call sites. That is the recurring defect class in this codebase — a
control that is real, correct, and not connected to the thing it is supposed
to control (invariant I4, the orphaned guard: `buildOrganizationNavigationItems`,
`LEAKED_JWT_SECRET_KEYS`, `uncovered_job_types`, `require_superadmin`). A
linter cannot see it. A test suite that imports the module directly, as
`tests/security/test_saml_xsw_rig.py` does, passes perfectly while the ACS
endpoint remains exactly as vulnerable as it was.

`verify_arch28.py` check G3 asserts the call sites exist. This script creates
them.

THE CONFIG PATCH IS NOT COSMETIC
================================

`saml_gateway` reads `SAML_CLOCK_SKEW_S` and `app/api/v1/saml.py` reads
`SAML_RAW_ASSERTION_RETENTION_DAYS`, both through
`getattr(settings, NAME, default)`. Neither is a declared Settings field, and
`model_config` sets `extra="ignore"`, so an environment variable with no
matching field is DISCARDED. Those two settings have therefore never read
configuration on any deployment — they return their literal defaults, always.

This is the identical defect ARCH-25 found and fixed for ARCH-16's
`DNS_RESOLVERS`, `DNS_TIMEOUT_S`, `DOMAIN_VERIFICATION_TXT_PREFIX` and
`DOMAIN_VERIFICATION_TOKEN_TTL_DAYS`, and the ARCH-25 config block documents it
at length. ARCH-16 had four more instances that the ARCH-25 sweep did not
reach. This closes them.

The third instance is worse than dead: `SAML_CRYPTO_BACKEND` is named to the
operator as the REMEDY for an encrypted-assertion refusal. It is not declared,
so setting it does nothing, and `python3-saml` is not installed, so it could
not work if it were declared. An error message that sends an operator to a
setting that does not exist is worse than an error message with no remedy at
all, because the operator spends an afternoon before concluding the same thing.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Read from argv at MODULE LOAD, not in main().
#:
#: The patches below run at import time so that `--check` and the real apply
#: share one code path — the ARCH-27 precedent. But `patch_arch27_wiring.py`
#: (and ARCH-26's) set `CHECK_ONLY = args.check` inside `main()`, which runs
#: AFTER every patch has already executed. `--check` therefore writes the files
#: for real on all three scripts, and then reports "WOULD PATCH". Confirmed by
#: running it: the second invocation reports "already present".
#:
#: Nobody noticed because the patches are idempotent, so the damage is invisible
#: unless you were relying on `--check` to inspect before committing — which is
#: the only reason the flag exists.
#:
#: Fixed here. `scripts/patch_arch26_wiring.py` and `patch_arch27_wiring.py`
#: still carry it; both are recorded in the ARCH-28 runbook as a follow-up,
#: deliberately NOT fixed in this phase because editing a shipped phase's patch
#: script is a change to sealed history and belongs in its own change.
CHECK_ONLY = "--check" in sys.argv[1:]
_applied: list[str] = []
_skipped: list[str] = []
_failed: list[str] = []


class AnchorMissing(RuntimeError):
    """An anchor was not found. The file has moved on; stop rather than guess."""


def _read(relative: str) -> tuple[pathlib.Path, str]:
    path = ROOT / relative
    if not path.exists():
        raise AnchorMissing(f"{relative} does not exist.")
    # utf-8-sig: app/schemas/usage.py carries a pre-existing UTF-8 BOM and the
    # same reader is used across every ARCH-0V-era script for consistency.
    return path, path.read_text(encoding="utf-8-sig")


def _write(path: pathlib.Path, text: str) -> None:
    if CHECK_ONLY:
        return
    path.write_text(text, encoding="utf-8")


def patch(relative: str, *, marker: str, anchor: str, replacement: str) -> None:
    """Insert `replacement` in place of `anchor`, once.

    `marker` is a string that exists only after the patch has been applied.
    Checking it rather than checking for the replacement itself means a patch
    whose replacement was later hand-edited is still recognised as applied.
    """
    try:
        path, source = _read(relative)
    except AnchorMissing as exc:
        _failed.append(f"{relative}: {exc}")
        return

    if marker in source:
        _skipped.append(f"{relative} (already applied)")
        return

    if anchor not in source:
        _failed.append(
            f"{relative}: anchor not found. Expected to find:\n"
            f"    {anchor.splitlines()[0][:100]}"
        )
        return

    if source.count(anchor) != 1:
        _failed.append(
            f"{relative}: anchor appears {source.count(anchor)} times; "
            "refusing to guess which one."
        )
        return

    _write(path, source.replace(anchor, replacement, 1))
    _applied.append(relative)


# ===========================================================================
# 1. app/core/config.py — four declared SAML fields
# ===========================================================================

patch(
    "app/core/config.py",
    marker="SAML_XSW_DEFENCE_ENABLED",
    anchor=(
        "    # ======================================================================\n"
        "    # ARCH-25 — white-label, custom domains and tenant branding."
    ),
    replacement=(
        "    # ======================================================================\n"
        "    # ARCH-28 — SAML settings that ARCH-16 read but never declared.\n"
        "    #\n"
        "    # The same defect the ARCH-25 block below documents, four more\n"
        "    # instances of it. `saml_gateway` reads SAML_CLOCK_SKEW_S and\n"
        "    # `app/api/v1/saml.py` reads SAML_RAW_ASSERTION_RETENTION_DAYS, both\n"
        "    # through getattr(). Neither was declared, so extra=\"ignore\"\n"
        "    # discarded them and both have returned their literal defaults on\n"
        "    # every deployment since ARCH-16 shipped.\n"
        "    #\n"
        "    # SAML_CRYPTO_BACKEND is the worst of the four: it was named to the\n"
        "    # operator as the REMEDY in the encrypted-assertion error message. It\n"
        "    # is declared here so the value is at least readable, and pinned to a\n"
        "    # single legal value, because python3-saml is not installed and\n"
        "    # xmlsec1 is not in the runtime image by deliberate policy. See\n"
        "    # app/services/auth/saml_security.XMLSEC1_POLICY for the decision and\n"
        "    # its reasoning.\n"
        "    SAML_CLOCK_SKEW_S: int = 120\n"
        "    SAML_RAW_ASSERTION_RETENTION_DAYS: int = 30\n"
        "    SAML_CRYPTO_BACKEND: Literal[\"signxml\"] = \"signxml\"\n"
        "\n"
        "    # The ARCH-28 XSW kill switch. Defaults to on and FAILS to on: if\n"
        "    # settings cannot be read at all, hardening_policy_from_settings()\n"
        "    # returns the hardened policy. A GA platform needs a switch for the\n"
        "    # night a real IdP turns out to emit something structurally odd; it\n"
        "    # does not need one that can be reached by accident.\n"
        "    SAML_XSW_DEFENCE_ENABLED: bool = True\n"
        "\n"
        "    # ======================================================================\n"
        "    # ARCH-25 — white-label, custom domains and tenant branding."
    ),
)

# `Literal` may not be imported yet.
patch(
    "app/core/config.py",
    marker="# ARCH-28: Literal is used by SAML_CRYPTO_BACKEND",
    anchor="from pydantic_settings import BaseSettings, SettingsConfigDict",
    replacement=(
        "from pydantic_settings import BaseSettings, SettingsConfigDict\n"
        "\n"
        "# ARCH-28: Literal is used by SAML_CRYPTO_BACKEND to pin the one legal\n"
        "# value, so a deployment that sets it to python3-saml fails at startup\n"
        "# rather than at the first encrypted assertion.\n"
        "from typing import Literal  # noqa: E402"
    ),
)


# ===========================================================================
# 2. app/main.py — register the deprecation middleware
# ===========================================================================

patch(
    "app/main.py",
    marker="from app.middleware.deprecation import DeprecationMiddleware",
    anchor="from app.middleware.global_rate_limit import GlobalRateLimitMiddleware",
    replacement=(
        "from app.middleware.deprecation import DeprecationMiddleware\n"
        "from app.middleware.global_rate_limit import GlobalRateLimitMiddleware"
    ),
)

patch(
    "app/main.py",
    marker="app.add_middleware(DeprecationMiddleware)",
    anchor="app.add_middleware(RequestTraceMiddleware)",
    replacement=(
        "app.add_middleware(RequestTraceMiddleware)\n"
        "\n"
        "# ARCH-28 RFC 8594. Registered LAST, which in Starlette makes it the\n"
        "# OUTERMOST layer: the effective order becomes\n"
        "#   Deprecation -> RequestTrace -> PublicApiRateLimit -> GlobalRateLimit\n"
        "#   -> HostTenant -> app.\n"
        "#\n"
        "# Outermost is deliberate. ARCH-21's _apply_version_headers runs INSIDE\n"
        "# each public gateway handler, so a 429 from the rate limiter, a 404\n"
        "# from host resolution and a 401 from an expired key all carry no\n"
        "# policy headers — and a client being throttled mid-migration is\n"
        "# precisely the client that needs to see the sunset date.\n"
        "#\n"
        "# It never overwrites a header a handler already set, and it MERGES\n"
        "# Link rather than replacing it, so the gateway's rel=\"describedby\"\n"
        "# and this layer's rel=\"sunset\" coexist.\n"
        "app.add_middleware(DeprecationMiddleware)"
    ),
)


# ===========================================================================
# 3. app/services/identity/saml_gateway.py — call the ARCH-28 defences
# ===========================================================================

patch(
    "app/services/identity/saml_gateway.py",
    marker="from app.services.auth import saml_security",
    anchor=(
        "from app.services.identity._integration import get_settings, utcnow\n"
        "from app.services.identity.errors import AssertionRejected"
    ),
    replacement=(
        "from app.services.auth import saml_security\n"
        "from app.services.identity._integration import get_settings, utcnow\n"
        "from app.services.identity.errors import AssertionRejected"
    ),
)

# 3a. Replace the dead-end encrypted-assertion refusal.
patch(
    "app/services/identity/saml_gateway.py",
    marker="saml_security.refuse_encrypted_assertion(envelope)",
    anchor=(
        '    if envelope.find(".//xenc:EncryptedData", NS) is not None:\n'
        '        raise AssertionRejected(\n'
        '            "REJECTED_UNKNOWN",\n'
        '            "encrypted assertions require the xmlsec backend; set '
        'SAML_CRYPTO_BACKEND=python3-saml")'
    ),
    replacement=(
        "    # ARCH-28 tranche 3. The previous message named\n"
        "    # SAML_CRYPTO_BACKEND=python3-saml as the remedy: an undeclared\n"
        "    # setting, discarded by extra=\"ignore\", for a backend that is not\n"
        "    # installed. This one names the IdP screen to change instead.\n"
        "    saml_security.refuse_encrypted_assertion(envelope)"
    ),
)

# 3b. THE XSW GATE. This is the call site that makes saml_security real.
patch(
    "app/services/identity/saml_gateway.py",
    marker="saml_security.enforce_structural_integrity(",
    anchor=(
        "    verified_root = get_backend().verify(raw, idp_certificates)\n"
        "\n"
        "    assertion = verified_root\n"
        "    if not assertion.tag.endswith(\"Assertion\"):\n"
        "        assertion = verified_root.find(\"./saml:Assertion\", NS)\n"
        "    if assertion is None:\n"
        "        raise AssertionRejected(\n"
        "            \"REJECTED_SIGNATURE\",\n"
        "            \"the signature did not cover an Assertion element\")\n"
        "\n"
        "    assertion_id = assertion.get(\"ID\")\n"
        "    if not assertion_id:\n"
        "        raise AssertionRejected(\"REJECTED_SIGNATURE\", "
        "\"verified assertion has no ID\")"
    ),
    replacement=(
        "    # ARCH-28: certificate windows first, so an expired IdP certificate\n"
        "    # produces a dated refusal instead of arriving as the generic\n"
        "    # \"no configured certificate verified the signature\" that\n"
        "    # SignXmlBackend.verify flattens every certificate error into.\n"
        "    policy = saml_security.hardening_policy_from_settings(settings)\n"
        "    idp_certificates = saml_security.verify_certificate_validity(\n"
        "        idp_certificates, now=now, policy=policy)\n"
        "\n"
        "    verified_root = get_backend().verify(raw, idp_certificates)\n"
        "\n"
        "    assertion = verified_root\n"
        "    if not assertion.tag.endswith(\"Assertion\"):\n"
        "        assertion = verified_root.find(\"./saml:Assertion\", NS)\n"
        "    if assertion is None:\n"
        "        raise AssertionRejected(\n"
        "            \"REJECTED_SIGNATURE\",\n"
        "            \"the signature did not cover an Assertion element\")\n"
        "\n"
        "    assertion_id = assertion.get(\"ID\")\n"
        "    if not assertion_id:\n"
        "        raise AssertionRejected(\"REJECTED_SIGNATURE\", "
        "\"verified assertion has no ID\")\n"
        "\n"
        "    # ARCH-28 XSW GATE. signxml returns the subtree the Reference\n"
        "    # actually covered, so a wrapped document does not impersonate —\n"
        "    # but XSW-2, XSW-3, XSW-6 and plain injection all VERIFY, measured\n"
        "    # against signxml 5.1.0. What gets through is a document containing\n"
        "    # an assertion this SP never consumed, which then lands in\n"
        "    # sso_assertions.raw_payload and in front of every incident\n"
        "    # responder who reads it. Refusing the shape removes the whole\n"
        "    # family and removes the dependence on nobody ever reading from\n"
        "    # `envelope` again.\n"
        "    saml_security.enforce_structural_integrity(\n"
        "        raw, verified_assertion_id=assertion_id, policy=policy)\n"
        "\n"
        "    saml_security.require_bearer_confirmation(assertion, policy=policy)"
    ),
)

# 3c. InResponseTo must come from inside the signature.
patch(
    "app/services/identity/saml_gateway.py",
    marker="saml_security.require_signed_request_binding(",
    anchor=(
        "    in_response_to = (subject_confirmation.get(\"InResponseTo\")\n"
        "                      if subject_confirmation is not None else None)\n"
        "    if in_response_to is None:\n"
        "        in_response_to = envelope.get(\"InResponseTo\")"
    ),
    replacement=(
        "    # ARCH-28: resolved from SIGNED data only.\n"
        "    #\n"
        "    # The removed line was `in_response_to = envelope.get(\"InResponseTo\")`\n"
        "    # — a read from outside the signature, feeding the comparison two\n"
        "    # blocks below that decides whether this assertion answers an\n"
        "    # AuthnRequest we issued. An attacker replaying an IdP-initiated\n"
        "    # assertion at an SP with allow_unsolicited=False could satisfy that\n"
        "    # check by stamping a Response/@InResponseTo of their choosing; the\n"
        "    # assertion was never bound to our request at all.\n"
        "    in_response_to = saml_security.require_signed_request_binding(\n"
        "        assertion, expected_in_response_to=expected_in_response_to,\n"
        "        policy=policy)"
    ),
)


# ===========================================================================
# 4. app/api/v1/saml.py — bind the verified issuer to the selected config
# ===========================================================================

patch(
    "app/api/v1/saml.py",
    marker="saml_security.bind_issuer(",
    anchor=(
        "        saml_gateway.guard_replay(db, assertion_id=data.assertion_id,\n"
        "                                  idp_config_id=config.id,\n"
        "                                  not_on_or_after=data.not_on_or_after)"
    ),
    replacement=(
        "        # ARCH-28: the config above was chosen by reading saml:Issuer\n"
        "        # from the UNVERIFIED envelope. Nothing compared that choice\n"
        "        # back to the issuer inside the signed assertion. Today the\n"
        "        # mismatch is caught incidentally, because the wrong config\n"
        "        # carries the wrong certificate — but that is a property of the\n"
        "        # certificate inventory, not of the code. Two organizations\n"
        "        # behind one Entra tenant, or a certificate copied during a\n"
        "        # migration, and cross-tenant assertion acceptance is immediate.\n"
        "        from app.services.auth import saml_security\n"
        "\n"
        "        saml_security.bind_issuer(\n"
        "            verified_issuer=data.issuer,\n"
        "            configured_entity_id=config.idp_entity_id,\n"
        "        )\n"
        "        saml_gateway.guard_replay(db, assertion_id=data.assertion_id,\n"
        "                                  idp_config_id=config.id,\n"
        "                                  not_on_or_after=data.not_on_or_after)"
    ),
)


# ===========================================================================
# 5. Historical gate reconciliation — the stale head assertion
# ===========================================================================
#
# `verify_arch16_full_compatibility.py` asserts the Alembic head is
# `arch16_step8_outbox_identity_vocabulary`. Eleven phases and sixty-odd
# migrations later that is false, and the gate fails for a reason that has
# nothing to do with ARCH-16's compatibility.
#
# The fix is NOT to bump the literal to revision 118 — that just moves the same
# time bomb to ARCH-29. The check that ARCH-16 actually wanted was "the DAG has
# a SINGLE head", which is the invariant that catches a merge conflict in
# alembic. Assert that, and assert ARCH-16's own revision is reachable from it.

patch(
    "scripts/verify_arch16_full_compatibility.py",
    marker="# ARCH-28 reconciliation: assert single head, not a frozen literal",
    anchor=(
        "        if len(heads) == 1 and heads[0] == \"arch16_step8_outbox_identity_vocabulary\":\n"
        "            record_pass(\"Alembic DAG Integrity\", f\"Single clean head at '{heads[0]}'\")\n"
        "        else:\n"
        "            record_fail(\"Alembic DAG Integrity\", f\"Expected single head "
        "'arch16_step8_outbox_identity_vocabulary', found: {heads}\")"
    ),
    replacement=(
        "        # ARCH-28 reconciliation: assert single head, not a frozen literal.\n"
        "        #\n"
        "        # This asserted heads == ['arch16_step8_outbox_identity_vocabulary'],\n"
        "        # which stopped being true the moment ARCH-17 shipped. Bumping the\n"
        "        # literal to the current head would move the same failure to the\n"
        "        # next phase. The invariant ARCH-16 wanted is that the DAG has ONE\n"
        "        # head — that is what catches an alembic merge conflict — plus\n"
        "        # that ARCH-16's own revision is still reachable from it, which is\n"
        "        # what catches someone deleting it.\n"
        "        ARCH16_REVISION = \"arch16_step8_outbox_identity_vocabulary\"\n"
        "        if len(heads) != 1:\n"
        "            record_fail(\"Alembic DAG Integrity\",\n"
        "                        f\"Expected exactly one head, found: {heads}\")\n"
        "        else:\n"
        "            lineage = {rev.revision for rev in "
        "script.iterate_revisions(heads[0], \"base\")}\n"
        "            if ARCH16_REVISION in lineage:\n"
        "                record_pass(\"Alembic DAG Integrity\",\n"
        "                            f\"Single head '{heads[0]}'; {ARCH16_REVISION} \"\n"
        "                            f\"reachable across {len(lineage)} revisions\")\n"
        "            else:\n"
        "                record_fail(\"Alembic DAG Integrity\",\n"
        "                            f\"{ARCH16_REVISION} is not in the lineage of \"\n"
        "                            f\"head '{heads[0]}'\")"
    ),
)


# ---------------------------------------------------------------------------
# 5b. The three LIVE-DATABASE head assertions.
# ---------------------------------------------------------------------------
#
# These survived eleven phases because they only execute where PostgreSQL is
# reachable, and the routine gate runs are static. They fail the moment
# run_all_gates.py is pointed at a real database — which is the ARCH-28 GA
# criterion, so they have to be reconciled now.
#
# In all three the reconciliation is the same shape as ARCH-16's: the phase's
# own revision must still be REACHABLE from the head, which is the invariant
# each gate actually wanted, instead of BEING the head, which stopped being
# true the moment the next phase shipped. Reachability stays true forever;
# equality is a dated assertion with no expiry warning.

patch(
    "scripts/verify_arch07_final.py",
    marker="# ARCH-28 reconciliation: ARCH-07's revision must be REACHABLE",
    anchor=(
        "        head = session.execute(\n"
        "            text(\"SELECT version_num FROM alembic_version\")\n"
        "        ).scalar_one()\n"
        "        check(f\"alembic head == {EXPECTED_HEAD}\", head == EXPECTED_HEAD,\n"
        "              f\"head={head}\")"
    ),
    replacement=(
        "        # ARCH-28 reconciliation: ARCH-07's revision must be REACHABLE\n"
        "        # from the head, not BE the head. This asserted equality against\n"
        "        # b6e1d94f07ca and has been false since ARCH-08 shipped; it only\n"
        "        # ever ran where PostgreSQL was reachable, which is why nobody\n"
        "        # saw it during eleven phases of static gate runs.\n"
        "        head = session.execute(\n"
        "            text(\"SELECT version_num FROM alembic_version\")\n"
        "        ).scalar_one()\n"
        "        from alembic.config import Config as _AlembicConfig\n"
        "        from alembic.script import ScriptDirectory as _ScriptDirectory\n"
        "\n"
        "        _script = _ScriptDirectory.from_config(\n"
        "            _AlembicConfig(str(backend_dir / \"alembic.ini\")))\n"
        "        lineage = {r.revision for r in _script.iterate_revisions(head, \"base\")}\n"
        "        check(f\"{EXPECTED_HEAD} is reachable from the live head\",\n"
        "              EXPECTED_HEAD in lineage,\n"
        "              f\"head={head}; ARCH-07's revision is not in its lineage\")"
    ),
)

patch(
    "scripts/verify_arch11_step2.py",
    marker="# ARCH-28 reconciliation: single head, ARCH-11 reachable",
    anchor=(
        "        heads = sql(\"SELECT version_num FROM alembic_version\").scalars().all()\n"
        "        check(\n"
        "            \"S2.15\",\n"
        "            heads == [\"arch11_step2_chunks_expand\"],\n"
        "            f\"alembic head: {heads}\",\n"
        "        )"
    ),
    replacement=(
        "        # ARCH-28 reconciliation: single head, ARCH-11 reachable.\n"
        "        # S2.15 asserted the head WAS arch11_step2_chunks_expand, which\n"
        "        # stopped being true at ARCH-12. The invariant it wanted is that\n"
        "        # the DAG has one head and this phase's expand migration is still\n"
        "        # in its lineage.\n"
        "        heads = sql(\"SELECT version_num FROM alembic_version\").scalars().all()\n"
        "        from alembic.config import Config as _AlembicConfig\n"
        "        from alembic.script import ScriptDirectory as _ScriptDirectory\n"
        "\n"
        "        _script = _ScriptDirectory.from_config(\n"
        "            _AlembicConfig(str(REPO_ROOT / \"alembic.ini\")))\n"
        "        _lineage = (\n"
        "            {r.revision for r in _script.iterate_revisions(heads[0], \"base\")}\n"
        "            if len(heads) == 1 else set()\n"
        "        )\n"
        "        check(\n"
        "            \"S2.15\",\n"
        "            len(heads) == 1 and \"arch11_step2_chunks_expand\" in _lineage,\n"
        "            f\"alembic head: {heads}\",\n"
        "        )"
    ),
)

patch(
    "scripts/verify_arch12.py",
    marker="# ARCH-28 reconciliation: ARCH-12's revision must be REACHABLE",
    anchor=(
        "    @check(\"db    migration head is arch12_step7_notification_deliveries\")\n"
        "    def _head() -> None:\n"
        "        with engine.connect() as connection:\n"
        "            rows = connection.execute(\n"
        "                text(\"SELECT version_num FROM alembic_version\")\n"
        "            ).scalars().all()\n"
        "        assert EXPECTED_HEAD in rows, f\"head is {rows}, expected {EXPECTED_HEAD}\""
    ),
    replacement=(
        "    @check(\"db    arch12_step7_notification_deliveries is reachable from head\")\n"
        "    def _head() -> None:\n"
        "        # ARCH-28 reconciliation: ARCH-12's revision must be REACHABLE\n"
        "        # from the head, not BE the head. `EXPECTED_HEAD in rows` reads\n"
        "        # like a membership test but rows is the single-row contents of\n"
        "        # alembic_version, so it was an equality in disguise and has\n"
        "        # been false since ARCH-13.\n"
        "        from alembic.config import Config as _AlembicConfig\n"
        "        from alembic.script import ScriptDirectory as _ScriptDirectory\n"
        "\n"
        "        with engine.connect() as connection:\n"
        "            rows = connection.execute(\n"
        "                text(\"SELECT version_num FROM alembic_version\")\n"
        "            ).scalars().all()\n"
        "        assert len(rows) == 1, f\"expected a single head, found {rows}\"\n"
        "        _script = _ScriptDirectory.from_config(\n"
        "            _AlembicConfig(str(ROOT / \"alembic.ini\")))\n"
        "        lineage = {r.revision for r in _script.iterate_revisions(rows[0], \"base\")}\n"
        "        assert EXPECTED_HEAD in lineage, (\n"
        "            f\"head is {rows[0]}; {EXPECTED_HEAD} is not in its lineage\")"
    ),
)


# ---------------------------------------------------------------------------
# 5c. The ROLLING head allowlists in ARCH-25 and ARCH-26.
# ---------------------------------------------------------------------------
#
# Found by running the reconciled run_all_gates.py, not by reading. Both gates
# scan the migration files themselves — no database — and then assert the head
# is a member of a hardcoded set:
#
#   verify_arch25.py: heads[0] not in ("arch25_step2_custom_domains",
#                                      "arch26_step2_warehouse_sync")
#   verify_arch26.py: heads[0] != "arch26_step2_warehouse_sync"
#
# ARCH-25's set has two members because ARCH-26 came along and appended itself
# rather than fixing the shape. That is the failure mode in miniature: each
# phase extends the allowlist by one, every earlier gate stays green for
# exactly one more phase, and the maintenance cost is paid forever by whoever
# ships next. ARCH-27 did not pay it, so both gates are red today.
#
# Same reconciliation as everywhere else in this phase: single head, and this
# phase's own terminal revision REACHABLE from it. Reachability does not need
# extending when ARCH-29 ships.

patch(
    "scripts/verify_arch25.py",
    marker="# ARCH-28 reconciliation: reachability, not a rolling allowlist",
    anchor=(
        "    if len(heads) != 1:\n"
        "        problems.append(f\"expected a single head, found {heads}\")\n"
        "    elif heads[0] not in (\"arch25_step2_custom_domains\", "
        "\"arch26_step2_warehouse_sync\"):\n"
        "        problems.append(f\"unexpected head: {heads[0]}\")"
    ),
    replacement=(
        "    # ARCH-28 reconciliation: reachability, not a rolling allowlist.\n"
        "    # The tuple gained a member when ARCH-26 shipped and was not\n"
        "    # extended for ARCH-27, so this gate has been red since. Asserting\n"
        "    # that ARCH-25's terminal revision is an ANCESTOR of the head is the\n"
        "    # invariant that was wanted, and it needs no maintenance.\n"
        "    if len(heads) != 1:\n"
        "        problems.append(f\"expected a single head, found {heads}\")\n"
        "    else:\n"
        "        _seen, _cursor = set(), heads[0]\n"
        "        while _cursor and _cursor not in _seen:\n"
        "            _seen.add(_cursor)\n"
        "            _cursor = downs.get(_cursor)\n"
        "        if \"arch25_step2_custom_domains\" not in _seen:\n"
        "            problems.append(\n"
        "                f\"arch25_step2_custom_domains is not an ancestor of \"\n"
        "                f\"the head {heads[0]}\")"
    ),
)

patch(
    "scripts/verify_arch26.py",
    marker="# ARCH-28 reconciliation: reachability, not a pinned head",
    anchor=(
        "    if len(heads) != 1:\n"
        "        problems.append(f\"{len(heads)} heads: {sorted(heads)}\")\n"
        "    elif heads[0] != \"arch26_step2_warehouse_sync\":\n"
        "        problems.append(f\"head is {heads[0]!r}\")"
    ),
    replacement=(
        "    # ARCH-28 reconciliation: reachability, not a pinned head.\n"
        "    # This asserted the head WAS arch26_step2_warehouse_sync, which\n"
        "    # stopped being true the moment ARCH-27 shipped.\n"
        "    if len(heads) != 1:\n"
        "        problems.append(f\"{len(heads)} heads: {sorted(heads)}\")\n"
        "    else:\n"
        "        _seen, _cursor = set(), heads[0]\n"
        "        while _cursor and _cursor not in _seen:\n"
        "            _seen.add(_cursor)\n"
        "            _cursor = revisions.get(_cursor)\n"
        "        if \"arch26_step2_warehouse_sync\" not in _seen:\n"
        "            problems.append(\n"
        "                f\"arch26_step2_warehouse_sync is not an ancestor of \"\n"
        "                f\"the head {heads[0]!r}\")"
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-28 wiring patches")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what would change without writing",
    )
    parser.parse_args()

    # The patches above ran at import time, under the CHECK_ONLY resolved from
    # argv at module load. Nothing below re-runs them; this only reports.
    for entry in _applied:
        print(f"  {'WOULD PATCH' if CHECK_ONLY else 'PATCHED'}  {entry}")
    for entry in _skipped:
        print(f"  SKIP     {entry}")
    for entry in _failed:
        print(f"  FAILED   {entry}", file=sys.stderr)

    print(
        f"\n{len(_applied)} applied, {len(_skipped)} already present, "
        f"{len(_failed)} failed."
    )
    if _failed:
        print(
            "\nAn anchor was missing. That means the target file has changed "
            "since ARCH-28 was written. Do NOT hand-apply blindly — re-read "
            "the file and update the anchor in this script, so the next run "
            "is still idempotent.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())