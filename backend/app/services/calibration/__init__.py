"""ARCH-35 — calibrated autonomy and conformal risk control.

THIS FILE IMPORTS NOTHING, AND THAT IS LOAD-BEARING
===================================================

`app/services/assertions/calibration.py` is ARCH-33's pure calibration
contract, and ARCH-35 replaces the estimator behind it with
`app.services.calibration.estimators` and `.fit`. `verify_arch33.py` loads the
assertion engines through namespace stubs so that no heavy `__init__.py` runs;
when that loader resolves `app.services.calibration.estimators`, Python
executes THIS file first. An import here of anything that reaches SQLAlchemy,
settings or the declarative registry would make ARCH-33's offline gates need a
configured environment — the exact thing they exist to work without.

THE MODULES, AND WHICH ONES ARE PURE
====================================

Pure (standard library, NumPy, SciPy, scikit-learn; no Session, no clock, no
random source):

    vocabulary.py   closed enums and every tunable constant, copied into the
                    migration and gated for equality
    estimators.py   method selection, isotonic and Platt fits, evaluation
    fit.py          the held-out split, ECE and Brier, the refusal rule,
                    label -> example mapping
    risk.py         conformal threshold search, Clopper-Pearson, the
                    error-versus-coverage curve
    monitor.py      PSI, the realized-rate test, staleness
    sampling.py     the deterministic audit sampler
    decision.py     the per-score autonomy decision, given a model snapshot

Impure (they hold the Session):

    labels.py       harvesting reviewer outcomes into calibration_labels
    refit.py        persisting model versions, drift suspension, resume
    apply.py        `calibrated(org, decision_type, raw_score)` — the one
                    function every automatic decision calls
    overview.py     what the console reads
"""
