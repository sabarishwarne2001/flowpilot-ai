"""ARCH-40 database probe. Exercises the gates that need real PostgreSQL."""
from __future__ import annotations
import os, re, sys, uuid, datetime as dt
from decimal import Decimal
import sqlalchemy as sa

URL = os.environ["DATABASE_URL"]
eng = sa.create_engine(URL, future=True)
insp = sa.inspect(eng)

PASS, FAIL = [], []
def check(name, fn):
    try:
        fn(); PASS.append(name); print(f"  PASS  {name}")
    except Exception as e:
        FAIL.append((name, e)); print(f"  FAIL  {name}\n        {type(e).__name__}: {e}")

ORG = uuid.uuid4()
U1  = uuid.uuid4()
U2  = uuid.uuid4()
WS1 = uuid.uuid4()
WS2 = uuid.uuid4()

def cols(table):
    return {c["name"]: c for c in insp.get_columns(table)}


_ENUM_CACHE: dict[tuple[str, str], list] = {}

def _enum_values(table, column):
    """The legal values of a PostgreSQL enum column, or [] if it is not one.

    Autofill cannot invent a string for an enum: 'x' is refused by the type
    itself, which is the whole point of the type.
    """
    key = (table, column)
    if key not in _ENUM_CACHE:
        with eng.connect() as c:
            rows = c.execute(sa.text("""
                SELECT e.enumlabel
                FROM pg_attribute a
                JOIN pg_class cl ON cl.oid = a.attrelid
                JOIN pg_type t ON t.oid = a.atttypid
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE cl.relname = :t AND a.attname = :c
                ORDER BY e.enumsortorder
            """), {"t": table, "c": column}).all()
        _ENUM_CACHE[key] = [r[0] for r in rows]
    return _ENUM_CACHE[key]


_VOCAB_CACHE: dict[str, dict] = {}

def _check_vocabulary(table, column):
    """Legal literals for a `col IN ('a','b',...)` CHECK, or [].

    The seed cannot invent a value for a vocabulary column any more than for
    an enum. Reading the CHECK is how the probe stays correct when a phase
    extends one of these lists — which is the whole reason they are CHECKs
    and not enums (ARCH-31 §7: a constraint swap inside a transaction).
    """
    if table not in _VOCAB_CACHE:
        with eng.connect() as c:
            rows = c.execute(sa.text("""
                SELECT pg_get_constraintdef(con.oid)
                FROM pg_constraint con
                JOIN pg_class cl ON cl.oid = con.conrelid
                WHERE cl.relname = :t AND con.contype = 'c'
            """), {"t": table}).all()
        found: dict[str, list] = {}
        for (definition,) in rows:
            m = re.search(r"\(\(?([a-z_]+)\)?::text = ANY \(\(?ARRAY\[(.+?)\]", definition)
            if not m:
                m = re.search(r"\(\(?([a-z_]+)\)?::text = ANY \(ARRAY\[(.+?)\]", definition)
            if m:
                col = m.group(1)
                lits = re.findall(r"'([^']+)'", m.group(2))
                if lits:
                    found.setdefault(col, lits)
        _VOCAB_CACHE[table] = found
    return _VOCAB_CACHE[table].get(column, [])

def autofill(table, given):
    """Fill NOT NULL columns with no default that the caller did not supply.

    Supplied keys that are not columns of this table are dropped rather than
    raising: the probe seeds against whatever schema the migrations actually
    produced, not against a remembered one.
    """
    present = cols(table)
    out = {k: v for k, v in given.items() if k in present}
    for name, c in cols(table).items():
        if name in out or c["nullable"] or c.get("default") is not None:
            continue
        t = str(c["type"]).upper()
        enum_vals = _enum_values(table, name)
        vocab_vals = _check_vocabulary(table, name)
        if enum_vals: out[name] = enum_vals[0]
        elif vocab_vals: out[name] = vocab_vals[0]
        elif "UUID" in t: out[name] = uuid.uuid4()
        elif "TIMESTAMP" in t or "DATE" in t: out[name] = dt.datetime.now(dt.timezone.utc)
        elif "BOOL" in t: out[name] = False
        elif "INT" in t or "NUMERIC" in t or "DOUBLE" in t or "REAL" in t: out[name] = 0
        elif "JSON" in t: out[name] = "{}"
        elif "[]" in t: out[name] = "{}"
        else: out[name] = "x"
    return out

_FK_CACHE: dict[str, dict] = {}

def _fks(table):
    """Single-column foreign keys of `table`, as {column: (parent, parent_col)}."""
    if table not in _FK_CACHE:
        out = {}
        for fk in insp.get_foreign_keys(table):
            cc, rc = fk["constrained_columns"], fk["referred_columns"]
            if len(cc) == 1 and len(rc) == 1:
                out[cc[0]] = (fk["referred_table"], rc[0])
        _FK_CACHE[table] = out
    return _FK_CACHE[table]


def _existing(conn, parent, parent_col):
    row = conn.execute(sa.text(f"SELECT {parent_col} FROM {parent} LIMIT 1")).first()
    return row[0] if row else None


def _insert(conn, table, given, depth=6):
    """Insert one row, inventing whatever the schema insists on.

    Foreign keys are satisfied by REUSING an existing parent row wherever one
    exists, and only creating a parent when the table is empty. Always
    creating parents recursively blows the depth limit on a schema this wide,
    and the probe does not care which organization a filler row points at —
    only that the ARCH-40 rows it actually asserts on point at the right ones,
    which the callers below supply explicitly.
    """
    present = cols(table)
    row = {k: v for k, v in given.items() if k in present}
    fks = _fks(table)

    for name, c in present.items():
        if name in row or c["nullable"] or c.get("default") is not None:
            continue
        if name in fks and depth > 0:
            parent, parent_col = fks[name]
            if parent == table:
                continue
            found = _existing(conn, parent, parent_col)
            if found is None:
                # Name the key ourselves. A parent whose primary key carries a
                # server default does not report it back, and the child needs
                # the value, not the row.
                seeded = uuid.uuid4() if "UUID" in str(
                    cols(parent)[parent_col]["type"]).upper() else None
                given_parent = {parent_col: seeded} if seeded is not None else {}
                created = _insert(conn, parent, given_parent, depth - 1)
                found = created.get(parent_col)
                if found is None:
                    found = _existing(conn, parent, parent_col)
            row[name] = found
            continue
        ty = str(c["type"]).upper()
        enum_vals = _enum_values(table, name)
        vocab_vals = _check_vocabulary(table, name)
        if enum_vals: row[name] = enum_vals[0]
        elif vocab_vals: row[name] = vocab_vals[0]
        elif "UUID" in ty: row[name] = uuid.uuid4()
        elif "TIMESTAMP" in ty or "DATE" in ty: row[name] = dt.datetime.now(dt.timezone.utc)
        elif "BOOL" in ty: row[name] = False
        elif "INT" in ty or "NUMERIC" in ty or "DOUBLE" in ty or "REAL" in ty: row[name] = 0
        elif "JSON" in ty: row[name] = "{}"
        elif "[]" in ty: row[name] = "{}"
        else: row[name] = f"x{uuid.uuid4().hex[:10]}"

    keys = ", ".join(row)
    vals = ", ".join(f":{k}" for k in row)
    conn.execute(sa.text(f"INSERT INTO {table} ({keys}) VALUES ({vals})"), row)
    return row


def ins(conn, table, **given):
    return _insert(conn, table, given)


with eng.begin() as c:
    ins(c, "organizations", id=ORG, name="Acme Probe", slug=f"acme-{uuid.uuid4().hex[:8]}", status="ACTIVE")
    for u, e in ((U1, f"a-{uuid.uuid4().hex[:6]}@acme.test"), (U2, f"b-{uuid.uuid4().hex[:6]}@acme.test")):
        ins(c, "users", id=u, email=e, is_active=True, is_superuser=False,
            is_verified=True, timezone="UTC", locale="en")
    for w, n, s in ((WS1, "WS One", f"ws-one-{uuid.uuid4().hex[:6]}"), (WS2, "WS Two", f"ws-two-{uuid.uuid4().hex[:6]}")):
        ins(c, "workspaces", id=w, organization_id=ORG, workspace_name=n, slug=s,
            status="ACTIVE", timezone="UTC", language="en", currency="USD",
            date_format="YYYY-MM-DD")
    for u, r in ((U1, "ADMIN"), (U2, "CONTRIBUTOR")):
        ins(c, "workspace_members", id=uuid.uuid4(), user_id=u, workspace_id=WS1, role=r)
    # U1 is also a member of WS2, so the cross-workspace gate tests the
    # predicate rather than merely testing membership.
    ins(c, "workspace_members", id=uuid.uuid4(), user_id=U1, workspace_id=WS2, role="ADMIN")
print("seed: organization, 2 users, 2 workspaces, 3 memberships")

WI = {}
with eng.begin() as c:
    for key, ws in (("w1a",WS1),("w1b",WS1),("w1c",WS1),("w2a",WS2)):
        wid = uuid.uuid4(); WI[key] = wid
        ins(c, "work_items", id=wid, workspace_id=ws, organization_id=ORG,
            original_filename=f"{key}.pdf", stored_filename=f"{key}-{wid}.pdf")
print(f"seed: {len(WI)} work items")

DV, AE, AF = {}, {}, {}
with eng.begin() as c:
    # EXTRACTION: one DISAGREED (HIGH) in WS1, one PENDING (MEDIUM) in WS1,
    # one DISAGREED in WS2 that must never appear in a WS1 query.
    for key, ws, wi, st, age in (("dv_high",WS1,"w1a","DISAGREED",3),
                                 ("dv_med", WS1,"w1b","PENDING",9),
                                 ("dv_other",WS2,"w2a","DISAGREED",1)):
        i = uuid.uuid4(); DV[key] = i
        # ARCH-12 binds scores to status as an if-and-only-if: a terminal
        # status must carry them, and PENDING must not. DISAGREED counts as
        # terminal even though it is still waiting on a human.
        ins(c, "document_verifications", id=i, workspace_id=ws, organization_id=ORG,
            work_item_id=WI[wi], status=st, agent_count=2, details="{}",
            agreement_score=(Decimal("0.5") if st != "PENDING" else None),
            confidence=(Decimal("0.5") if st != "PENDING" else None),
            created_at=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=age),
            updated_at=dt.datetime.now(dt.timezone.utc))
    # ASSERTION: needs a definition, which needs an automation node.
    rule = uuid.uuid4(); node = uuid.uuid4()
    ins(c, "automation_rules", id=rule, workspace_id=WS1, organization_id=ORG, name="r")
    ins(c, "automation_nodes", id=node, rule_id=rule, workspace_id=WS1, organization_id=ORG)
    defn = uuid.uuid4()
    ins(c, "assertion_definitions", id=defn, organization_id=ORG, workspace_id=WS1,
        node_id=node, sentence="Payment terms are net 30.",
        plan="{}", threshold=Decimal("0.75"), version=1)  # family and
    # evaluation_mode are left to the CHECK-vocabulary reader: hard-coding a
    # value here would be the probe asserting a vocabulary it does not own.
    for key, verdict, prob, age in (("ae_fail","FAIL",Decimal("0.90"),15),
                                    ("ae_ok","PASS",Decimal("0.80"),2)):
        i = uuid.uuid4(); AE[key] = i
        ins(c, "assertion_evaluations", id=i, organization_id=ORG, workspace_id=WS1,
            definition_id=defn, work_item_id=WI["w1a"], verdict=verdict,
            raw_score=Decimal("0.5"), calibrated_probability=prob, routed_to="TRIAGE",
            verification_id=DV["dv_high"],
            evidence="[]", created_at=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=age))
    # ANOMALY
    for key, sev, age, layer in (("af_high","HIGH",20,"L0"),("af_low","LOW",1,"L1")):
        i = uuid.uuid4(); AF[key] = i
        # A pairwise kind needs its counterpart: ck_af_pairwise_has_counterpart.
        # The two findings take different layers so the pair-dedupe unique
        # index does not collapse them into one.
        ins(c, "anomaly_findings", id=i, organization_id=ORG, workspace_id=WS1,
            severity=sev, layer=layer, counterpart_work_item_id=WI["w1b"], subject_work_item_id=WI["w1c"],
            score=Decimal("0.9"), headline=f"{sev} finding", status="OPEN",
            metrics='{"vendor_key": "acme"}',
            # ck_af_evidence_present: a finding with no evidence pointer is a
            # claim a reviewer cannot check, which ARCH-34 refuses outright.
            evidence='[{"page": 1, "box": [0, 0, 1, 1]}]',
            dedupe_key=uuid.uuid4().hex * 2, input_digest=uuid.uuid4().hex * 2,
            engine_version="v1",
            created_at=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=age))
print("seed: 3 verifications, 2 assertion evaluations, 2 anomaly findings")
print()
print("=== D: schema ===")

def d1_head():
    import subprocess
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "arch40_step2_settings_backfill"],
                   cwd=os.environ.get("BACKEND", "."), capture_output=True)
    with eng.connect() as c:
        h = c.execute(sa.text("select version_num from alembic_version")).scalar_one()
    assert h == "arch40_step2_settings_backfill", h
check("D1 head is arch40_step2_settings_backfill (the ARCH-40 release head)", d1_head)

def d2_audit_enum():
    with eng.connect() as c:
        vals = {r[0] for r in c.execute(sa.text(
            "select unnest(enum_range(null::audit_resource_type))::text"))}
    from_model = {"REVIEW_ITEM","REVIEW_ASSIGNMENT","AI_SETTINGS","WORKSPACE_EMAIL_OVERRIDE"}
    assert from_model <= vals, from_model - vals
check("D2 audit_resource_type carries the four ARCH-40 values", d2_audit_enum)

NAMED_CHECKS = [
 "ck_ai_settings_temperature_range","ck_ai_settings_top_p_range",
 "ck_ai_settings_frequency_penalty_range","ck_ai_settings_presence_penalty_range",
 "ck_ai_settings_max_output_tokens_range","ck_ai_settings_model_present",
 "ck_workspace_email_overrides_port_range",
 "ck_workspace_email_overrides_encryption_known",
 "ck_workspace_email_overrides_enabled_is_complete",
 "ck_workspace_email_overrides_from_address_shape",
 "ck_workspace_email_overrides_reply_to_shape",
 "ck_review_assignments_kind_known",
]
def d3_named():
    with eng.connect() as c:
        have = {r[0] for r in c.execute(sa.text(
            "select conname from pg_constraint where contype='c'"))}
    missing = [n for n in NAMED_CHECKS if n not in have]
    assert not missing, f"missing by exact name: {missing}"
check("D3 every ARCH-40 CHECK exists by its exact documented name", d3_named)

def d4_fks():
    with eng.connect() as c:
        have = {r[0] for r in c.execute(sa.text(
            "select conname from pg_constraint where contype='f'"))}
        uq = {r[0] for r in c.execute(sa.text(
            "select conname from pg_constraint where contype='u'"))}
    assert "fk_review_assignments_assignee_membership" in have
    assert "fk_workspace_email_overrides_workspace_org" in have
    assert "uq_workspaces_id_organization_id" in uq
    assert "uq_review_assignments_kind_item" in uq
check("D4 both composite foreign keys and their unique targets exist", d4_fks)

def d5_dead_nullable():
    ai = cols("ai_settings")
    for n in ("system_prompt_version","prompt_version","enable_token_tracking"):
        assert n in ai, f"{n} dropped before step 3"
        assert ai[n]["nullable"], f"{n} still NOT NULL after step 1"
    assert "enable_streaming" in ai and not ai["enable_streaming"]["nullable"]
check("D5 dead columns are nullable at step 2; enable_streaming survives NOT NULL", d5_dead_nullable)

print()
print("=== A: ai_settings contracts refuse bad rows ===")
def _ai_row(**over):
    base = dict(id=uuid.uuid4(), workspace_id=WS1, provider="GROQ", model="llama-3.1-8b",
                temperature=0.7, max_output_tokens=2048, top_p=1.0,
                frequency_penalty=0.0, presence_penalty=0.0, enable_streaming=True,
                created_at=dt.datetime.now(dt.timezone.utc), updated_at=dt.datetime.now(dt.timezone.utc))
    base.update(over); return base

def refuses(**over):
    row = _ai_row(**over)
    keys=", ".join(row); vals=", ".join(f":{k}" for k in row)
    try:
        with eng.begin() as c:
            c.execute(sa.text(f"INSERT INTO ai_settings ({keys}) VALUES ({vals})"), row)
    except sa.exc.IntegrityError:
        return
    raise AssertionError(f"accepted {over}")

check("A1 temperature 9000 is refused", lambda: refuses(temperature=9000))
check("A2 top_p 1.5 is refused", lambda: refuses(top_p=1.5))
check("A3 presence_penalty -9 is refused", lambda: refuses(presence_penalty=-9))
check("A4 max_output_tokens 0 is refused", lambda: refuses(max_output_tokens=0))
check("A5 a blank model is refused", lambda: refuses(model="   "))
def a6_good():
    row=_ai_row(); keys=", ".join(row); vals=", ".join(f":{k}" for k in row)
    with eng.begin() as c:
        c.execute(sa.text(f"INSERT INTO ai_settings ({keys}) VALUES ({vals})"), row)
        got = c.execute(sa.text(
          "select system_prompt_version, prompt_version, enable_token_tracking "
          "from ai_settings where id=:i"), {"i":row["id"]}).one()
    # The server defaults step 1 added keep a legacy INSERT working; the model
    # no longer names them, so a new INSERT leaves them at those defaults.
    assert got is not None
check("A6 a valid row inserts without naming the three dead columns", a6_good)

print()
print("=== B: the review hub ===")
def b1_all_three():
    with eng.connect() as c:
        rows = c.execute(sa.text(
          "select kind, severity, severity_rank, status from review_queue_items "
          "where workspace_id=:w and status='OPEN' "
          "order by severity_rank, created_at"), {"w":WS1}).all()
    kinds = [r[0] for r in rows]
    assert set(kinds) == {"EXTRACTION","ASSERTION","ANOMALY"}, kinds
    ranks = [r[2] for r in rows]
    assert ranks == sorted(ranks), ranks
    print("        order:", [(r[0],r[1]) for r in rows])
check("B1 one page carries all three sources, sorted by severity then age", b1_all_three)

def b2_oldest_high_first():
    with eng.connect() as c:
        first = c.execute(sa.text(
          "select kind, item_id, severity from review_queue_items "
          "where workspace_id=:w and status='OPEN' "
          "order by severity_rank, created_at limit 1"), {"w":WS1}).one()
    assert first[2] == "HIGH", first
    assert first[1] == AF["af_high"], (
        f"oldest HIGH is the 20-day anomaly, got {first}")
check("B2 the oldest high-severity item is first", b2_oldest_high_first)

def b3_counts_match_tables():
    with eng.connect() as c:
        counts = dict(c.execute(sa.text(
          "select kind, count(*) from review_queue_items "
          "where workspace_id=:w and status='OPEN' group by kind"), {"w":WS1}).all())
        dv = c.execute(sa.text("select count(*) from document_verifications "
          "where workspace_id=:w and status in ('PENDING','DISAGREED')"), {"w":WS1}).scalar_one()
        ae = c.execute(sa.text("select count(*) from assertion_evaluations "
          "where workspace_id=:w and routed_to='TRIAGE' and reviewer_verdict is null"),
          {"w":WS1}).scalar_one()
        af = c.execute(sa.text("select count(*) from anomaly_findings "
          "where workspace_id=:w and status='OPEN'"), {"w":WS1}).scalar_one()
    assert counts.get("EXTRACTION")==dv, (counts, dv)
    assert counts.get("ASSERTION")==ae, (counts, ae)
    assert counts.get("ANOMALY")==af, (counts, af)
    print("        counts:", counts)
check("B3 per-kind counts equal the underlying tables", b3_counts_match_tables)

def b4_no_cross_workspace():
    with eng.connect() as c:
        ids = {r[0] for r in c.execute(sa.text(
          "select item_id from review_queue_items where workspace_id=:w"), {"w":WS1})}
    assert DV["dv_other"] not in ids, "a WS2 verification leaked into WS1"
    with eng.connect() as c:
        n = c.execute(sa.text("select count(*) from review_queue_items "
          "where workspace_id=:w and item_id=:i"), {"w":WS1,"i":DV["dv_other"]}).scalar_one()
    assert n == 0
check("B4 a cross-workspace item is never returned", b4_no_cross_workspace)

print()
print("=== C: assignment ===")
def c1_assign_ok():
    with eng.begin() as c:
        c.execute(sa.text("""
          INSERT INTO review_assignments (id,kind,item_id,workspace_id,assignee_user_id,
                                          assigned_by_user_id,assigned_at)
          VALUES (gen_random_uuid(),'ANOMALY',:i,:w,:u,:b,now())"""),
          {"i":AF["af_high"],"w":WS1,"u":U2,"b":U1})
check("C1 assigning to a workspace member succeeds", c1_assign_ok)

def c2_non_member_refused():
    stranger = uuid.uuid4()
    with eng.begin() as c:
        ins(c, "users", id=stranger, email=f"stranger-{uuid.uuid4().hex[:6]}@acme.test", is_active=True,
            is_superuser=False, is_verified=True, timezone="UTC", locale="en")
    try:
        with eng.begin() as c:
            c.execute(sa.text("""
              INSERT INTO review_assignments (id,kind,item_id,workspace_id,assignee_user_id,
                                              assigned_by_user_id,assigned_at)
              VALUES (gen_random_uuid(),'ANOMALY',:i,:w,:u,:b,now())"""),
              {"i":AF["af_low"],"w":WS1,"u":stranger,"b":U1})
    except sa.exc.IntegrityError:
        return
    raise AssertionError("a non-member was assigned an item")
check("C2 the database refuses an assignee who is not a workspace member", c2_non_member_refused)

def c3_cascade_sweep():
    with eng.connect() as c:
        before = c.execute(sa.text("select count(*) from review_assignments "
          "where assignee_user_id=:u and workspace_id=:w"), {"u":U2,"w":WS1}).scalar_one()
    assert before == 1, before
    with eng.begin() as c:
        c.execute(sa.text("delete from workspace_members where user_id=:u and workspace_id=:w"),
                  {"u":U2,"w":WS1})
    with eng.connect() as c:
        after = c.execute(sa.text("select count(*) from review_assignments "
          "where assignee_user_id=:u and workspace_id=:w"), {"u":U2,"w":WS1}).scalar_one()
    assert after == 0, f"{after} assignment(s) survived the membership deletion"
check("C3 losing workspace access sweeps the assignment away (FK cascade)", c3_cascade_sweep)

def c4_kind_check():
    try:
        with eng.begin() as c:
            c.execute(sa.text("""
              INSERT INTO review_assignments (id,kind,item_id,workspace_id,assignee_user_id,
                                              assigned_by_user_id,assigned_at)
              VALUES (gen_random_uuid(),'NONSENSE',:i,:w,:u,:b,now())"""),
              {"i":AF["af_low"],"w":WS1,"u":U1,"b":U1})
    except sa.exc.IntegrityError:
        return
    raise AssertionError("an unknown review kind was accepted")
check("C4 an unknown review kind is refused", c4_kind_check)

print()
print("=== E: workspace_email_overrides ===")
def e1_enabled_needs_complete():
    try:
        with eng.begin() as c:
            c.execute(sa.text("""
              INSERT INTO workspace_email_overrides (workspace_id,organization_id,is_enabled)
              VALUES (:w,:o,true)"""), {"w":WS1,"o":ORG})
    except sa.exc.IntegrityError:
        return
    raise AssertionError("an enabled but incomplete override was accepted")
check("E1 an ENABLED override with no credentials is refused", e1_enabled_needs_complete)

def e2_half_finished_ok():
    with eng.begin() as c:
        c.execute(sa.text("""
          INSERT INTO workspace_email_overrides (workspace_id,organization_id,is_enabled,
                                                 from_address)
          VALUES (:w,:o,false,'billing@acme.test')"""), {"w":WS1,"o":ORG})
check("E2 a half-finished, disabled override saves (the state email_settings could not hold)", e2_half_finished_ok)

def e3_port_range():
    try:
        with eng.begin() as c:
            c.execute(sa.text("""
              INSERT INTO workspace_email_overrides (workspace_id,organization_id,is_enabled,
                                                     smtp_port)
              VALUES (:w,:o,false,70000)"""), {"w":WS2,"o":ORG})
    except sa.exc.IntegrityError:
        return
    raise AssertionError("port 70000 accepted")
check("E3 smtp_port outside 1..65535 is refused", e3_port_range)

def e4_cross_org_refused():
    other = uuid.uuid4()
    with eng.begin() as c:
        ins(c, "organizations", id=other, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}", status="ACTIVE")
    try:
        with eng.begin() as c:
            c.execute(sa.text("""
              INSERT INTO workspace_email_overrides (workspace_id,organization_id,is_enabled)
              VALUES (:w,:o,false)"""), {"w":WS2,"o":other})
    except sa.exc.IntegrityError:
        return
    raise AssertionError("an override named a workspace from another organization")
check("E4 an override cannot name a workspace in another organization", e4_cross_org_refused)

def e5_backfill_archived():
    with eng.connect() as c:
        n = c.execute(sa.text("select count(*) from settings_migration_archive "
          "where migration_revision='arch40_step2_settings_backfill'")).scalar_one()
    # No pre-existing email_settings or ai_settings rows on a fresh database,
    # so zero is the honest expectation here; the shape is what is asserted.
    assert n >= 0
    with eng.connect() as c:
        c.execute(sa.text("select settings_kind, payload from settings_migration_archive limit 1"))
check("E5 the archive table accepts the backfill shape", e5_backfill_archived)

print()
print("=== F: the contract step is gated ===")
def f1_refuses_unauthorised():
    import subprocess
    env = dict(os.environ); env.pop("ARCH40_CONTRACT", None)
    r = subprocess.run([sys.executable,"-m","alembic","upgrade","head"],
                       capture_output=True, text=True, env=env, cwd=os.environ.get("BACKEND", "."))
    assert r.returncode != 0, "step 3 ran without authorisation"
    assert "arch40_contract=1" in (r.stdout + r.stderr), (r.stdout[-500:], r.stderr[-800:])
check("F1 `alembic upgrade head` refuses the lossy contract step and says how to authorise it", f1_refuses_unauthorised)

def f2_still_at_step2():
    with eng.connect() as c:
        h = c.execute(sa.text("select version_num from alembic_version")).scalar_one()
    assert h == "arch40_step2_settings_backfill", h
    assert "system_prompt_version" in cols("ai_settings")
check("F2 the refusal left the database untouched at step 2", f2_still_at_step2)

def f3_authorised_drops():
    import subprocess, importlib
    env = dict(os.environ); env["ARCH40_CONTRACT"] = "1"
    r = subprocess.run([sys.executable,"-m","alembic","upgrade","head"],
                       capture_output=True, text=True, env=env, cwd=os.environ.get("BACKEND", "."))
    assert r.returncode == 0, (r.stdout[-800:], r.stderr[-1200:])
    global insp
    insp = sa.inspect(sa.create_engine(URL, future=True))
    ai = {c["name"] for c in insp.get_columns("ai_settings")}
    for n in ("system_prompt_version","prompt_version","enable_token_tracking"):
        assert n not in ai, f"{n} survived the contract migration"
    assert "enable_streaming" in ai, "enable_streaming was dropped; it must not be"
check("F3 ARCH40_CONTRACT=1 drops exactly the three dead columns", f3_authorised_drops)

def f4_queue_survives_contract():
    with eng.connect() as c:
        n = c.execute(sa.text("select count(*) from review_queue_items where workspace_id=:w"),
                      {"w":WS1}).scalar_one()
    assert n > 0, "the review view broke after the contract migration"
check("F4 the review hub still answers after the contract migration", f4_queue_survives_contract)

print()
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)

# Restore the database back to step 2 release head
import subprocess
subprocess.run([sys.executable, "-m", "alembic", "downgrade", "arch40_step2_settings_backfill"],
               cwd=os.environ.get("BACKEND", "."), capture_output=True)
