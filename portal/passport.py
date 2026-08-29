# -*- coding: utf-8 -*-
"""Контекст вкладки «Паспорт тома».

Веб только читает: разбор давно сделан воркером, здесь строки из БД
раскладываются в блоки экрана. Порядок блоков задаёт смысл — сначала
расхождения, потом факты.
"""
from sqlalchemy import select

from .models import (DeclaredSheet, DocRef, Document, NormRef, RevisionEntry,
                     Symbol)

LEVELS = {'red': 'r', 'amber': 'y', 'ok': 'g'}

NORM_LABELS = {
    'active': 'действует',
    'superseded': 'заменён',
    'cancelled': 'отменён',
    'verify': 'проверить',
    'unknown': 'нет в реестре',
}
NORM_LEVELS = {'active': 'g', 'superseded': 'y', 'cancelled': 'r',
               'verify': 'y', 'unknown': ''}

# сколько проверок сошлось — показываем свёрнутой строкой, чтобы тревожных
# отметок было ровно столько, сколько реальных проблем
CHECKS = ('нумерация ведомости', 'ссылочные документы', 'листы спецификации',
          'номера изменений', 'нормативная база', 'условные обозначения',
          'читаемость листов')


def context(session, doc: Document):
    """Всё, что показывает вкладка «Паспорт» по одному тому."""
    sheets = session.scalars(
        select(DeclaredSheet).where(DeclaredSheet.document_id == doc.id)
        .order_by(DeclaredSheet.no)).all()
    refs = session.scalars(
        select(DocRef).where(DocRef.document_id == doc.id)
        .order_by(DocRef.kind, DocRef.code)).all()
    norms = session.scalars(
        select(NormRef).where(NormRef.document_id == doc.id)
        .order_by(NormRef.code)).all()
    revisions = session.scalars(
        select(RevisionEntry).where(RevisionEntry.document_id == doc.id)
        .order_by(RevisionEntry.number, RevisionEntry.id)).all()
    symbols = session.scalars(
        select(Symbol).where(Symbol.document_id == doc.id)
        .order_by(Symbol.id)).all()

    findings = [dict(f, level_class=LEVELS.get(f.get('level'), ''))
                for f in (doc.findings or [])]
    gaps = {n for f in findings if f.get('code') == 'sheet_gap'
            for n in (f.get('sheets') or [])}

    rows = []
    prev = 0
    for s in sheets:
        while prev + 1 < s.no:               # дыры показываем явной строкой
            prev += 1
            rows.append({'no': prev, 'title': '', 'missing': True,
                         'revisions': [], 'mark': ''})
        rows.append({'no': s.no, 'title': s.title, 'missing': False,
                     'revisions': s.revisions or [], 'mark': s.mark})
        prev = s.no

    for n in norms:
        n.label = NORM_LABELS.get(n.status, n.status)
        n.level = NORM_LEVELS.get(n.status, '')

    return {
        'doc': doc,
        'findings': findings,
        'passed': len(CHECKS) - len(findings),
        'checks': CHECKS,
        'sheets': rows,
        'gaps': sorted(gaps),
        'refs': [r for r in refs if r.kind != 'volume'],
        'volumes': [r for r in refs if r.kind == 'volume'],
        'norms': norms,
        'problem_norms': [n for n in norms
                          if n.status in ('superseded', 'cancelled', 'verify')
                          and not n.contextual],
        'revisions': revisions,
        'symbols': symbols,
        'symbols_used': sum(1 for s in symbols if s.used),
    }
