"""Schéma initial : tables V1 et V1.1, contraintes multi-tenant, audit non modifiable.

Revision ID: eccbd9bf9e88
Revises: 
Create Date: 2026-09-22 19:11:18.018461

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'eccbd9bf9e88'
down_revision = None
branch_labels = None
depends_on = None


AUDIT_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_event_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Le journal d''audit est en ajout seul : % interdit', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade():
    # Emails insensibles à la casse (extension « trusted » : créable par le propriétaire de la base).
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.create_table('laboratory',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('logo_attachment_id', sa.Uuid(), nullable=True),
    sa.Column('legal_info', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('retention_days', sa.Integer(), nullable=False),
    sa.Column('max_active_users', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('notify_by_email', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'read_only', 'suspended')", name=op.f('ck_laboratory_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_laboratory')),
    sa.UniqueConstraint('slug', name=op.f('uq_laboratory_slug'))
    )
    op.create_table('login_attempt',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_login_attempt'))
    )
    with op.batch_alter_table('login_attempt', schema=None) as batch_op:
        batch_op.create_index('ix_login_attempt_email_created_at', ['email', 'created_at'], unique=False)
        batch_op.create_index('ix_login_attempt_ip_created_at', ['ip', 'created_at'], unique=False)

    op.create_table('stripe_event',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('stripe_event_id', sa.String(length=80), nullable=False),
    sa.Column('type', sa.String(length=80), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stripe_event')),
    sa.UniqueConstraint('stripe_event_id', name=op.f('uq_stripe_event_stripe_event_id'))
    )
    op.create_table('user_account',
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('is_platform_admin', sa.Boolean(), nullable=False),
    sa.Column('is_disabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('session_version', sa.Integer(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('anonymized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_account')),
    sa.UniqueConstraint('email', name=op.f('uq_user_account_email'))
    )
    op.create_table('attachment',
    sa.Column('storage_key', sa.String(length=120), nullable=False),
    sa.Column('original_name', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('owner_type', sa.String(length=40), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('uploaded_by_id', sa.Uuid(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("owner_type IN ('laboratory', 'equipment', 'maintenance_event', 'corrective_action', 'transmission', 'non_conformity')", name=op.f('ck_attachment_owner_type')),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_attachment_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by_id'], ['user_account.id'], name=op.f('fk_attachment_uploaded_by_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attachment')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_attachment_storage_key')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_attachment_tenant_id_id'))
    )
    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.create_index('ix_attachment_owner', ['tenant_id', 'owner_type', 'owner_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_attachment_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('audit_event',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=True),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('object_type', sa.String(length=60), nullable=True),
    sa.Column('object_id', sa.String(length=64), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_audit_event_tenant_id_laboratory')),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name=op.f('fk_audit_event_user_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_event'))
    )
    with op.batch_alter_table('audit_event', schema=None) as batch_op:
        batch_op.create_index('ix_audit_event_tenant_object', ['tenant_id', 'object_type', 'object_id'], unique=False)
        batch_op.create_index('ix_audit_event_tenant_occurred', ['tenant_id', sa.literal_column('occurred_at DESC')], unique=False)

    op.create_table('auth_token',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('purpose', sa.String(length=30), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("purpose IN ('reset_password', 'verify_email')", name=op.f('ck_auth_token_purpose')),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name=op.f('fk_auth_token_user_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_token')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_auth_token_token_hash'))
    )
    with op.batch_alter_table('auth_token', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auth_token_user_id'), ['user_id'], unique=False)

    op.create_table('corrective_action',
    sa.Column('source_type', sa.String(length=30), nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('responsible_id', sa.Uuid(), nullable=True),
    sa.Column('due_on', sa.Date(), nullable=True),
    sa.Column('done_on', sa.Date(), nullable=True),
    sa.Column('done_comment', sa.Text(), nullable=True),
    sa.Column('validated_by_id', sa.Uuid(), nullable=True),
    sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("source_type IN ('ciq_run', 'non_conformity', 'manual')", name=op.f('ck_corrective_action_source_type')),
    sa.CheckConstraint("status IN ('open', 'done', 'validated')", name=op.f('ck_corrective_action_status')),
    sa.ForeignKeyConstraint(['created_by_id'], ['user_account.id'], name=op.f('fk_corrective_action_created_by_id_user_account')),
    sa.ForeignKeyConstraint(['responsible_id'], ['user_account.id'], name=op.f('fk_corrective_action_responsible_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_corrective_action_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['validated_by_id'], ['user_account.id'], name=op.f('fk_corrective_action_validated_by_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_corrective_action')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_corrective_action_tenant_id_id'))
    )
    with op.batch_alter_table('corrective_action', schema=None) as batch_op:
        batch_op.create_index('ix_corrective_action_source', ['tenant_id', 'source_type', 'source_id'], unique=False)
        batch_op.create_index('ix_corrective_action_status_due', ['tenant_id', 'status', 'due_on'], unique=False)
        batch_op.create_index(batch_op.f('ix_corrective_action_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('equipment_category',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_equipment_category_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_equipment_category')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_equipment_category_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'name', name=op.f('uq_equipment_category_tenant_id_name'))
    )
    with op.batch_alter_table('equipment_category', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_equipment_category_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('notification',
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('object_type', sa.String(length=40), nullable=True),
    sa.Column('object_id', sa.Uuid(), nullable=True),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('link', sa.String(length=255), nullable=True),
    sa.Column('dedup_key', sa.String(length=200), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('emailed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("level IN ('info', 'warning', 'danger')", name=op.f('ck_notification_level')),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_notification_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name=op.f('fk_notification_user_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification')),
    sa.UniqueConstraint('tenant_id', 'dedup_key', name=op.f('uq_notification_tenant_id_dedup_key')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_notification_tenant_id_id'))
    )
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('ix_notification_user_read', ['tenant_id', 'user_id', 'read_at'], unique=False)

    op.create_table('subscription',
    sa.Column('stripe_customer_id', sa.String(length=80), nullable=True),
    sa.Column('stripe_subscription_id', sa.String(length=80), nullable=True),
    sa.Column('plan', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('grace_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_at_period_end', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("plan IS NULL OR plan IN ('monthly', 'yearly')", name=op.f('ck_subscription_plan')),
    sa.CheckConstraint("status IN ('trialing', 'active', 'past_due', 'canceled', 'suspended')", name=op.f('ck_subscription_status')),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_subscription_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_subscription')),
    sa.UniqueConstraint('stripe_customer_id', name=op.f('uq_subscription_stripe_customer_id')),
    sa.UniqueConstraint('stripe_subscription_id', name=op.f('uq_subscription_stripe_subscription_id')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_subscription_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', name='uq_subscription_tenant')
    )
    with op.batch_alter_table('subscription', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscription_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('team',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_team_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_team_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'name', name=op.f('uq_team_tenant_id_name'))
    )
    with op.batch_alter_table('team', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_team_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('transmission',
    sa.Column('author_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('priority', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('due_on', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("category IN ('general', 'equipment', 'ciq', 'reagents', 'samples', 'quality', 'other')", name=op.f('ck_transmission_category')),
    sa.CheckConstraint("priority IN ('normal', 'important', 'urgent')", name=op.f('ck_transmission_priority')),
    sa.CheckConstraint("status IN ('new', 'read', 'in_progress', 'done', 'archived')", name=op.f('ck_transmission_status')),
    sa.ForeignKeyConstraint(['author_id'], ['user_account.id'], name=op.f('fk_transmission_author_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_transmission_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transmission')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_transmission_tenant_id_id'))
    )
    with op.batch_alter_table('transmission', schema=None) as batch_op:
        batch_op.create_index('ix_transmission_status', ['tenant_id', 'status', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_transmission_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('equipment',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('category_id', sa.Uuid(), nullable=True),
    sa.Column('manufacturer', sa.String(length=120), nullable=True),
    sa.Column('model', sa.String(length=120), nullable=True),
    sa.Column('serial_number', sa.String(length=120), nullable=True),
    sa.Column('internal_id', sa.String(length=60), nullable=False),
    sa.Column('location', sa.String(length=120), nullable=True),
    sa.Column('commissioned_on', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('criticality', sa.String(length=10), nullable=False),
    sa.Column('responsible_id', sa.Uuid(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("criticality IN ('low', 'medium', 'high')", name=op.f('ck_equipment_criticality')),
    sa.CheckConstraint("status IN ('in_service', 'out_of_service', 'maintenance', 'suspended', 'retired')", name=op.f('ck_equipment_status')),
    sa.ForeignKeyConstraint(['created_by_id'], ['user_account.id'], name=op.f('fk_equipment_created_by_id_user_account')),
    sa.ForeignKeyConstraint(['responsible_id'], ['user_account.id'], name=op.f('fk_equipment_responsible_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'category_id'], ['equipment_category.tenant_id', 'equipment_category.id'], name=op.f('fk_equipment_tenant_id_category_id_equipment_category'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_equipment_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_equipment')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_equipment_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'internal_id', name=op.f('uq_equipment_tenant_id_internal_id'))
    )
    with op.batch_alter_table('equipment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_equipment_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('invitation',
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('invited_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("role IN ('admin', 'quality', 'technician', 'reader')", name=op.f('ck_invitation_role')),
    sa.ForeignKeyConstraint(['invited_by_id'], ['user_account.id'], name=op.f('fk_invitation_invited_by_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'team_id'], ['team.tenant_id', 'team.id'], name=op.f('fk_invitation_tenant_id_team_id_team'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_invitation_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invitation')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_invitation_tenant_id_id')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_invitation_token_hash'))
    )
    with op.batch_alter_table('invitation', schema=None) as batch_op:
        batch_op.create_index('ix_invitation_tenant_email', ['tenant_id', 'email'], unique=False)
        batch_op.create_index(batch_op.f('ix_invitation_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('membership',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("role IN ('admin', 'quality', 'technician', 'reader')", name=op.f('ck_membership_role')),
    sa.ForeignKeyConstraint(['tenant_id', 'team_id'], ['team.tenant_id', 'team.id'], name=op.f('fk_membership_tenant_id_team_id_team'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_membership_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name=op.f('fk_membership_user_id_user_account'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_membership')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_membership_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'user_id', name=op.f('uq_membership_tenant_id_user_id'))
    )
    with op.batch_alter_table('membership', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_membership_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_membership_user_id'), ['user_id'], unique=False)

    op.create_table('transmission_comment',
    sa.Column('transmission_id', sa.Uuid(), nullable=False),
    sa.Column('author_id', sa.Uuid(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['user_account.id'], name=op.f('fk_transmission_comment_author_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'transmission_id'], ['transmission.tenant_id', 'transmission.id'], name=op.f('fk_transmission_comment_tenant_id_transmission_id_transmission'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_transmission_comment_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transmission_comment')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_transmission_comment_tenant_id_id'))
    )
    with op.batch_alter_table('transmission_comment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_transmission_comment_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_transmission_comment_transmission_id'), ['transmission_id'], unique=False)

    op.create_table('transmission_read_receipt',
    sa.Column('transmission_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'transmission_id'], ['transmission.tenant_id', 'transmission.id'], name=op.f('fk_transmission_read_receipt_tenant_id_transmission_id_transmission'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_transmission_read_receipt_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name=op.f('fk_transmission_read_receipt_user_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transmission_read_receipt')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_transmission_read_receipt_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'transmission_id', 'user_id', name=op.f('uq_transmission_read_receipt_tenant_id_transmission_id_user_id'))
    )
    with op.batch_alter_table('transmission_read_receipt', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_transmission_read_receipt_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('transmission_recipient',
    sa.Column('transmission_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('(user_id IS NULL) <> (team_id IS NULL)', name=op.f('ck_transmission_recipient_one_target')),
    sa.ForeignKeyConstraint(['tenant_id', 'team_id'], ['team.tenant_id', 'team.id'], name=op.f('fk_transmission_recipient_tenant_id_team_id_team'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'transmission_id'], ['transmission.tenant_id', 'transmission.id'], name=op.f('fk_transmission_recipient_tenant_id_transmission_id_transmission'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_transmission_recipient_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name=op.f('fk_transmission_recipient_user_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transmission_recipient')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_transmission_recipient_tenant_id_id'))
    )
    with op.batch_alter_table('transmission_recipient', schema=None) as batch_op:
        batch_op.create_index('ix_transmission_recipient_team', ['tenant_id', 'team_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_transmission_recipient_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_transmission_recipient_transmission_id'), ['transmission_id'], unique=False)
        batch_op.create_index('ix_transmission_recipient_user', ['tenant_id', 'user_id'], unique=False)

    op.create_table('ciq_parameter',
    sa.Column('equipment_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('unit', sa.String(length=40), nullable=True),
    sa.Column('decimals', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'equipment_id'], ['equipment.tenant_id', 'equipment.id'], name=op.f('fk_ciq_parameter_tenant_id_equipment_id_equipment'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_ciq_parameter_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ciq_parameter')),
    sa.UniqueConstraint('tenant_id', 'equipment_id', 'name', name=op.f('uq_ciq_parameter_tenant_id_equipment_id_name')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_ciq_parameter_tenant_id_id'))
    )
    with op.batch_alter_table('ciq_parameter', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ciq_parameter_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('maintenance_plan',
    sa.Column('equipment_id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=30), nullable=False),
    sa.Column('period_value', sa.Integer(), nullable=False),
    sa.Column('period_unit', sa.String(length=10), nullable=False),
    sa.Column('provider', sa.String(length=200), nullable=True),
    sa.Column('responsible_id', sa.Uuid(), nullable=True),
    sa.Column('last_done_on', sa.Date(), nullable=True),
    sa.Column('next_due_on', sa.Date(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("event_type IN ('calibration', 'verification', 'preventive', 'corrective', 'intermediate_check', 'qualification')", name=op.f('ck_maintenance_plan_event_type')),
    sa.CheckConstraint("period_unit IN ('day', 'week', 'month', 'year')", name=op.f('ck_maintenance_plan_period_unit')),
    sa.CheckConstraint('period_value > 0', name=op.f('ck_maintenance_plan_period_positive')),
    sa.ForeignKeyConstraint(['created_by_id'], ['user_account.id'], name=op.f('fk_maintenance_plan_created_by_id_user_account')),
    sa.ForeignKeyConstraint(['responsible_id'], ['user_account.id'], name=op.f('fk_maintenance_plan_responsible_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'equipment_id'], ['equipment.tenant_id', 'equipment.id'], name=op.f('fk_maintenance_plan_tenant_id_equipment_id_equipment'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_maintenance_plan_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_maintenance_plan')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_maintenance_plan_tenant_id_id'))
    )
    with op.batch_alter_table('maintenance_plan', schema=None) as batch_op:
        batch_op.create_index('ix_maintenance_plan_due', ['tenant_id', 'next_due_on'], unique=False, postgresql_where=sa.text('is_active'))
        batch_op.create_index(batch_op.f('ix_maintenance_plan_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('ciq_rule_config',
    sa.Column('parameter_id', sa.Uuid(), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('rules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('comment_required_on', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('min_reference_points', sa.Integer(), nullable=False),
    sa.Column('chain_lots', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("mode IN ('westgard', 'shewhart')", name=op.f('ck_ciq_rule_config_mode')),
    sa.ForeignKeyConstraint(['tenant_id', 'parameter_id'], ['ciq_parameter.tenant_id', 'ciq_parameter.id'], name=op.f('fk_ciq_rule_config_tenant_id_parameter_id_ciq_parameter'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_ciq_rule_config_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ciq_rule_config')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_ciq_rule_config_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'parameter_id', name=op.f('uq_ciq_rule_config_tenant_id_parameter_id'))
    )
    with op.batch_alter_table('ciq_rule_config', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ciq_rule_config_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('ciq_run',
    sa.Column('equipment_id', sa.Uuid(), nullable=False),
    sa.Column('parameter_id', sa.Uuid(), nullable=False),
    sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('operator_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('justification', sa.Text(), nullable=True),
    sa.Column('justified_by_id', sa.Uuid(), nullable=True),
    sa.Column('justified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("status IN ('open', 'accepted', 'rejected', 'justified')", name=op.f('ck_ciq_run_status')),
    sa.ForeignKeyConstraint(['created_by_id'], ['user_account.id'], name=op.f('fk_ciq_run_created_by_id_user_account')),
    sa.ForeignKeyConstraint(['justified_by_id'], ['user_account.id'], name=op.f('fk_ciq_run_justified_by_id_user_account')),
    sa.ForeignKeyConstraint(['operator_id'], ['user_account.id'], name=op.f('fk_ciq_run_operator_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'equipment_id'], ['equipment.tenant_id', 'equipment.id'], name=op.f('fk_ciq_run_tenant_id_equipment_id_equipment'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'parameter_id'], ['ciq_parameter.tenant_id', 'ciq_parameter.id'], name=op.f('fk_ciq_run_tenant_id_parameter_id_ciq_parameter'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_ciq_run_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ciq_run')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_ciq_run_tenant_id_id'))
    )
    with op.batch_alter_table('ciq_run', schema=None) as batch_op:
        batch_op.create_index('ix_ciq_run_parameter_run_at', ['tenant_id', 'parameter_id', 'run_at'], unique=False)
        batch_op.create_index('ix_ciq_run_status', ['tenant_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_ciq_run_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('control_level',
    sa.Column('parameter_id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=40), nullable=False),
    sa.Column('order', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("mode IS NULL OR mode IN ('westgard', 'shewhart')", name=op.f('ck_control_level_mode')),
    sa.ForeignKeyConstraint(['tenant_id', 'parameter_id'], ['ciq_parameter.tenant_id', 'ciq_parameter.id'], name=op.f('fk_control_level_tenant_id_parameter_id_ciq_parameter'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_control_level_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_control_level')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_control_level_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'parameter_id', 'label', name=op.f('uq_control_level_tenant_id_parameter_id_label'))
    )
    with op.batch_alter_table('control_level', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_control_level_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('maintenance_event',
    sa.Column('plan_id', sa.Uuid(), nullable=True),
    sa.Column('equipment_id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=30), nullable=False),
    sa.Column('performed_on', sa.Date(), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=True),
    sa.Column('provider', sa.String(length=200), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('performed_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("event_type IN ('calibration', 'verification', 'preventive', 'corrective', 'intermediate_check', 'qualification')", name=op.f('ck_maintenance_event_event_type')),
    sa.CheckConstraint("outcome IS NULL OR outcome IN ('conform', 'non_conform')", name=op.f('ck_maintenance_event_outcome')),
    sa.ForeignKeyConstraint(['performed_by_id'], ['user_account.id'], name=op.f('fk_maintenance_event_performed_by_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'equipment_id'], ['equipment.tenant_id', 'equipment.id'], name=op.f('fk_maintenance_event_tenant_id_equipment_id_equipment'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'plan_id'], ['maintenance_plan.tenant_id', 'maintenance_plan.id'], name=op.f('fk_maintenance_event_tenant_id_plan_id_maintenance_plan'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_maintenance_event_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_maintenance_event')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_maintenance_event_tenant_id_id'))
    )
    with op.batch_alter_table('maintenance_event', schema=None) as batch_op:
        batch_op.create_index('ix_maintenance_event_equipment', ['tenant_id', 'equipment_id', 'performed_on'], unique=False)
        batch_op.create_index(batch_op.f('ix_maintenance_event_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('control_lot',
    sa.Column('level_id', sa.Uuid(), nullable=False),
    sa.Column('manufacturer', sa.String(length=120), nullable=True),
    sa.Column('lot_number', sa.String(length=80), nullable=False),
    sa.Column('expires_on', sa.Date(), nullable=True),
    sa.Column('in_use_from', sa.Date(), nullable=True),
    sa.Column('in_use_to', sa.Date(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id', 'level_id'], ['control_level.tenant_id', 'control_level.id'], name=op.f('fk_control_lot_tenant_id_level_id_control_level'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_control_lot_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_control_lot')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_control_lot_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'level_id', 'lot_number', name=op.f('uq_control_lot_tenant_id_level_id_lot_number'))
    )
    with op.batch_alter_table('control_lot', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_control_lot_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('non_conformity',
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('detected_on', sa.Date(), nullable=False),
    sa.Column('detected_by_id', sa.Uuid(), nullable=True),
    sa.Column('origin', sa.String(length=30), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('impact', sa.Text(), nullable=True),
    sa.Column('immediate_action', sa.Text(), nullable=True),
    sa.Column('root_cause', sa.Text(), nullable=True),
    sa.Column('no_action_justification', sa.Text(), nullable=True),
    sa.Column('responsible_id', sa.Uuid(), nullable=True),
    sa.Column('target_date', sa.Date(), nullable=True),
    sa.Column('effectiveness_check', sa.Text(), nullable=True),
    sa.Column('effectiveness_justification', sa.Text(), nullable=True),
    sa.Column('closed_on', sa.Date(), nullable=True),
    sa.Column('validated_by_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.Column('source_ciq_run_id', sa.Uuid(), nullable=True),
    sa.Column('equipment_id', sa.Uuid(), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("origin IN ('ciq', 'metrology', 'audit_internal', 'audit_external', 'complaint', 'supplier', 'other')", name=op.f('ck_non_conformity_origin')),
    sa.CheckConstraint("severity IN ('minor', 'major', 'critical')", name=op.f('ck_non_conformity_severity')),
    sa.CheckConstraint("status IN ('draft', 'open', 'analysis', 'action', 'verification', 'closed', 'cancelled')", name=op.f('ck_non_conformity_status')),
    sa.ForeignKeyConstraint(['created_by_id'], ['user_account.id'], name=op.f('fk_non_conformity_created_by_id_user_account')),
    sa.ForeignKeyConstraint(['detected_by_id'], ['user_account.id'], name=op.f('fk_non_conformity_detected_by_id_user_account')),
    sa.ForeignKeyConstraint(['responsible_id'], ['user_account.id'], name=op.f('fk_non_conformity_responsible_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'equipment_id'], ['equipment.tenant_id', 'equipment.id'], name=op.f('fk_non_conformity_tenant_id_equipment_id_equipment'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'source_ciq_run_id'], ['ciq_run.tenant_id', 'ciq_run.id'], name=op.f('fk_non_conformity_tenant_id_source_ciq_run_id_ciq_run'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_non_conformity_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['validated_by_id'], ['user_account.id'], name=op.f('fk_non_conformity_validated_by_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_non_conformity')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_non_conformity_tenant_id_id')),
    sa.UniqueConstraint('tenant_id', 'year', 'seq', name=op.f('uq_non_conformity_tenant_id_year_seq'))
    )
    with op.batch_alter_table('non_conformity', schema=None) as batch_op:
        batch_op.create_index('ix_non_conformity_status', ['tenant_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_non_conformity_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('control_limit_set',
    sa.Column('lot_id', sa.Uuid(), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('mean', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('sd', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('reference_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reference_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('n_reference', sa.Integer(), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("mode IN ('westgard', 'shewhart')", name=op.f('ck_control_limit_set_mode')),
    sa.CheckConstraint("source IN ('supplier', 'lab', 'computed')", name=op.f('ck_control_limit_set_source')),
    sa.CheckConstraint('sd > 0', name=op.f('ck_control_limit_set_sd_positive')),
    sa.ForeignKeyConstraint(['created_by_id'], ['user_account.id'], name=op.f('fk_control_limit_set_created_by_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'lot_id'], ['control_lot.tenant_id', 'control_lot.id'], name=op.f('fk_control_limit_set_tenant_id_lot_id_control_lot'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_control_limit_set_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_control_limit_set')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_control_limit_set_tenant_id_id'))
    )
    with op.batch_alter_table('control_limit_set', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_control_limit_set_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('uq_control_limit_set_active', ['tenant_id', 'lot_id'], unique=True, postgresql_where=sa.text('valid_to IS NULL'))

    op.create_table('non_conformity_status_change',
    sa.Column('non_conformity_id', sa.Uuid(), nullable=False),
    sa.Column('from_status', sa.String(length=20), nullable=True),
    sa.Column('to_status', sa.String(length=20), nullable=False),
    sa.Column('changed_by_id', sa.Uuid(), nullable=True),
    sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("from_status IS NULL OR from_status IN ('draft', 'open', 'analysis', 'action', 'verification', 'closed', 'cancelled')", name=op.f('ck_non_conformity_status_change_from_status')),
    sa.CheckConstraint("to_status IN ('draft', 'open', 'analysis', 'action', 'verification', 'closed', 'cancelled')", name=op.f('ck_non_conformity_status_change_to_status')),
    sa.ForeignKeyConstraint(['changed_by_id'], ['user_account.id'], name=op.f('fk_non_conformity_status_change_changed_by_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'non_conformity_id'], ['non_conformity.tenant_id', 'non_conformity.id'], name=op.f('fk_non_conformity_status_change_tenant_id_non_conformity_id_non_conformity'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_non_conformity_status_change_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_non_conformity_status_change')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_non_conformity_status_change_tenant_id_id'))
    )
    with op.batch_alter_table('non_conformity_status_change', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_non_conformity_status_change_non_conformity_id'), ['non_conformity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_non_conformity_status_change_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('ciq_result',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('level_id', sa.Uuid(), nullable=False),
    sa.Column('lot_id', sa.Uuid(), nullable=False),
    sa.Column('limit_set_id', sa.Uuid(), nullable=False),
    sa.Column('parameter_id', sa.Uuid(), nullable=False),
    sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('mode', sa.String(length=20), nullable=False),
    sa.Column('value', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('z_score', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('deviation', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('rules_triggered', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rules_not_evaluated', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('entered_by_id', sa.Uuid(), nullable=True),
    sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('void_reason', sa.Text(), nullable=True),
    sa.Column('voided_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("mode IN ('westgard', 'shewhart')", name=op.f('ck_ciq_result_mode')),
    sa.CheckConstraint("status IN ('accepted', 'warning', 'rejected')", name=op.f('ck_ciq_result_status')),
    sa.ForeignKeyConstraint(['entered_by_id'], ['user_account.id'], name=op.f('fk_ciq_result_entered_by_id_user_account')),
    sa.ForeignKeyConstraint(['tenant_id', 'level_id'], ['control_level.tenant_id', 'control_level.id'], name=op.f('fk_ciq_result_tenant_id_level_id_control_level'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'limit_set_id'], ['control_limit_set.tenant_id', 'control_limit_set.id'], name=op.f('fk_ciq_result_tenant_id_limit_set_id_control_limit_set'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'lot_id'], ['control_lot.tenant_id', 'control_lot.id'], name=op.f('fk_ciq_result_tenant_id_lot_id_control_lot'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'parameter_id'], ['ciq_parameter.tenant_id', 'ciq_parameter.id'], name=op.f('fk_ciq_result_tenant_id_parameter_id_ciq_parameter'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id', 'run_id'], ['ciq_run.tenant_id', 'ciq_run.id'], name=op.f('fk_ciq_result_tenant_id_run_id_ciq_run'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tenant_id'], ['laboratory.id'], name=op.f('fk_ciq_result_tenant_id_laboratory'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['voided_by_id'], ['user_account.id'], name=op.f('fk_ciq_result_voided_by_id_user_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ciq_result')),
    sa.UniqueConstraint('tenant_id', 'id', name=op.f('uq_ciq_result_tenant_id_id'))
    )
    with op.batch_alter_table('ciq_result', schema=None) as batch_op:
        batch_op.create_index('ix_ciq_result_level_run_at', ['tenant_id', 'level_id', 'run_at'], unique=False)
        batch_op.create_index('ix_ciq_result_lot_run_at', ['tenant_id', 'lot_id', 'run_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_ciq_result_parameter_id'), ['parameter_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ciq_result_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index('uq_ciq_result_run_level_valid', ['tenant_id', 'run_id', 'level_id'], unique=True, postgresql_where=sa.text('voided_at IS NULL'))


    # Défense en profondeur : même si les droits du rôle applicatif étaient mal configurés,
    # toute modification ou suppression du journal d'audit est refusée par PostgreSQL.
    op.execute(AUDIT_GUARD_FUNCTION)
    op.execute(
        "CREATE TRIGGER audit_event_no_update_delete BEFORE UPDATE OR DELETE ON audit_event "
        "FOR EACH ROW EXECUTE FUNCTION audit_event_immutable()"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_event_immutable()"
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event")
    op.execute("DROP TRIGGER IF EXISTS audit_event_no_update_delete ON audit_event")
    op.execute("DROP FUNCTION IF EXISTS audit_event_immutable()")
    with op.batch_alter_table('ciq_result', schema=None) as batch_op:
        batch_op.drop_index('uq_ciq_result_run_level_valid', postgresql_where=sa.text('voided_at IS NULL'))
        batch_op.drop_index(batch_op.f('ix_ciq_result_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_ciq_result_parameter_id'))
        batch_op.drop_index('ix_ciq_result_lot_run_at')
        batch_op.drop_index('ix_ciq_result_level_run_at')

    op.drop_table('ciq_result')
    with op.batch_alter_table('non_conformity_status_change', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_non_conformity_status_change_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_non_conformity_status_change_non_conformity_id'))

    op.drop_table('non_conformity_status_change')
    with op.batch_alter_table('control_limit_set', schema=None) as batch_op:
        batch_op.drop_index('uq_control_limit_set_active', postgresql_where=sa.text('valid_to IS NULL'))
        batch_op.drop_index(batch_op.f('ix_control_limit_set_tenant_id'))

    op.drop_table('control_limit_set')
    with op.batch_alter_table('non_conformity', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_non_conformity_tenant_id'))
        batch_op.drop_index('ix_non_conformity_status')

    op.drop_table('non_conformity')
    with op.batch_alter_table('control_lot', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_control_lot_tenant_id'))

    op.drop_table('control_lot')
    with op.batch_alter_table('maintenance_event', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_maintenance_event_tenant_id'))
        batch_op.drop_index('ix_maintenance_event_equipment')

    op.drop_table('maintenance_event')
    with op.batch_alter_table('control_level', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_control_level_tenant_id'))

    op.drop_table('control_level')
    with op.batch_alter_table('ciq_run', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ciq_run_tenant_id'))
        batch_op.drop_index('ix_ciq_run_status')
        batch_op.drop_index('ix_ciq_run_parameter_run_at')

    op.drop_table('ciq_run')
    with op.batch_alter_table('ciq_rule_config', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ciq_rule_config_tenant_id'))

    op.drop_table('ciq_rule_config')
    with op.batch_alter_table('maintenance_plan', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_maintenance_plan_tenant_id'))
        batch_op.drop_index('ix_maintenance_plan_due', postgresql_where=sa.text('is_active'))

    op.drop_table('maintenance_plan')
    with op.batch_alter_table('ciq_parameter', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ciq_parameter_tenant_id'))

    op.drop_table('ciq_parameter')
    with op.batch_alter_table('transmission_recipient', schema=None) as batch_op:
        batch_op.drop_index('ix_transmission_recipient_user')
        batch_op.drop_index(batch_op.f('ix_transmission_recipient_transmission_id'))
        batch_op.drop_index(batch_op.f('ix_transmission_recipient_tenant_id'))
        batch_op.drop_index('ix_transmission_recipient_team')

    op.drop_table('transmission_recipient')
    with op.batch_alter_table('transmission_read_receipt', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transmission_read_receipt_tenant_id'))

    op.drop_table('transmission_read_receipt')
    with op.batch_alter_table('transmission_comment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transmission_comment_transmission_id'))
        batch_op.drop_index(batch_op.f('ix_transmission_comment_tenant_id'))

    op.drop_table('transmission_comment')
    with op.batch_alter_table('membership', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_membership_user_id'))
        batch_op.drop_index(batch_op.f('ix_membership_tenant_id'))

    op.drop_table('membership')
    with op.batch_alter_table('invitation', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invitation_tenant_id'))
        batch_op.drop_index('ix_invitation_tenant_email')

    op.drop_table('invitation')
    with op.batch_alter_table('equipment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_equipment_tenant_id'))

    op.drop_table('equipment')
    with op.batch_alter_table('transmission', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transmission_tenant_id'))
        batch_op.drop_index('ix_transmission_status')

    op.drop_table('transmission')
    with op.batch_alter_table('team', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_team_tenant_id'))

    op.drop_table('team')
    with op.batch_alter_table('subscription', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscription_tenant_id'))

    op.drop_table('subscription')
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.drop_index('ix_notification_user_read')
        batch_op.drop_index(batch_op.f('ix_notification_tenant_id'))

    op.drop_table('notification')
    with op.batch_alter_table('equipment_category', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_equipment_category_tenant_id'))

    op.drop_table('equipment_category')
    with op.batch_alter_table('corrective_action', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_corrective_action_tenant_id'))
        batch_op.drop_index('ix_corrective_action_status_due')
        batch_op.drop_index('ix_corrective_action_source')

    op.drop_table('corrective_action')
    with op.batch_alter_table('auth_token', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auth_token_user_id'))

    op.drop_table('auth_token')
    with op.batch_alter_table('audit_event', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_event_tenant_occurred')
        batch_op.drop_index('ix_audit_event_tenant_object')

    op.drop_table('audit_event')
    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attachment_tenant_id'))
        batch_op.drop_index('ix_attachment_owner')

    op.drop_table('attachment')
    op.drop_table('user_account')
    op.drop_table('stripe_event')
    with op.batch_alter_table('login_attempt', schema=None) as batch_op:
        batch_op.drop_index('ix_login_attempt_ip_created_at')
        batch_op.drop_index('ix_login_attempt_email_created_at')

    op.drop_table('login_attempt')
    op.drop_table('laboratory')
