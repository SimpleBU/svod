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

# статусы плана проверки
DRAFT, FROZEN = 'draft', 'frozen'

# решение эксперта по позиции: не трогал / беру / снимаю
AUTO, TAKE, SKIP = 'auto', 'take', 'skip'


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
    # расхождения между объявленным и фактическим: производны от разбора,
    # поэтому лежат json-ом рядом с томом, а не отдельной таблицей
    findings: Mapped[dict] = mapped_column(Json, default=list)
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


class DeclaredSheet(Base):
    """Строка ведомости рабочих чертежей: что бюро объявило в томе."""
    __tablename__ = 'declared_sheet'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text, default='')
    revisions: Mapped[list] = mapped_column(Json, default=list)
    mark: Mapped[str] = mapped_column(String(20), default='')   # Зам. / Нов. / -
    src_page: Mapped[int] = mapped_column(Integer, default=0)


class DocRef(Base):
    """Ссылочный, прилагаемый документ или соседний комплект раздела."""
    __tablename__ = 'doc_ref'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    kind: Mapped[str] = mapped_column(String(20))     # referenced|attached|volume
    code: Mapped[str] = mapped_column(String(200), default='')
    title: Mapped[str] = mapped_column(Text, default='')
    sheets_declared: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default='')
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    src_page: Mapped[int] = mapped_column(Integer, default=0)


class NormRef(Base):
    """Норматив из общих указаний со статусом из реестра."""
    __tablename__ = 'norm_ref'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    code: Mapped[str] = mapped_column(String(120), default='')
    title: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), default='unknown')
    replaced_by: Mapped[str] = mapped_column(String(300), default='')
    note: Mapped[str] = mapped_column(Text, default='')
    contextual: Mapped[bool] = mapped_column(Boolean, default=False)
    sources: Mapped[list] = mapped_column(Json, default=list)


class Symbol(Base):
    """Строка таблицы условных обозначений: подпись и картинка символа."""
    __tablename__ = 'symbol'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    name: Mapped[str] = mapped_column(Text, default='')
    code: Mapped[str] = mapped_column(String(60), default='')
    page: Mapped[int] = mapped_column(Integer, default=0)
    image_key: Mapped[str] = mapped_column(String(500), default='')
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)   # марка есть в спецификации


class RevisionEntry(Base):
    """Строка листа регистрации изменений."""
    __tablename__ = 'revision_entry'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    number: Mapped[int | None] = mapped_column(Integer)
    sheets: Mapped[str] = mapped_column(Text, default='')
    content: Mapped[str] = mapped_column(Text, default='')
    doc_code: Mapped[str] = mapped_column(String(200), default='')
    basis: Mapped[str] = mapped_column(Text, default='')
    src_page: Mapped[int] = mapped_column(Integer, default=0)


class CheckPlan(Base, TimestampMixin):
    """Версия плана проверки по тому.

    Черновик правится галочками; зафиксированный только читается — по нему
    и запускается сверка, чтобы замечание можно было привязать к версии.
    """
    __tablename__ = 'check_plan'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey('org.id', ondelete='CASCADE'), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('document.id', ondelete='CASCADE'),
                                             index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default=DRAFT, index=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict] = mapped_column(Json, default=dict)


class CheckItem(Base):
    """Позиция плана проверки.

    `cls` пишет машина, `decision` — эксперт. Разделение осознанное: булев
    флаг «включено» либо затирался бы прогоном, либо не давал машине ничего
    предлагать.
    """
    __tablename__ = 'check_item'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('check_plan.id', ondelete='CASCADE'),
                                         index=True)
    spec_item_id: Mapped[int | None] = mapped_column(
        ForeignKey('spec_item.id', ondelete='SET NULL'))
    key: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(20), default='spec')   # spec|manual
    pos: Mapped[str] = mapped_column(String(40), default='')
    name: Mapped[str] = mapped_column(Text, default='')
    mark: Mapped[str] = mapped_column(String(300), default='')
    unit: Mapped[str] = mapped_column(String(20), default='')
    qty: Mapped[float | None] = mapped_column(Float)
    page: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[int] = mapped_column(Integer, default=0)
    cls: Mapped[str] = mapped_column(String(2), default='C', index=True)
    reasons: Mapped[list] = mapped_column(Json, default=list)
    verifiable_by: Mapped[list] = mapped_column(Json, default=list)
    evidence: Mapped[list] = mapped_column(Json, default=list)
    decision: Mapped[str] = mapped_column(String(10), default=AUTO)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default='')

    @property
    def included(self):
        """Что реально пойдёт в проверку: решение эксперта, иначе класс A."""
        if self.decision == TAKE:
            return True
        if self.decision == SKIP:
            return False
        return self.cls == 'A'

    @property
    def origin(self):
        """Откуда взялась галочка — это и есть метрика качества модели."""
        if self.decision == TAKE:
            return 'взято вручную'
        if self.decision == SKIP:
            return 'снято экспертом'
        return 'предложено машиной' if self.cls == 'A' else ''


class CheckRule(Base, TimestampMixin):
    """Решение эксперта, перенесённое на уровень объекта.

    Когда бюро присылает «Изм. 2», отбор не начинается с нуля: решения
    переносятся по ключу позиции с пометкой, что это наследство.
    """
    __tablename__ = 'check_rule'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('project.id', ondelete='CASCADE'),
                                            index=True)
    key: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str] = mapped_column(String(10), default=AUTO)
    comment: Mapped[str] = mapped_column(Text, default='')
    from_submission_id: Mapped[int | None] = mapped_column(Integer)


Index('ix_spec_item_doc_mark', SpecItem.document_id, SpecItem.canon_mark)
Index('ix_sheet_doc_page', Sheet.document_id, Sheet.page)
Index('ix_check_item_plan_cls', CheckItem.plan_id, CheckItem.cls)
Index('ix_check_rule_project_key', CheckRule.project_id, CheckRule.key, unique=True)
