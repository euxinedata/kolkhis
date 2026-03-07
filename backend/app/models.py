import uuid
from datetime import datetime
from typing import Optional

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    shell_service_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_id: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    picture_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class OrgMembership(Base):
    __tablename__ = "org_memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"))
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin, member
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, active
    shell_username: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QueryJob(Base):
    __tablename__ = "query_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    sql: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    alpha_2: Mapped[str] = mapped_column(String(2), unique=True)
    alpha_3: Mapped[str] = mapped_column(String(3), unique=True)


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True)
    idle_timeout: Mapped[int] = mapped_column(Integer, default=900)  # seconds
    worker_size: Mapped[str] = mapped_column(String(20), default="cpx42")


class WorkerVM(Base):
    __tablename__ = "worker_vms"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True)
    hetzner_server_id: Mapped[int] = mapped_column(Integer)
    private_ip: Mapped[str] = mapped_column(String(45))
    server_type: Mapped[str] = mapped_column(String(20), default="cpx42")
    status: Mapped[str] = mapped_column(String(20))  # provisioning, ready, destroying, destroyed
    last_query_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    destroyed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(30))  # compute_start, compute_stop, storage
    server_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    worker_vm_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ServerTypeRate(Base):
    __tablename__ = "server_type_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_type: Mapped[str] = mapped_column(String(20), unique=True)
    hourly_rate_eur: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    display_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)



class BillingPeriod(Base):
    __tablename__ = "billing_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    compute_seconds: Mapped[int] = mapped_column(Integer)
    compute_cost_cents: Mapped[int] = mapped_column(Integer)
    storage_cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_cents: Mapped[int] = mapped_column(Integer)
    stripe_reported: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class OrgDatabase(Base):
    __tablename__ = "org_databases"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    data_path: Mapped[str] = mapped_column(String(512))
    metadata_schema: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ShellProvision(Base):
    __tablename__ = "shell_provisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))  # create, delete
    resource_type: Mapped[str] = mapped_column(String(20))  # pv, pvc, deployment, service
    resource_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20))  # pending, success, failed
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
