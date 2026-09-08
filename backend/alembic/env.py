import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_PARTITION_TABLE_PREFIX = "document_chunks_p"
UNMANAGED_TABLES: frozenset[str] = frozenset({"billable_seats"})


def include_object(obj, name, type_, reflected, compare_to):
    if name == "billable_seats":
        return False
    if name and name.startswith(_PARTITION_TABLE_PREFIX):
        return False
    if type_ == "table" and reflected and compare_to is None:
        if name in UNMANAGED_TABLES:
            return False
    return True


def _is_comment_only_alter(op_) -> bool:
    from alembic.operations import ops as alembic_ops

    if not isinstance(op_, alembic_ops.AlterColumnOp):
        return False
    if op_.modify_type is not None:
        return False
    if op_.modify_nullable is not None:
        return False
    if op_.modify_name is not None:
        return False
    if op_.modify_server_default not in (False, None):
        return False
    return True


def _strip_comment_only_ops(directives) -> None:
    from alembic.operations import ops as alembic_ops

    comment_op_names = (
        "CreateTableCommentOp",
        "DropTableCommentOp",
    )
    comment_ops = tuple(
        getattr(alembic_ops, attr)
        for attr in comment_op_names
        if hasattr(alembic_ops, attr)
    )

    for directive in directives:
        for upgrade_ops in getattr(directive, "upgrade_ops_list", []) or [
            getattr(directive, "upgrade_ops", None)
        ]:
            if upgrade_ops is None:
                continue
            surviving = []
            for op_ in upgrade_ops.ops:
                if comment_ops and isinstance(op_, comment_ops):
                    continue
                if isinstance(op_, alembic_ops.AlterColumnOp) and _is_comment_only_alter(op_):
                    continue
                if isinstance(op_, alembic_ops.ModifyTableOps):
                    inner = [
                        sub
                        for sub in op_.ops
                        if not (comment_ops and isinstance(sub, comment_ops))
                        and not _is_comment_only_alter(sub)
                    ]
                    if not inner:
                        continue
                    op_.ops = inner
                surviving.append(op_)
            upgrade_ops.ops = surviving


def process_revision_directives(context_, revision, directives) -> None:
    _strip_comment_only_ops(directives)


def _get_target_url() -> str:
    ini_url = config.get_main_option("sqlalchemy.url")
    # If ini_url is set by test suite (postgresql://...flowpilot_test), use it
    if ini_url and not ini_url.startswith("driver://") and ini_url.strip() != "":
        return ini_url
    return str(settings.SQLALCHEMY_DATABASE_URI if hasattr(settings, "SQLALCHEMY_DATABASE_URI") else settings.sqlalchemy_database_uri)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _get_target_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        process_revision_directives=process_revision_directives,
        compare_type=True,
        compare_comments=False,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _get_target_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            process_revision_directives=process_revision_directives,
            compare_type=True,
            compare_comments=False,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
