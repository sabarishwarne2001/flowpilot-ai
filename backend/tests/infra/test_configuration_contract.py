"""Campaign session 4 (§C.5, F-011) — one contract for every backend setting.

    pytest tests/infra/test_configuration_contract.py -q

A setting is a field of `app.core.config.Settings`: an environment variable of the same
name that the API, the workers, alembic and the scripts all read at start-up. For every
one of them this module checks, without a database:

(a) code reads it. A setting nothing reads is a promise the product does not keep: an
    operator changes it, restarts, and nothing happens. Reads are found in the source
    (`settings.NAME`, `app_settings.NAME`, `getattr(settings, "NAME", ...)`,
    `os.environ.get("NAME")`, a property or start-up check of `Settings` that is itself
    used), plus the two short, justified lists below for reads Python cannot see;
(b) `docs/CONFIGURATION.md` names it, and names nothing that is not a setting;
(c) the templates carry what a deployment must set: `.env.production.template` every
    setting production cannot run without, `.env.example` what a developer needs;
(d) a production start with a required secret missing is REFUSED, and the refusal names
    the variable (built exactly as the API and workers build it at import time);

and the reverse: every variable a template names is a setting, or one of the few
variables read by something else (Docker Compose, Caddy, alembic, a one-off script),
listed here with its reader so the list cannot quietly go stale.
"""

from __future__ import annotations

import ast
import base64
import functools
import re
import secrets
from pathlib import Path

import pytest
from pydantic import AliasChoices, ValidationError

from app.core.config import Settings
from app.core.production_guard import UnsafeConfigurationError

pytestmark = pytest.mark.no_db

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
CONFIG = BACKEND / "app" / "core" / "config.py"
DOC = REPO / "docs" / "CONFIGURATION.md"
EXAMPLE = BACKEND / ".env.example"
PRODUCTION = BACKEND / ".env.production.template"
FRONTEND_EXAMPLE = REPO / "frontend" / ".env.example"

FIELDS: tuple[str, ...] = tuple(Settings.model_fields)

#: The Python the deployment runs: the application, its scripts (host cron and one-off
#: commands), the migrations and the evaluation harness. Tests do not count as a reader.
CODE_ROOTS = ("app", "scripts", "alembic", "evaluation", "perf")
#: Historical one-off scripts (CLAUDE.md): never a reader of live configuration.
HISTORICAL = re.compile(r"^(apply_|verify_|arch07_|arch08_)")

# ---------------------------------------------------------------------------------------
# The two allow-lists. Keep them tiny; every entry says who reads the setting and is
# checked below, so an entry whose reader disappears fails the test.
# ---------------------------------------------------------------------------------------

#: Modules that look a setting up by a NAME HELD IN A VARIABLE (`getattr(settings, name)`),
#: so the names they hold as plain strings are reads.
DYNAMIC_LOOKUP_MODULES: dict[str, str] = {
    # The protection switches are checked in a loop: `for name in (...): getattr(settings, name)`.
    "app/core/production_guard.py": "getattr(settings, name)",
    # The live gateway's key setting is chosen by gateway name.
    "app/services/billing/portal_service.py": "getattr(settings, key_setting, None)",
    # The webhook body limit is chosen by gateway name.
    "app/api/v1/billing_webhook_multi.py": "getattr(settings, attr, 512 * 1024)",
    # Each provider names its platform key (`platform_setting=`); provider_clients.platform_key
    # reads it with getattr(settings, attribute, None).
    "app/core/byok_providers.py": "platform_setting=",
    # Each add-on names its Dodo product setting (`product_setting=`); the entitlement, add-on
    # and reconciliation services read it with getattr(settings, offer.product_setting).
    "app/services/billing/entitlement_service.py": "product_setting=",
}

#: Settings read by something that is not Python, so (a) cannot see it in the source.
NON_PYTHON_READERS: dict[str, tuple[str, str]] = {
    # Caddy asks Let's Encrypt for certificates under this address; docker-compose.prod.yml
    # hands it to the caddy service (and refuses to start without it).
    "ACME_CONTACT_EMAIL": ("backend/deploy/Caddyfile", "{$ACME_CONTACT_EMAIL}"),
    # Declared only to be checked: the Literal type refuses any value but signxml at start-up
    # (python3-saml is not installed, ARCH-28). test_saml_crypto_backend_refuses_python3_saml.
    "SAML_CRYPTO_BACKEND": ("backend/app/core/config.py", 'SAML_CRYPTO_BACKEND: Literal["signxml"]'),
}

#: Variables the templates name that are NOT settings, with the file that reads each.
NOT_SETTINGS: dict[str, tuple[str, str]] = {
    # Docker Compose and Caddy
    "IMAGE_TAG": ("backend/docker-compose.prod.yml", "${IMAGE_TAG"),
    "APP_DOMAIN": ("backend/deploy/Caddyfile", "{$APP_DOMAIN}"),
    "REDIS_PASSWORD": ("backend/docker-compose.prod.yml", "${REDIS_PASSWORD"),
    # Read straight from the process environment
    "SERVICE_ROLE": ("backend/app/db/session.py", "SERVICE_ROLE"),
    "ARCH40_CONTRACT": ("backend/alembic/versions/arch40_step3_contract_ai_settings.py", "ARCH40_CONTRACT"),
    # One-off and host commands
    "GATEWAY_PRICE_ID_DEVELOPER": ("backend/scripts/seed_quota_tiers.py", "GATEWAY_PRICE_ID_DEVELOPER"),
    "GATEWAY_PRICE_ID_BUSINESS": ("backend/scripts/seed_quota_tiers.py", "GATEWAY_PRICE_ID_BUSINESS"),
    "GATEWAY_PRICE_ID_ENTERPRISE": ("backend/scripts/seed_quota_tiers.py", "GATEWAY_PRICE_ID_ENTERPRISE"),
    "SEED_ADMIN_EMAIL": ("backend/scripts/seed_admin.py", "SEED_ADMIN_EMAIL"),
    "SEED_ADMIN_PASSWORD": ("backend/scripts/seed_admin.py", "SEED_ADMIN_PASSWORD"),
    "FLOWPILOT_BACKUP_KEY": ("backend/scripts/backup_floor.py", "FLOWPILOT_BACKUP_KEY"),
}

# ---------------------------------------------------------------------------------------
# What a deployment must set
# ---------------------------------------------------------------------------------------

_STRIPE_TEST_KEY = "sk_test_" + "a1B2c3D4" * 4
_DODO_KEY = "dodo_" + "a1B2c3D4" * 4

#: Production cannot run without these: the start-up guard refuses to start when one is
#: missing. Value: what else the deployment must have set for the setting to be needed.
REQUIRED_IN_PRODUCTION: dict[str, dict[str, str]] = {
    "JWT_SECRET_KEY": {},
    "API_KEY_PEPPER": {},
    "REDIS_IDENTITY_PEPPER": {},
    "EMAIL_ENCRYPTION_KEYS": {},
    "POSTGRES_PASSWORD": {},
    "CORS_ORIGINS": {},
    "FRONTEND_URL": {},
    "REDIS_URL": {},
    "PLATFORM_SMTP_HOST": {},
    "OPERATOR_ALERT_EMAILS": {},
    # The platform serves AI on its own Groq key (N-054): every document is read with it.
    "GROQ_API_KEY": {},
    "RERANKER_INTERNAL_TOKEN": {"RERANKER_ENABLED": "true"},
    "S3_BUCKET": {"STORAGE_BACKEND": "s3"},
    "S3_ACCESS_KEY_ID": {"STORAGE_BACKEND": "s3"},
    "S3_SECRET_ACCESS_KEY": {"STORAGE_BACKEND": "s3"},
    "STRIPE_WEBHOOK_SECRETS": {"STRIPE_SECRET_KEY": _STRIPE_TEST_KEY},
    "DODO_WEBHOOK_SECRET": {"BILLING_GATEWAY": "DODO", "DODO_API_KEY": _DODO_KEY},
}

#: Further settings a production deployment sets for itself (its own addresses, its payment
#: and mail accounts, the proxy in front of it); their defaults are for development.
PRODUCTION_SETS_ITSELF = {
    "ENVIRONMENT", "LOG_LEVEL", "POSTGRES_USER", "POSTGRES_DB", "STORAGE_BACKEND", "S3_REGION",
    "S3_ENDPOINT_URL", "ACME_CONTACT_EMAIL", "PUBLIC_API_URL", "PLATFORM_RESERVED_HOSTS",
    "TRUSTED_PROXY_HOPS", "TRUSTED_PROXY_HOPS_CONFIRMED", "RERANKER_ENABLED", "BILLING_GATEWAY",
    "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_LIVEMODE", "DODO_API_KEY",
    "DODO_LIVEMODE", "PLATFORM_SMTP_PORT", "PLATFORM_SMTP_USERNAME", "PLATFORM_SMTP_PASSWORD",
    "PLATFORM_SMTP_ENCRYPTION", "PLATFORM_SMTP_FROM_EMAIL", "PLATFORM_SMTP_FROM_NAME",
}

#: What a developer sets to run the product on a laptop: the one secret with no default, the
#: local services' addresses and credentials, and the keys of outside services they try.
DEVELOPER_SETS = {
    "ENVIRONMENT", "JWT_SECRET_KEY", "API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER",
    "EMAIL_ENCRYPTION_KEYS", "FRONTEND_URL", "CORS_ORIGINS", "POSTGRES_HOST", "POSTGRES_PORT",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "REDIS_URL", "STORAGE_BACKEND",
    "S3_BUCKET", "S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
    "PLATFORM_SMTP_HOST", "PLATFORM_SMTP_PORT", "PLATFORM_SMTP_USERNAME",
    "PLATFORM_SMTP_PASSWORD", "PLATFORM_SMTP_ENCRYPTION", "PLATFORM_SMTP_FROM_EMAIL",
    "GROQ_API_KEY", "GEMINI_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
    "STRIPE_WEBHOOK_SECRETS", "BILLING_GATEWAY", "DODO_API_KEY", "DODO_WEBHOOK_SECRET",
    "RERANKER_ENABLED", "RERANKER_URL", "RERANKER_INTERNAL_TOKEN", "OPERATOR_ALERT_EMAILS",
}


# ---------------------------------------------------------------------------------------
# Reading the source
# ---------------------------------------------------------------------------------------

#: `settings`, `app_settings`, `self.settings`, `get_settings()`, `_settings()`.
_SETTINGS_RECEIVER = re.compile(r"(?:^|[._])(?:get_)?settings(?:\(\))?$")


def _code_files() -> list[Path]:
    files: list[Path] = []
    for root in CODE_ROOTS:
        for path in sorted((BACKEND / root).rglob("*.py")):
            if "__pycache__" in path.parts or HISTORICAL.match(path.name):
                continue
            files.append(path)
    return files


def _is_settings(node: ast.AST) -> bool:
    return bool(_SETTINGS_RECEIVER.search(ast.unparse(node)))


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_environ(node: ast.AST) -> bool:
    return ast.unparse(node) in {"os.environ", "environ"}


def _reads_in(tree: ast.AST, *, dynamic: bool) -> set[str]:
    """Setting names this module reads."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and _is_settings(node.value):
            names.add(node.attr)
        elif isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            args = node.args
            if func in {"getattr", "hasattr"} and len(args) >= 2:
                # Any receiver: an exact setting name looked up by getattr is a setting read
                # (egress.py names its settings object `s`).
                names.add(_string(args[1]) or "")
            elif func in {"os.environ.get", "os.getenv", "environ.get", "getenv"} and args:
                names.add(_string(args[0]) or "")
        elif isinstance(node, ast.Subscript) and _is_environ(node.value):
            names.add(_string(node.slice) or "")
        elif dynamic and _string(node):
            names.add(_string(node) or "")
    return names


def _settings_class() -> ast.ClassDef:
    tree = ast.parse(CONFIG.read_text(encoding="utf-8-sig"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")


def _self_reads(function: ast.AST) -> set[str]:
    return {
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and ast.unparse(node.value) == "self"
    }


def _decorators(function: ast.FunctionDef) -> set[str]:
    return {ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in function.decorator_list}


@functools.lru_cache(maxsize=1)
def code_reads() -> dict[str, set[str]]:
    """Setting name -> the files (relative to the repository) that read it."""
    readers: dict[str, set[str]] = {}
    attributes_used: set[str] = set()
    for path in _code_files():
        if path == CONFIG:
            continue
        relative = path.relative_to(BACKEND).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for name in _reads_in(tree, dynamic=relative in DYNAMIC_LOOKUP_MODULES):
            readers.setdefault(name, set()).add(f"backend/{relative}")
        attributes_used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    # Inside Settings itself: a start-up check (model validator) reads what it checks, and a
    # property or method reads a setting on behalf of whoever uses that property.
    methods = {
        item.name: item
        for item in _settings_class().body
        if isinstance(item, ast.FunctionDef)
    }
    live = {name for name, fn in methods.items() if "model_validator" in _decorators(fn) or name in attributes_used}
    while True:
        more = {called for name in live for called in _self_reads(methods[name]) if called in methods} - live
        if not more:
            break
        live |= more
    for name in live:
        for attribute in _self_reads(methods[name]):
            readers.setdefault(attribute, set()).add(f"backend/app/core/config.py:Settings.{name}")
    return readers


def _template_names(path: Path, *, commented: bool = True) -> set[str]:
    """`NAME=value` lines, and with `commented` the `# NAME=value` examples too."""
    pattern = r"^\s*#?\s*([A-Z][A-Z0-9_]*)=" if commented else r"^([A-Z][A-Z0-9_]*)="
    return {
        m.group(1)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if (m := re.match(pattern, line))
    }


def _documented() -> set[str]:
    return set(re.findall(r"^\| `([A-Z][A-Z0-9_]*)` \|", DOC.read_text(encoding="utf-8"), re.M))


def _aliases() -> set[str]:
    names: set[str] = set()
    for field in Settings.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            names |= {choice for choice in alias.choices if isinstance(choice, str)}
        elif isinstance(alias, str):
            names.add(alias)
    return names


# ---------------------------------------------------------------------------------------
# (a) read by code
# ---------------------------------------------------------------------------------------


def test_every_setting_is_read_by_code() -> None:
    readers = code_reads()
    unread = sorted(name for name in FIELDS if name not in readers and name not in NON_PYTHON_READERS)
    assert not unread, (
        "settings nothing reads (an operator could set them and nothing would change); "
        f"wire each one up or remove it: {unread}"
    )


@pytest.mark.parametrize("name", sorted(NON_PYTHON_READERS))
def test_each_non_python_reader_really_reads_its_setting(name: str) -> None:
    path, marker = NON_PYTHON_READERS[name]
    assert name in FIELDS, f"{name} is no longer a setting; drop it from NON_PYTHON_READERS"
    assert marker in (REPO / path).read_text(encoding="utf-8"), f"{path} no longer reads {name}"


@pytest.mark.parametrize("module", sorted(DYNAMIC_LOOKUP_MODULES))
def test_each_dynamic_lookup_module_still_looks_settings_up_by_name(module: str) -> None:
    assert DYNAMIC_LOOKUP_MODULES[module] in (BACKEND / module).read_text(encoding="utf-8")


def test_the_reader_scan_finds_reads_it_should() -> None:
    """Control: each kind of read the scan claims to recognise is recognised."""
    readers = code_reads()
    assert any("app/core/security.py" in r for r in readers["JWT_SECRET_KEY"])  # settings.NAME
    assert "backend/app/services/notification/outbox_dispatcher.py" in readers["PROJECT_NAME"]  # app_settings.NAME
    assert any("production_guard" in r for r in readers["SAML_XSW_DEFENCE_ENABLED"])  # by a name held in a variable
    assert any("domain_service" in r for r in readers["ACME_AGENT_URL"])  # getattr(settings, "NAME", ...)
    assert any("Settings.s3_credentials" in r for r in readers["S3_SESSION_TOKEN"])  # through a used property
    assert any("Settings._resolve_storage_credentials" in r for r in readers["S3_DEV_FALLBACK_CREDENTIALS"])
    # `egress.LLM_PROVIDER` is the name of an outbound channel, not a read of a setting.
    assert "backend/app/core/egress.py" not in readers.get("LLM_PROVIDER", set())


def test_saml_crypto_backend_refuses_python3_saml(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one setting kept only for its start-up check (NON_PYTHON_READERS)."""
    monkeypatch.setenv("SAML_CRYPTO_BACKEND", "python3-saml")
    with pytest.raises(ValidationError, match="SAML_CRYPTO_BACKEND"):
        Settings(_env_file=None)


# ---------------------------------------------------------------------------------------
# (b) documented
# ---------------------------------------------------------------------------------------


def test_every_setting_is_documented_in_configuration_md() -> None:
    missing = sorted(set(FIELDS) - _documented())
    assert not missing, f"docs/CONFIGURATION.md does not describe: {missing} (run docs/hardening/tools/config_reference.py)"


def test_configuration_md_describes_no_setting_that_does_not_exist() -> None:
    stale = sorted(_documented() - set(FIELDS))
    assert not stale, f"docs/CONFIGURATION.md describes settings that no longer exist: {stale}"


def test_configuration_md_counts_the_settings_it_lists() -> None:
    match = re.search(r"^(\d+) settings\.", DOC.read_text(encoding="utf-8"), re.M)
    assert match and int(match.group(1)) == len(FIELDS)


# ---------------------------------------------------------------------------------------
# (c) in the templates where a deployment needs them
# ---------------------------------------------------------------------------------------


def test_the_production_template_asks_for_everything_production_needs() -> None:
    needed = set(REQUIRED_IN_PRODUCTION) | PRODUCTION_SETS_ITSELF
    missing = sorted(needed - _template_names(PRODUCTION, commented=False))
    assert not missing, f"backend/.env.production.template does not ask for: {missing}"


def test_the_development_example_has_what_a_developer_sets() -> None:
    missing = sorted(DEVELOPER_SETS - _template_names(EXAMPLE, commented=False))
    assert not missing, f"backend/.env.example does not list: {missing}"


def test_the_must_set_lists_name_real_settings() -> None:
    """Control: a renamed or removed setting cannot linger in the lists above."""
    listed = set(REQUIRED_IN_PRODUCTION) | PRODUCTION_SETS_ITSELF | DEVELOPER_SETS
    assert listed <= set(FIELDS), sorted(listed - set(FIELDS))


@pytest.mark.parametrize("template", [EXAMPLE, PRODUCTION], ids=["env.example", "env.production.template"])
def test_every_template_variable_is_a_setting_or_has_a_named_reader(template: Path) -> None:
    unknown = sorted(_template_names(template) - set(FIELDS) - _aliases() - set(NOT_SETTINGS))
    assert not unknown, (
        f"{template.name} names variables nothing reads (the app drops unknown names without a "
        f"word): {unknown}"
    )


@pytest.mark.parametrize("name", sorted(NOT_SETTINGS))
def test_each_non_setting_variable_has_its_reader(name: str) -> None:
    path, marker = NOT_SETTINGS[name]
    assert name not in FIELDS, f"{name} is a setting now; drop it from NOT_SETTINGS"
    assert marker in (REPO / path).read_text(encoding="utf-8"), f"{path} no longer reads {name}"
    assert name in _template_names(EXAMPLE) | _template_names(PRODUCTION), f"{name} is in no template"


def test_the_frontend_template_names_only_what_the_web_app_reads() -> None:
    """F-010: the web app reads VITE_API_URL alone; VITE_APP_NAME, VITE_APP_VERSION and
    VITE_ENVIRONMENT were listed and never read."""
    names = _template_names(FRONTEND_EXAMPLE)
    assert names == {"VITE_API_URL"}, names
    client = (REPO / "frontend" / "src" / "services" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "import.meta.env.VITE_API_URL" in client


# ---------------------------------------------------------------------------------------
# (d) production refuses to start without a required secret
# ---------------------------------------------------------------------------------------


def good_production_env() -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "API_KEY_PEPPER": secrets.token_hex(32),
        "REDIS_IDENTITY_PEPPER": secrets.token_hex(32),
        "EMAIL_ENCRYPTION_KEYS": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "POSTGRES_HOST": "db",
        "CORS_ORIGINS": "https://app.example.com",
        "FRONTEND_URL": "https://app.example.com",
        "STORAGE_BACKEND": "s3",
        "S3_BUCKET": "flowpilot-prod",
        "S3_ACCESS_KEY_ID": "AKIA" + secrets.token_hex(8).upper(),
        "S3_SECRET_ACCESS_KEY": secrets.token_urlsafe(30),
        "REDIS_URL": "redis://redis:6379/0",
        "PLATFORM_SMTP_HOST": "smtp.postmarkapp.com",
        "RERANKER_ENABLED": "true",
        "RERANKER_INTERNAL_TOKEN": secrets.token_hex(32),
        "OPERATOR_ALERT_EMAILS": "ops@example.com",
        "GROQ_API_KEY": "gsk_" + secrets.token_hex(24),
        "STRIPE_WEBHOOK_SECRETS": "whsec_" + secrets.token_hex(16),
        "DODO_WEBHOOK_SECRET": "whsec_" + secrets.token_hex(16),
    }


@pytest.fixture()
def boot(monkeypatch: pytest.MonkeyPatch):
    """Build Settings from exactly `values`, as the API and workers do at import time."""
    ambient = set(FIELDS) | _aliases() | {"EMAIL_ENCRYPTION_KEY", "SERVICE_ROLE"}
    for name in ambient:
        monkeypatch.delenv(name, raising=False)

    def _boot(values: dict[str, str]) -> Settings:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    return _boot


def test_a_complete_production_environment_starts(boot) -> None:
    """Control for the refusals below: they fail for the one missing setting alone."""
    values = good_production_env()
    for needs in REQUIRED_IN_PRODUCTION.values():
        values.update(needs)
    assert boot(values).ENVIRONMENT == "production"


@pytest.mark.parametrize("blank", [False, True], ids=["unset", "blank"])
@pytest.mark.parametrize("name", sorted(REQUIRED_IN_PRODUCTION))
def test_production_refuses_to_start_without_a_required_setting(boot, name: str, blank: bool) -> None:
    values = {**good_production_env(), **REQUIRED_IN_PRODUCTION[name]}
    values.pop(name, None)
    if blank:
        values[name] = ""
    with pytest.raises((UnsafeConfigurationError, ValidationError)) as refusal:
        boot(values)
    assert name in str(refusal.value), f"the refusal does not name {name}: {refusal.value}"
