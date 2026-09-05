"""Runtime hardening patch — RH-1 through RH-4.

Anchored, idempotent, fail-loud edits to shared files. Follows the ARCH-19
precedent: never rewrite a large shared file wholesale, never guess at an
anchor, never silently skip a miss.

Run from the backend directory:

    python scripts/patch_runtime_hardening.py            # apply
    python scripts/patch_runtime_hardening.py --check    # report only, exit 1 if unapplied
    python scripts/patch_runtime_hardening.py --revert   # not supported; use git

What it fixes
-------------
RH-1  app/core/config.py
      Declares S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY / S3_SESSION_TOKEN with
      AWS_* validation aliases, and resolves development defaults for MinIO.

      Root cause: Settings.model_config sets extra="ignore". backend/.env.example
      ships AWS_ACCESS_KEY_ID=minioadmin, but pydantic-settings discards keys it
      has not declared and never writes them to os.environ. boto3 reads
      os.environ, not the Settings object. A host-run API process therefore has
      no credentials at all and every upload dies with NoCredentialsError. The
      containers happened to work only because docker-compose.yml passes AWS_*
      through as real process environment variables.

RH-2  app/core/storage/s3.py
      Threads explicit credentials into boto3.client(). Fixes both the primary
      driver and the ARCH-20 regional drivers, which construct the same class.

RH-3  app/worker.py
      Adds --loop all and --loop scheduler, routed to the new supervisor. The
      previous entrypoint ran exactly one loop with no exception guard outside
      the handler call, so a transient OperationalError raised by claim_jobs(),
      reap_expired_leases() or db.commit() escaped main() and killed the
      process. That is the "worker terminates unexpectedly" symptom.

RH-4  deploy/bin/flowpilot-sweep + deploy/cron.d/flowpilot-sweepers
      Wires scripts/sweep_compliance.py, which exists but is dispatched by
      nothing. ARCH-20 retention has never run in production. Orphaned-guard
      pattern, scheduling layer.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parent.parent


class AnchorMiss(RuntimeError):
    """Raised when an expected anchor is absent. Never downgraded to a warning."""


@dataclass
class Edit:
    ident: str
    path: Path
    sentinel: str
    apply: Callable[[str], str]


def _insert_after(source: str, anchor: str, addition: str, *, ident: str) -> str:
    idx = source.find(anchor)
    if idx == -1:
        raise AnchorMiss(f"{ident}: anchor not found:\n{anchor!r}")
    if source.count(anchor) != 1:
        raise AnchorMiss(
            f"{ident}: anchor appears {source.count(anchor)} times, expected exactly 1"
        )
    cut = idx + len(anchor)
    return source[:cut] + addition + source[cut:]


def _replace_once(source: str, old: str, new: str, *, ident: str) -> str:
    if source.count(old) != 1:
        raise AnchorMiss(
            f"{ident}: replacement target appears {source.count(old)} times, expected exactly 1"
        )
    return source.replace(old, new)


# ---------------------------------------------------------------------------
# RH-1 — app/core/config.py
# ---------------------------------------------------------------------------

_RH1_SENTINEL = "S3_ACCESS_KEY_ID"

_RH1_IMPORT_ANCHOR = (
    "from pydantic import field_validator, SecretStr, model_validator"
)
_RH1_IMPORT_NEW = (
    "from pydantic import (\n"
    "    AliasChoices,\n"
    "    Field,\n"
    "    field_validator,\n"
    "    SecretStr,\n"
    "    model_validator,\n"
    ")"
)

_RH1_FIELD_ANCHOR = "    S3_MAX_CONCURRENCY: int = 4\n"

_RH1_FIELDS = '''
    # ======================================================================
    # RH-1 — object storage credentials.
    #
    # These were never declared. backend/.env.example has shipped
    # AWS_ACCESS_KEY_ID=minioadmin since ARCH-10, but `model_config` sets
    # extra="ignore", so pydantic-settings read the key, found no matching
    # field, and dropped it. It was never written to os.environ, which is
    # the only place botocore's credential chain looks. Every host-run
    # process (uvicorn in a venv, `python -m app.worker`) therefore had no
    # credentials and raised NoCredentialsError on the first PutObject.
    # docker-compose.yml masked this by exporting AWS_* into the container
    # environment for real.
    #
    # AliasChoices keeps every existing .env and compose file working: the
    # canonical name is S3_*, the AWS_* spelling is accepted verbatim.
    # ======================================================================
    S3_ACCESS_KEY_ID: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
    )
    S3_SECRET_ACCESS_KEY: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices(
            "S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"
        ),
    )
    S3_SESSION_TOKEN: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("S3_SESSION_TOKEN", "AWS_SESSION_TOKEN"),
    )

    #: When true and ENVIRONMENT is development or test, an object-storage
    #: backend with no credentials resolves to the docker-compose MinIO
    #: defaults instead of falling through to botocore's ambient chain.
    #: Ignored outside development and test, where a missing credential is a
    #: hard boot failure rather than something to paper over.
    S3_DEV_FALLBACK_CREDENTIALS: bool = True
'''

_RH1_VALIDATOR_ANCHOR = """    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )
"""

_RH1_VALIDATOR = '''
    # ======================================================================
    # RH-1 — storage credential resolution.
    # ======================================================================

    _DEV_S3_ACCESS_KEY_ID = "minioadmin"
    _DEV_S3_SECRET_ACCESS_KEY = "minioadmin"
    _DEV_S3_ENDPOINT_URL = "http://localhost:9000"
    _DEV_S3_BUCKET = "flowpilot-dev"

    @model_validator(mode="after")
    def _resolve_storage_credentials(self) -> "Settings":
        """Fill dev storage defaults, or refuse to boot in production.

        The two failure modes this closes are asymmetric and are handled
        asymmetrically on purpose.

        Development: a founder running `uvicorn app.main:app` on the host
        against docker-compose MinIO has no AWS_* in the shell. Silently
        deferring to botocore's ambient chain turns that into a
        NoCredentialsError at upload time, several layers below the cause.
        Defaulting to the compose credentials is correct here because those
        credentials are already the documented, committed, non-secret
        defaults in docker-compose.yml.

        Production: defaulting would be a security defect. An operator who
        forgot S3_SECRET_ACCESS_KEY must get a boot failure naming the
        variable, not a running process that writes tenant documents into
        whatever bucket an instance role happens to reach.
        """
        backend = (self.STORAGE_BACKEND or "").strip().lower()
        if backend not in {"s3", "r2", "minio"}:
            return self

        environment = (self.ENVIRONMENT or "").strip().lower()
        is_dev = environment in {"development", "dev", "test", "testing", "local"}
        has_key = bool(self.S3_ACCESS_KEY_ID)
        has_secret = bool(
            self.S3_SECRET_ACCESS_KEY
            and self.S3_SECRET_ACCESS_KEY.get_secret_value().strip()
        )

        if has_key and has_secret:
            return self

        if is_dev and self.S3_DEV_FALLBACK_CREDENTIALS:
            if not has_key:
                object.__setattr__(
                    self, "S3_ACCESS_KEY_ID", self._DEV_S3_ACCESS_KEY_ID
                )
            if not has_secret:
                object.__setattr__(
                    self,
                    "S3_SECRET_ACCESS_KEY",
                    SecretStr(self._DEV_S3_SECRET_ACCESS_KEY),
                )
            if not self.S3_ENDPOINT_URL:
                object.__setattr__(
                    self, "S3_ENDPOINT_URL", self._DEV_S3_ENDPOINT_URL
                )
            if not self.S3_BUCKET:
                object.__setattr__(self, "S3_BUCKET", self._DEV_S3_BUCKET)
            warnings.warn(
                "STORAGE_BACKEND=%s with no S3_ACCESS_KEY_ID/"
                "S3_SECRET_ACCESS_KEY. Falling back to the local MinIO "
                "development credentials from docker-compose.yml. This "
                "fallback is refused when ENVIRONMENT=production."
                % backend,
                RuntimeWarning,
                stacklevel=2,
            )
            return self

        missing = []
        if not has_key:
            missing.append("S3_ACCESS_KEY_ID (or AWS_ACCESS_KEY_ID)")
        if not has_secret:
            missing.append("S3_SECRET_ACCESS_KEY (or AWS_SECRET_ACCESS_KEY)")
        raise ValueError(
            f"STORAGE_BACKEND={backend!r} requires explicit object-storage "
            f"credentials. Missing: {', '.join(missing)}. Refusing to fall "
            "back to botocore's ambient credential chain, which would let a "
            "misconfigured host write tenant documents to an unintended "
            "bucket under an instance role."
        )

    @property
    def s3_credentials(self) -> dict[str, Optional[str]]:
        """The kwargs boto3.client() needs. Empty values become None."""
        secret = (
            self.S3_SECRET_ACCESS_KEY.get_secret_value()
            if self.S3_SECRET_ACCESS_KEY
            else None
        )
        token = (
            self.S3_SESSION_TOKEN.get_secret_value()
            if self.S3_SESSION_TOKEN
            else None
        )
        return {
            "aws_access_key_id": self.S3_ACCESS_KEY_ID or None,
            "aws_secret_access_key": secret or None,
            "aws_session_token": token or None,
        }

'''


def _rh1(source: str) -> str:
    source = _replace_once(
        source, _RH1_IMPORT_ANCHOR, _RH1_IMPORT_NEW, ident="RH-1 import"
    )
    source = _insert_after(
        source, _RH1_FIELD_ANCHOR, _RH1_FIELDS, ident="RH-1 fields"
    )
    idx = source.find(_RH1_VALIDATOR_ANCHOR)
    if idx == -1:
        raise AnchorMiss("RH-1 validator: model_config anchor not found")
    return source[:idx] + _RH1_VALIDATOR + source[idx:]


# ---------------------------------------------------------------------------
# RH-2 — app/core/storage/s3.py
# ---------------------------------------------------------------------------

_RH2_SENTINEL = "_resolved_credentials"

_RH2_SIG_OLD = """        addressing_style: Optional[str] = None,
        client: Any = None,
    ) -> None:"""

_RH2_SIG_NEW = '''        addressing_style: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        session_token: Optional[str] = None,
        client: Any = None,
    ) -> None:'''

_RH2_CLIENT_OLD = """        self._client = boto3.client(
            "s3",
            region_name="auto" if self._flavor == "r2" else region,
            endpoint_url=endpoint_url,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                max_pool_connections=max_pool_connections,
                s3=s3_options or None,
                signature_version="s3v4",
            ),
        )"""

_RH2_CLIENT_NEW = '''        credentials = self._resolved_credentials(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

        self._client = boto3.client(
            "s3",
            region_name="auto" if self._flavor == "r2" else region,
            endpoint_url=endpoint_url,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                max_pool_connections=max_pool_connections,
                s3=s3_options or None,
                signature_version="s3v4",
            ),
            **credentials,
        )

    @staticmethod
    def _resolved_credentials(
        *,
        access_key_id: Optional[str],
        secret_access_key: Optional[str],
        session_token: Optional[str],
    ) -> dict[str, str]:
        """Explicit credentials, falling back to Settings, never to ambient.

        RH-2. This constructor previously called boto3.client() with no
        credential kwargs at all, so botocore walked its ambient chain:
        os.environ, ~/.aws/credentials, then IMDS. On a developer host with
        none of those, the first PutObject raised NoCredentialsError from
        four frames below document_intake_service, which made the failure
        read as a storage bug rather than a configuration one.

        Settings is imported lazily. app.core.storage.s3 is imported by
        app.core.storage.__init__, which app.core.config must not depend on;
        a module-level import here would close that cycle.

        An explicitly passed credential always wins, which is what keeps the
        ARCH-26 warehouse connectors and every test double able to inject
        their own without touching global settings.
        """
        if access_key_id and secret_access_key:
            resolved = {
                "aws_access_key_id": access_key_id,
                "aws_secret_access_key": secret_access_key,
            }
            if session_token:
                resolved["aws_session_token"] = session_token
            return resolved

        from app.core.config import settings

        from_settings = settings.s3_credentials
        return {
            key: value for key, value in from_settings.items() if value
        }'''


def _rh2(source: str) -> str:
    source = _replace_once(source, _RH2_SIG_OLD, _RH2_SIG_NEW, ident="RH-2 signature")
    return _replace_once(
        source, _RH2_CLIENT_OLD, _RH2_CLIENT_NEW, ident="RH-2 boto3 client"
    )


# ---------------------------------------------------------------------------
# RH-3 — app/worker.py
# ---------------------------------------------------------------------------

_RH3_SENTINEL = "from app.workers.supervisor import"

_RH3_CHOICES_OLD = '''    parser.add_argument(
        "--loop",
        choices=["relay", "delivery", "jobs", "stripe"],
        required=True,
        help="relay | delivery | jobs | stripe",
    )'''

_RH3_CHOICES_NEW = '''    parser.add_argument(
        "--loop",
        choices=["relay", "delivery", "jobs", "stripe", "scheduler", "all"],
        required=True,
        help=(
            "relay | delivery | jobs | stripe | scheduler | all. "
            "'all' runs every loop plus the scheduler in one supervised "
            "process, which is the single-node and local-development shape. "
            "'scheduler' runs only the recurring-job producer."
        ),
    )
    parser.add_argument(
        "--no-supervise",
        action="store_true",
        help=(
            "Run the loop unsupervised, exactly as before RH-3: any exception "
            "escaping the loop body terminates the process. Container "
            "orchestrators with their own restart policy may prefer this."
        ),
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=0,
        help="0 means unlimited. Supervised mode only.",
    )'''

_RH3_DISPATCH_OLD = """    shutdown = GracefulShutdown().install()
    kwargs = dict(
        shutdown=shutdown,
        batch_size=args.batch_size,
        lease_seconds=lease,
        idle_sleep_seconds=args.idle_sleep,
    )
    if args.loop == "relay":
        runner, extra = run_relay_loop, {"per_org_cap": args.per_org_cap}
    elif args.loop == "delivery":
        runner, extra = run_delivery_loop, {"per_org_cap": args.per_org_cap}
    elif args.loop == "stripe":
        runner, extra = run_stripe_inbound_loop, {}
    else:
        runner, extra = run_jobs_loop, {"job_types": claimable_job_types(profile)}

    try:
        runner(**kwargs, **extra)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0"""

_RH3_DISPATCH_NEW = '''    shutdown = GracefulShutdown().install()
    kwargs = dict(
        shutdown=shutdown,
        batch_size=args.batch_size,
        lease_seconds=lease,
        idle_sleep_seconds=args.idle_sleep,
    )

    from app.workers.supervisor import LoopSpec, run_supervised

    def _spec(name: str) -> LoopSpec:
        if name == "relay":
            return LoopSpec(
                name="relay",
                runner=run_relay_loop,
                kwargs={**kwargs, "per_org_cap": args.per_org_cap},
            )
        if name == "delivery":
            return LoopSpec(
                name="delivery",
                runner=run_delivery_loop,
                kwargs={**kwargs, "per_org_cap": args.per_org_cap},
            )
        if name == "stripe":
            return LoopSpec(name="stripe", runner=run_stripe_inbound_loop, kwargs=dict(kwargs))
        if name == "scheduler":
            from app.workers.scheduler import run_scheduler_loop

            return LoopSpec(
                name="scheduler",
                runner=run_scheduler_loop,
                kwargs={
                    "shutdown": shutdown,
                    "tick_seconds": float(
                        getattr(settings, "SCHEDULER_TICK_SECONDS", 30.0)
                    ),
                },
            )
        return LoopSpec(
            name="jobs",
            runner=run_jobs_loop,
            kwargs={**kwargs, "job_types": claimable_job_types(profile)},
        )

    if args.loop == "all":
        names = ["jobs", "relay", "delivery", "stripe", "scheduler"]
    else:
        names = [args.loop]

    specs = [_spec(name) for name in names]

    if args.no_supervise:
        if len(specs) != 1:
            parser.error("--no-supervise is incompatible with --loop all")
        spec = specs[0]
        try:
            spec.runner(**spec.kwargs)
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0

    try:
        return run_supervised(
            specs,
            shutdown=shutdown,
            max_restarts=args.max_restarts,
        )
    except SystemExit as exc:
        return int(exc.code or 0)'''


def _rh3(source: str) -> str:
    source = _replace_once(
        source, _RH3_CHOICES_OLD, _RH3_CHOICES_NEW, ident="RH-3 argparse choices"
    )
    return _replace_once(
        source, _RH3_DISPATCH_OLD, _RH3_DISPATCH_NEW, ident="RH-3 dispatch"
    )


# ---------------------------------------------------------------------------
# RH-4 — sweeper dispatch
# ---------------------------------------------------------------------------

_RH4_SENTINEL = "sweep_compliance.py"

_RH4_CASE_OLD = """    arch09)      SCRIPT="scripts/sweep_arch09.py"      ; HEARTBEAT="${HEARTBEAT_UUID_ARCH09:-}"      ;;"""

_RH4_CASE_NEW = """    arch09)      SCRIPT="scripts/sweep_arch09.py"      ; HEARTBEAT="${HEARTBEAT_UUID_ARCH09:-}"      ;;
    compliance)  SCRIPT="scripts/sweep_compliance.py"  ; HEARTBEAT="${HEARTBEAT_UUID_COMPLIANCE:-}" ;;"""

_RH4_USAGE_OLD = (
    '    echo "usage: $(basename "$0") <arch07|identity|invitations|arch09> [args...]" >&2'
)
_RH4_USAGE_NEW = (
    '    echo "usage: $(basename "$0") '
    "<arch07|identity|invitations|arch09|compliance> [args...]\" >&2"
)


def _rh4_dispatcher(source: str) -> str:
    source = _replace_once(
        source, _RH4_USAGE_OLD, _RH4_USAGE_NEW, ident="RH-4 usage line"
    )
    return _replace_once(
        source, _RH4_CASE_OLD, _RH4_CASE_NEW, ident="RH-4 case arm"
    )


_RH4_CRON_ANCHOR = (
    "41 3 * * *  flowpilot  /srv/flowpilot/backend/deploy/bin/flowpilot-sweep "
    "arch09 --all --apply\n"
)

_RH4_CRON_NEW = (
    "# RH-4. scripts/sweep_compliance.py has existed since ARCH-20 and was\n"
    "# dispatched by nothing: not by this file, not by the flowpilot-sweep\n"
    "# case statement, not by any job handler. Retention and erasure\n"
    "# enforcement has therefore never executed on a deployed host.\n"
    "53 3 * * *  flowpilot  /srv/flowpilot/backend/deploy/bin/flowpilot-sweep "
    "compliance --apply\n"
)


def _rh4_cron(source: str) -> str:
    return _insert_after(
        source, _RH4_CRON_ANCHOR, _RH4_CRON_NEW, ident="RH-4 cron entry"
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

EDITS: list[Edit] = [
    Edit("RH-1", BACKEND / "app" / "core" / "config.py", _RH1_SENTINEL, _rh1),
    Edit("RH-2", BACKEND / "app" / "core" / "storage" / "s3.py", _RH2_SENTINEL, _rh2),
    Edit("RH-3", BACKEND / "app" / "worker.py", _RH3_SENTINEL, _rh3),
    Edit(
        "RH-4a",
        BACKEND / "deploy" / "bin" / "flowpilot-sweep",
        _RH4_SENTINEL,
        _rh4_dispatcher,
    ),
    Edit(
        "RH-4b",
        BACKEND / "deploy" / "cron.d" / "flowpilot-sweepers",
        "flowpilot-sweep compliance",
        _rh4_cron,
    ),
]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="patch_runtime_hardening")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    pending = 0
    failures = 0

    for edit in EDITS:
        if not edit.path.exists():
            print(f"[{edit.ident}] MISSING FILE: {edit.path}", file=sys.stderr)
            failures += 1
            continue

        source = edit.path.read_text(encoding="utf-8")

        if edit.sentinel in source:
            print(f"[{edit.ident}] already applied: {edit.path.name}")
            continue

        pending += 1
        if args.check:
            print(f"[{edit.ident}] NOT APPLIED: {edit.path.name}", file=sys.stderr)
            continue

        try:
            patched = edit.apply(source)
        except AnchorMiss as exc:
            print(f"[{edit.ident}] ANCHOR MISS -> {exc}", file=sys.stderr)
            failures += 1
            continue

        if edit.sentinel not in patched:
            print(
                f"[{edit.ident}] post-condition failed: sentinel "
                f"{edit.sentinel!r} absent after patch",
                file=sys.stderr,
            )
            failures += 1
            continue

        edit.path.write_text(patched, encoding="utf-8")
        print(f"[{edit.ident}] applied: {edit.path.name}")

    if failures:
        print(f"\n{failures} edit(s) failed. Nothing partially written.", file=sys.stderr)
        return 1
    if args.check and pending:
        print(f"\n{pending} edit(s) pending.", file=sys.stderr)
        return 1

    print("\nAll runtime hardening edits are in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())