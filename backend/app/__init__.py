# Database Models Scaffold

# HARDENING-T1:D34. Installed at package import so no entry point can miss it.
from app.core.log_safety import install as _install_log_safety  # noqa: E402

_install_log_safety()

