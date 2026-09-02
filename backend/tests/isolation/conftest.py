"""
Fixtures and self-contained database factories for ARCH-02 testing.
"""

import pytest
import uuid
from datetime import datetime, timezone
from app.models.user import User
from app.models.organization import Organization, OrganizationMember
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceStatus, WorkspaceRole
from app.models.work_item import WorkItem
from app.models.automation import AutomationRule
from app.models.assistant import Conversation
from app.models.notification import Notification, NotificationType, NotificationPriority, NotificationChannel
from app.schemas.work_item import WorkItemStatus

# ---------------------------------------------------------------------
# Factories (Self-contained to eliminate dependencies)
# ---------------------------------------------------------------------

def make_organization(db, slug):
    org = Organization(slug=slug, name=slug, status="ACTIVE")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org

def make_workspace(db, organization, slug):
    ws = Workspace(
        organization_id=organization.id,
        slug=slug,
        workspace_name=slug.capitalize(),
        status=WorkspaceStatus.ACTIVE,
        timezone="UTC",
        language="en",
        currency="USD",
        date_format="YYYY-MM-DD"
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)

    # Seed settings mock rows directly to prevent test 404s
    from app.models.ai_settings import AISettings, AIProvider
    from app.models.document_settings import DocumentSettings
    
    ai = AISettings(
        workspace_id=ws.id,
        provider=AIProvider.GROQ,
        model="mixtral-8x7b-32768",
        temperature=0.7,
        max_output_tokens=2048,
        top_p=1.0,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        input_cost_per_1k_tokens=0.0,
        output_cost_per_1k_tokens=0.0,
        system_prompt_version="v1",
        prompt_version="v1",
        enable_token_tracking=True,
        enable_streaming=True,
    )
    doc = DocumentSettings(
        workspace_id=ws.id,
        chunk_size=500,
        chunk_overlap=100,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        ocr_language="eng",
        max_upload_size=50,
        allowed_file_types="pdf,png,jpg,jpeg",
        duplicate_detection=True,
        automatic_classification=True,
        automatic_summarization=False,
        automatic_entity_extraction=False,
    )
    db.add(ai)
    db.add(doc)
    db.commit()
    return ws

def make_user(db, email):
    # email_verified_at is set because every fixture here exercises
    # workspace-scoped routes, and as of ARCH-03 Step 8 those are behind the
    # verification gate (§B.4). A fixture user without it produces 403 on every
    # tenant route — which would look like a tenancy regression and is not one.
    user = User(
        email=email,
        hashed_password="!mockpassword",
        is_active=True,
        is_superuser=False,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def join_org(db, org, user, role):
    member = OrganizationMember(organization_id=org.id, user_id=user.id, role=role, status="ACTIVE")
    db.add(member)
    db.commit()
    return member

def join_workspace(db, ws, user, role):
    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role, status="ACTIVE")
    db.add(member)
    db.commit()
    return member

def make_work_item(db, workspace, created_by, filename):
    item = WorkItem(
        original_filename=filename,
        stored_filename=f"{uuid.uuid4()}_{filename}",
        file_type="application/pdf",
        file_size=1024,
        status=WorkItemStatus.COMPLETED.value,
        workspace_id=workspace.id,
        created_by_user_id=created_by.id
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

def make_automation_rule(db, workspace, created_by, name):
    rule = AutomationRule(
        name=name,
        priority=100,
        event="WORK_ITEM_COMPLETED",
        conditions=[{"field": "status", "operator": "EQUALS", "value": "COMPLETED"}],
        logic_operator="AND",
        actions=[{"action_type": "email", "config": {"recipient": "test@test.com"}}],
        is_active=True,
        workspace_id=workspace.id,
        created_by_user_id=created_by.id
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule

def make_notification(db, workspace, user, title):
    note = Notification(
        title=title,
        message="Test Message",
        notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.INFO,
        delivery_channel=NotificationChannel.IN_APP,
        workspace_id=workspace.id,
        user_id=user.id
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note

def make_conversation(db, workspace, user, title):
    convo = Conversation(
        title=title,
        workspace_id=workspace.id,
        user_id=user.id
    )
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture
def org(db_session):
    return make_organization(db_session, slug="isolation-org")


@pytest.fixture
def alpha_ws(db_session, org):
    return make_workspace(db_session, organization=org, slug="alpha")


@pytest.fixture
def beta_ws(db_session, org):
    return make_workspace(db_session, organization=org, slug="beta")


@pytest.fixture
def multi_user(db_session, org, alpha_ws, beta_ws):
    user = make_user(db_session, email="multi@isolation.test")
    join_org(db_session, org, user, role="MEMBER")
    join_workspace(db_session, alpha_ws, user, role="ADMIN")
    join_workspace(db_session, beta_ws, user, role="ADMIN")
    return user


@pytest.fixture
def contributor_alpha(db_session, org, alpha_ws):
    user = make_user(db_session, email="contrib@isolation.test")
    join_org(db_session, org, user, role="MEMBER")
    join_workspace(db_session, alpha_ws, user, role="CONTRIBUTOR")
    return user


@pytest.fixture
def outsider(db_session):
    other_org = make_organization(db_session, slug="outsider-org")
    user = make_user(db_session, email="outsider@isolation.test")
    join_org(db_session, other_org, user, role="OWNER")
    return user


@pytest.fixture
def seeded(db_session, alpha_ws, beta_ws, multi_user, contributor_alpha):
    return {
        "alpha_doc": make_work_item(
            db_session, workspace=alpha_ws, created_by=multi_user,
            filename="ALPHA-MARKER-1.pdf",
        ),
        "alpha_doc_other_author": make_work_item(
            db_session, workspace=alpha_ws, created_by=contributor_alpha,
            filename="ALPHA-MARKER-2.pdf",
        ),
        "beta_doc": make_work_item(
            db_session, workspace=beta_ws, created_by=multi_user,
            filename="BETA-MARKER-1.pdf",
        ),
        "alpha_rule": make_automation_rule(
            db_session, workspace=alpha_ws, created_by=multi_user,
            name="ALPHA-RULE",
        ),
        "beta_rule": make_automation_rule(
            db_session, workspace=beta_ws, created_by=multi_user,
            name="BETA-RULE",
        ),
        "alpha_note": make_notification(
            db_session, workspace=alpha_ws, user=multi_user, title="ALPHA-NOTE",
        ),
        "beta_note": make_notification(
            db_session, workspace=beta_ws, user=multi_user, title="BETA-NOTE",
        ),
        "alpha_convo": make_conversation(
            db_session, workspace=alpha_ws, user=multi_user, title="ALPHA-CONVO",
        ),
        "beta_convo": make_conversation(
            db_session, workspace=beta_ws, user=multi_user, title="BETA-CONVO",
        ),
    }

@pytest.fixture
def token_for():
    from app.core.security import create_access_token
    def _token(user):
        return create_access_token(subject=str(user.id))
    return _token
