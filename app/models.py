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
    email_verified = Column(Boolean, default=False)
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
    item_group = Column(String(500))
    item_group_code = Column(String(500))
    sap_code = Column(String(500))
    source_sheet = Column(String(255))
    # Row index in the source sheet/file, captured at import time so the export
    # can preserve the user's original ordering instead of re-sorting by brand
    # /style/etc. NULL for legacy rows imported before this column existed.
    source_order = Column(Integer)

    # Search & review state
    search_status = Column(String(20), default="pending")  # pending, done
    candidates_json = Column(Text, default="[]")  # JSON list of candidate URLs
    scores_json = Column(Text, default="{}")  # JSON dict {url: score}
    match_reasons_json = Column(Text, default="{}")  # JSON dict {url: reason}
    review_status = Column(String(20), default="pending")  # pending, approved, skipped
    approved_url = Column(Text)
    suggested_url = Column(Text)
    pictures_url = Column(Text)  # original Dropbox folder link from "Pictures" column
    additional_urls_json = Column(Text, default="[]")  # extra images for folder download
    auto_selected = Column(Boolean, default=False)
    search_confidence = Column(Float, default=0.0)
    confidence_label = Column(String(20), default="low")
    confidence_reason = Column(Text)
    comming_soon_qty = Column(String(50))  # "Comming Soon" column from Google Sheets (Dubai Reorder)
    # Multi-source merge (Step 3): per-field {field: {value, source, conflicts:[{source,value}]}}.
    # Empty "{}" for normal single-source items; populated when this item was produced by a merge.
    provenance_json = Column(Text, default="{}")

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
    def provenance(self) -> dict:
        return json.loads(self.provenance_json or "{}")

    @provenance.setter
    def provenance(self, val: dict):
        self.provenance_json = json.dumps(val or {})

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

    @property
    def match_reasons(self) -> dict:
        return json.loads(self.match_reasons_json or "{}")

    @match_reasons.setter
    def match_reasons(self, val: dict):
        self.match_reasons_json = json.dumps(val)

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
            "item_group": self.item_group,
            "item_group_code": self.item_group_code,
            "sap_code": self.sap_code,
            "source_sheet": self.source_sheet,
            "comming_soon_qty": self.comming_soon_qty,
            "candidates": self.candidates,
            "scores": self.scores,
            "match_reasons": self.match_reasons,
            "review_status": self.review_status,
            "approved_url": self.approved_url,
            "suggested_url": self.suggested_url,
            "auto_selected": self.auto_selected,
            "search_confidence": self.search_confidence,
            "confidence_label": self.confidence_label,
            "confidence_reason": self.confidence_reason,
        }


class SearchCache(Base):
    """Cross-session search cache — same SKU reuses results."""
    __tablename__ = "search_cache"

    id = Column(Integer, primary_key=True)
    item_code = Column(String(500), nullable=False)
    color_code = Column(String(255), default="")
    brand = Column(String(255), default="")
    search_version = Column(Integer, default=1)
    candidates_json = Column(Text, default="[]")
    scores_json = Column(Text, default="{}")
    match_reasons_json = Column(Text, default="{}")
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

    @property
    def match_reasons(self) -> dict:
        return json.loads(self.match_reasons_json or "{}")

    @match_reasons.setter
    def match_reasons(self, val: dict):
        self.match_reasons_json = json.dumps(val)


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


class EmailVerificationCode(Base):
    __tablename__ = "email_verification_codes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code = Column(String(6), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


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


class ProductAttributeRun(Base):
    """A saved run of the Product Attributes tool — one SAP attribute extraction
    over one or more uploaded product exports. Persisted so users can reopen,
    re-download, and hand-correct the AI's product types / attributes."""
    __tablename__ = "product_attribute_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(500))
    created_at = Column(DateTime, default=_utcnow)
    status = Column(String(20), default="running")  # running, done, error
    error = Column(Text)
    filename = Column(String(1000))                 # original upload name(s)
    total_styles = Column(Integer, default=0)
    clean_count = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    row_count = Column(Integer, default=0)
    results_json = Column(Text, default="[]")       # list of per-style result dicts
    columns_json = Column(Text, default="[]")       # columns found in the source(s)

    @property
    def results(self) -> list:
        return json.loads(self.results_json or "[]")

    @results.setter
    def results(self, val: list):
        self.results_json = json.dumps(val or [])

    @property
    def columns(self) -> list:
        return json.loads(self.columns_json or "[]")

    @columns.setter
    def columns(self, val: list):
        self.columns_json = json.dumps(val or [])


class ImageSortRun(Base):
    """A saved run of the Image Sorter — one batch of unnamed product photos
    matched against a SAP master file's Item Group Codes.

    Persisted so users can reopen a run, correct the AI's assignments, and
    re-download the folder ZIP without re-running the vision passes.
    """
    __tablename__ = "image_sort_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(500))
    created_at = Column(DateTime, default=_utcnow)
    status = Column(String(20), default="running")   # running, done, error
    stage = Column(String(60))                       # human-readable current step
    error = Column(Text)

    master_name = Column(String(500))                # master file / sheet it matched against
    brand = Column(String(255))
    sources_json = Column(Text, default="[]")        # image sources (uploads + links)

    total_images = Column(Integer, default=0)
    matched_count = Column(Integer, default=0)
    review_count = Column(Integer, default=0)
    folder_count = Column(Integer, default=0)
    group_count = Column(Integer, default=0)         # item groups in the master file

    work_dir = Column(String(1000))                  # on-disk source images for this run
    groups_json = Column(Text, default="[]")         # the master file's item groups
    results_json = Column(Text, default="[]")        # per-image match results
    approved_json = Column(Text, default="[]")       # item group codes signed off in QC

    # A run can be handed to a colleague to finish the quality check. The
    # assignee gets the same edit rights as the owner; only the owner can
    # reassign or delete it.
    assigned_to_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                            nullable=True, index=True)
    # Catalogue pages are reference-only by default; set when the user wants
    # them named and included in the download too.
    export_catalog = Column(Boolean, default=False)

    def _json(self, field: str, default):
        try:
            return json.loads(getattr(self, field) or default)
        except (TypeError, ValueError):
            return json.loads(default)

    @property
    def results(self) -> list:
        return self._json("results_json", "[]")

    @results.setter
    def results(self, val: list):
        self.results_json = json.dumps(val or [])

    @property
    def groups(self) -> list:
        return self._json("groups_json", "[]")

    @groups.setter
    def groups(self, val: list):
        self.groups_json = json.dumps(val or [])

    @property
    def sources(self) -> list:
        return self._json("sources_json", "[]")

    @sources.setter
    def sources(self, val: list):
        self.sources_json = json.dumps(val or [])

    @property
    def approved(self) -> list:
        return self._json("approved_json", "[]")

    @approved.setter
    def approved(self, val: list):
        self.approved_json = json.dumps(sorted(set(val or [])))


class CollectionJob(Base):
    """One supplier collection moving through the Operations OS.

    Created by the email intake (or a manual upload) and owns everything that
    belongs to one brand + season + supplier version: the original attachments,
    the intake analysis, and later the four SAP output packages and their
    approval state. Replaces the one-file-per-Session model for collection work
    — a Session is one spreadsheet, a CollectionJob is one collection.
    """
    __tablename__ = "collection_jobs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Identity of the collection.
    brand = Column(String(255), index=True)
    season = Column(String(50), index=True)
    supplier = Column(String(255))
    version = Column(Integer, default=1)          # supplier file version (V1, V2...)

    # Where it came from. The original email is preserved unchanged so an
    # intake can always be re-run against the source of record.
    source = Column(String(20), default="email")  # email, upload
    email_from = Column(String(320))
    email_subject = Column(Text)
    received_at = Column(DateTime, default=_utcnow)

    # Lifecycle. Kept deliberately short for the pilot; the full status model
    # (approval gates, SAP import, B2B) extends this list rather than replacing
    # it.
    status = Column(String(30), default="received", index=True)
    # received -> analysed -> needs_input -> ready -> error
    error = Column(Text)

    total_styles = Column(Integer, default=0)
    total_skus = Column(Integer, default=0)

    report_json = Column(Text, default="{}")      # the intake report (see core/intake.py)

    files = relationship("CollectionFile", back_populates="job",
                         cascade="all, delete-orphan",
                         order_by="CollectionFile.id")

    __table_args__ = (
        Index("ix_collection_jobs_user_status", "user_id", "status"),
    )

    @property
    def report(self) -> dict:
        try:
            return json.loads(self.report_json or "{}")
        except (TypeError, ValueError):
            return {}

    @report.setter
    def report(self, val: dict):
        self.report_json = json.dumps(val or {})

    @property
    def label(self) -> str:
        """Human name for the collection, e.g. 'Carhartt WIP SS27 (V2)'."""
        parts = [p for p in (self.brand, self.season) if p]
        name = " ".join(parts) or (self.email_subject or "Untitled collection")
        return f"{name} (V{self.version})" if (self.version or 1) > 1 else name


class CollectionFile(Base):
    """One attachment belonging to a CollectionJob, with its detected kind.

    The uploaded bytes are never modified — ``file_path`` points at the original
    attachment. When a supplier PDF line sheet is converted for parsing, the
    derived spreadsheet is recorded in ``parse_path`` and the PDF is kept.
    """
    __tablename__ = "collection_files"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("collection_jobs.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    filename = Column(String(500), nullable=False)   # original attachment name
    file_path = Column(String(1000), nullable=False)  # the untouched original
    parse_path = Column(String(1000))                 # derived .xlsx, if any
    kind = Column(String(20), default="other", index=True)
    file_size = Column(Integer)
    # Set when a human corrects the automatic classification, so the intake
    # rules can be reviewed against real supplier files later.
    kind_corrected = Column(Boolean, default=False)
    parse_error = Column(Text)
    # How the file had to be read when the default layout failed, e.g.
    # "header row 6, sheet(s): ACL FW26". Shown so a wrong guess is correctable.
    parse_note = Column(Text)

    job = relationship("CollectionJob", back_populates="files")
