from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
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


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    alpha_2: Mapped[str] = mapped_column(String(2), unique=True)
    alpha_3: Mapped[str] = mapped_column(String(3), unique=True)
