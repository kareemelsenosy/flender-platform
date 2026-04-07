"""SQLAlchemy ORM models."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    mapping_formats = relationship("ColumnMappingFormat", back_populates="user")
    brand_configs = relationship("BrandSearchConfig", back_populates="user")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    source_type = Column(String(20), nullable=False)  # excel_upload, csv_upload, google_sheets
    source_ref = Column(Text)  # filename or Google Sheets URL
    status = Column(String(20), default="created", index=True)  # created, mapping, searching, reviewing, completed
    column_mapping_json = Column(Text, default="{}")  # JSON string
    config_json = Column(Text, default="{}")  # session-specific config
    total_items = Column(Integer, default=0)
    searched_items = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="sessions")
    uploaded_file = relationship("UploadedFile", back_populates="session", uselist=False, cascade="all, delete-orphan")
    unique_items = relationship("UniqueItem", back_populates="session", cascade="all, delete-orphan")
    generated_files = relationship("GeneratedFile", back_populates="session", cascade="all, delete-orphan")

    @property
    def column_mapping(self) -> dict:
        return json.loads(self.column_mapping_json or "{}")

    @column_mapping.setter
    def column_mapping(self, val: dict):
        self.column_mapping_json = json.dumps(val)

    @property
    def config(self) -> dict:
        return json.loads(self.config_json or "{}")

    @config.setter
    def config(self, val: dict):
        self.config_json = json.dumps(val)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)
    uploaded_at = Column(DateTime, default=_utcnow)

    session = relationship("Session", back_populates="uploaded_file")


class UniqueItem(Base):
    __tablename__ = "unique_items"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    item_code = Column(String(500), nullable=False)
    color_code = Column(String(255))
    brand = Column(String(255))
    style_name = Column(String(500))
    color_name = Column(String(255))
    gender = Column(String(50))
    wholesale_price = Column(Float)
    retail_price = Column(Float)
    sizes_json = Column(Text, default="[]")
    qty_available = Column(Float)
    barcode = Column(String(255))

    # Search & review state
    search_status = Column(String(20), default="pending")  # pending, done
    candidates_json = Column(Text, default="[]")  # JSON list of candidate URLs
    scores_json = Column(Text, default="{}")  # JSON dict {url: score}
    review_status = Column(String(20), default="pending")  # pending, approved, skipped
    approved_url = Column(Text)
    additional_urls_json = Column(Text, default="[]")  # extra images for folder download
    auto_selected = Column(Boolean, default=False)

    session = relationship("Session", back_populates="unique_items")

    __table_args__ = (
        UniqueConstraint("session_id", "item_code", "color_code"),
        Index("ix_unique_items_session_id", "session_id"),
        Index("ix_unique_items_session_search", "session_id", "search_status"),
        Index("ix_unique_items_session_review", "session_id", "review_status"),
    )

    @property
    def additional_urls(self) -> list:
        return json.loads(self.additional_urls_json or "[]")

    @additional_urls.setter
    def additional_urls(self, val: list):
        self.additional_urls_json = json.dumps(val)

    @property
    def sizes(self) -> list:
        return json.loads(self.sizes_json or "[]")

    @sizes.setter
    def sizes(self, val: list):
        self.sizes_json = json.dumps(val)

    @property
    def candidates(self) -> list:
        return json.loads(self.candidates_json or "[]")

    @candidates.setter
    def candidates(self, val: list):
        self.candidates_json = json.dumps(val)

    @property
    def scores(self) -> dict:
        return json.loads(self.scores_json or "{}")

    @scores.setter
    def scores(self, val: dict):
        self.scores_json = json.dumps(val)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "item_code": self.item_code,
            "color_code": self.color_code,
            "brand": self.brand,
            "style_name": self.style_name,
            "color_name": self.color_name,
            "gender": self.gender,
            "wholesale_price": self.wholesale_price,
            "retail_price": self.retail_price,
            "sizes": self.sizes,
            "qty_available": self.qty_available,
            "barcode": self.barcode,
            "candidates": self.candidates,
            "scores": self.scores,
            "review_status": self.review_status,
            "approved_url": self.approved_url,
            "auto_selected": self.auto_selected,
        }


class SearchCache(Base):
    """Cross-session search cache — same SKU reuses results."""
    __tablename__ = "search_cache"

    id = Column(Integer, primary_key=True)
    item_code = Column(String(500), nullable=False)
    color_code = Column(String(255), default="")
    brand = Column(String(255), default="")
    candidates_json = Column(Text, default="[]")
    scores_json = Column(Text, default="{}")
    searched_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("item_code", "color_code", "brand"),)

    @property
    def candidates(self) -> list:
        return json.loads(self.candidates_json or "[]")

    @candidates.setter
    def candidates(self, val: list):
        self.candidates_json = json.dumps(val)

    @property
    def scores(self) -> dict:
        return json.loads(self.scores_json or "{}")

    @scores.setter
    def scores(self, val: dict):
        self.scores_json = json.dumps(val)


class ColumnMappingFormat(Base):
    __tablename__ = "column_mapping_formats"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    mapping_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    user = relationship("User", back_populates="mapping_formats")

    @property
    def mapping(self) -> dict:
        return json.loads(self.mapping_json or "{}")

    @mapping.setter
    def mapping(self, val: dict):
        self.mapping_json = json.dumps(val)


class BrandSearchConfig(Base):
    __tablename__ = "brand_search_configs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    brand_name = Column(String(100), nullable=False)
    site_urls_json = Column(Text, default="[]")
    search_notes = Column(Text, default="")  # AI instructions for this brand
    priority = Column(Integer, default=0)

    user = relationship("User", back_populates="brand_configs")

    __table_args__ = (UniqueConstraint("user_id", "brand_name"),)

    @property
    def site_urls(self) -> list:
        return json.loads(self.site_urls_json or "[]")

    @site_urls.setter
    def site_urls(self, val: list):
        self.site_urls_json = json.dumps(val)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


class GeneratedFile(Base):
    __tablename__ = "generated_files"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False, index=True)
    token = Column(String(64), unique=True, nullable=False)
    file_path = Column(String(500), nullable=False)
    filename = Column(String(255), nullable=False)
    image_folder_path = Column(String(500))
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    session = relationship("Session", back_populates="generated_files")
