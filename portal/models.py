# -*- coding: utf-8 -*-
"""Модель данных портала.

Всё с org_id и created_at с первого дня, даже пока организация одна:
дописывать тенанта в таблицы задним числом дороже, чем нести его сразу.
Подача (submission) отделена от объекта тоже сразу — вторая подача бюро
(«Изм. 2») это главный сценарий второй недели.
"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB, 'postgresql')

# статусы тома
NEW, QUEUED, RUNNING, DONE, ERROR = 'new', 'queued', 'running', 'done', 'error'


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class Org(Base, TimestampMixin):
    __tablename__ = 'org'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(40), default='pilot')
    limits: Mapped[dict] = mapped_column(Json, default=dict)


class Project(Base, TimestampMixin):
    __tablename__ = 'project'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey('org.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(300))
    code: Mapped[str] = mapped_column(String(80), default='')
    bureau: Mapped[str] = mapped_column(String(200), default='')
    submissions: Mapped[list['Submission']] = relationship(
        back_populates='project', cascade='all, delete-orphan',
        order_by='Submission.id')


class Submission(Base, TimestampMixin):
    __tablename__ = 'submission'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'),
                                            index=True)
    label: Mapped[str] = mapped_column(String(80), default='Первая подача')
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
    project: Mapped['Project'] = relationship(back_populates='submissions')
    documents: Mapped[list['Document']] = relationship(
        back_populates='submission', cascade='all, delete-orphan',
        order_by='Document.id')


class Document(Base, TimestampMixin):
    """Том PDF: файл, как его прислало бюро."""
    __tablename__ = 'document'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey('org.id', ondelete='CASCADE'), index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey('submission.id', ondelete='CASCADE'), index=True)
    filename: Mapped[str] = mapped_column(String(400))
    cipher: Mapped[str] = mapped_column(String(120), default='')       # ПР-01.24-1-ЭОМ
    section: Mapped[str] = mapped_column(String(40), default='')       # ЭОМ
    section_label: Mapped[str] = mapped_column(String(120), default='')
    revision: Mapped[str] = mapped_column(String(80), default='')      # Изм. 1-4
    file_key: Mapped[str] = mapped_column(String(500), default='')
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    pages_total: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=NEW, index=True)
    error: Mapped[str] = mapped_column(Text, default='')
    capabilities: Mapped[dict] = mapped_column(Json, default=dict)
    kind_counts: Mapped[dict] = mapped_column(Json, default=dict)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submission: Mapped['Submission'] = relationship(back_populates='documents')


class Sheet(Base):
    """Лист тома. kind_override — ручная правка эксперта (экран классификации)."""
    __tablename__ = 'sheet'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    page: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    kind_override: Mapped[str] = mapped_column(String(20), default='')
    code: Mapped[str] = mapped_column(String(120), default='')
    title: Mapped[str] = mapped_column(String(300), default='')
    mult: Mapped[int] = mapped_column(Integer, default=1)


class SpecItem(Base):
    """Строка спецификации тома."""
    __tablename__ = 'spec_item'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    page: Mapped[int] = mapped_column(Integer)
    pos: Mapped[str] = mapped_column(String(40), default='')
    name: Mapped[str] = mapped_column(Text, default='')
    mark: Mapped[str] = mapped_column(String(300), default='')
    canon_mark: Mapped[str] = mapped_column(String(300), default='', index=True)
    unit: Mapped[str] = mapped_column(String(20), default='')
    qty: Mapped[float | None] = mapped_column(Float)
    qty_raw: Mapped[str] = mapped_column(String(80), default='')
    section: Mapped[str] = mapped_column(String(80), default='')
    category: Mapped[str] = mapped_column(String(300), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    composite: Mapped[bool] = mapped_column(Boolean, default=False)
    component_of: Mapped[str] = mapped_column(String(120), default='')
    expanded_range: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(Base, TimestampMixin):
    """Прогон по тому. Прогресс живёт здесь, а не в Redis: перезапуск
    воркера не должен терять историю."""
    __tablename__ = 'run'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey('org.id', ondelete='CASCADE'), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    kind: Mapped[str] = mapped_column(String(20), default='intake')
    status: Mapped[str] = mapped_column(String(20), default=QUEUED)
    stage: Mapped[str] = mapped_column(String(80), default='')
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    percent: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(Json, default=dict)
    error: Mapped[str] = mapped_column(Text, default='')
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index('ix_spec_item_doc_mark', SpecItem.document_id, SpecItem.canon_mark)
Index('ix_sheet_doc_page', Sheet.document_id, Sheet.page)
