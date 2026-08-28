# -*- coding: utf-8 -*-
"""Сводная номенклатура объекта.

Собирается из строк спецификаций всех томов подачи. Три вещи, которые
эксперт ищет руками и которые видно сразу: дубли между разделами,
позиции без марки (машинной сверке не поддаются) и строки, исключённые
изменением.
"""
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from .models import Document, SpecItem

NO_MARK = 'без марки'
DUPLICATE = 'дубль между разделами'
EXCLUDED = 'исключено изменением'
RANGE = 'раскрыт диапазон обозначений'
COMPONENT = 'комплектующее изделия'

FLAG_LEVEL = {NO_MARK: 'y', DUPLICATE: 'y', EXCLUDED: 'n', RANGE: 'n', COMPONENT: 'n'}


@dataclass
class Row:
    key: tuple
    mark: str
    name: str
    unit: str
    qty: float
    sections: list = field(default_factory=list)
    documents: set = field(default_factory=set)
    pages: set = field(default_factory=set)
    flags: list = field(default_factory=list)
    excluded: bool = False

    @property
    def has_mark(self):
        return bool(self.mark)

    @property
    def qty_display(self):
        q = self.qty
        if q is None:
            return ''
        return str(int(q)) if abs(q - round(q)) < 1e-6 else f'{q:.2f}'.rstrip('0').rstrip('.')


def _norm_name(name):
    return re.sub(r'\s+', ' ', (name or '')).strip().lower()[:120]


def collect(session, submission_id):
    """-> (rows, totals). Одна выборка на подачу, группировка в памяти:
    строк спецификации на объект — тысячи, не миллионы."""
    q = (select(SpecItem, Document.section, Document.section_label, Document.id)
         .join(Document, Document.id == SpecItem.document_id)
         .where(Document.submission_id == submission_id)
         .order_by(SpecItem.document_id, SpecItem.page, SpecItem.id))
    groups = {}
    mark_sections = {}
    for item, section, section_label, doc_id in session.execute(q):
        # Ключ — марка И наименование. Одной марки мало: в колонке марки
        # часто стоит стандарт («ГОСТ 8946-75», «EN 877», «PPR»), общий для
        # сотни разных изделий. Сложить их в одну строку — как раз то ложное
        # «всё сходится», из-за которого порталу перестают верить.
        key = (item.canon_mark or '', _norm_name(item.name))
        r = groups.get(key)
        if r is None:
            r = groups[key] = Row(key=key, mark=item.mark or '', name=item.name or '',
                                  unit=item.unit or '', qty=0.0, excluded=True)
        if not r.mark and item.mark:
            r.mark = item.mark
        if len(item.name or '') > len(r.name):
            r.name = item.name or ''
        if not r.unit:
            r.unit = item.unit or ''
        if not item.excluded:
            r.excluded = False
            r.qty += item.qty or 0.0
        sec = section or '—'
        if sec not in r.sections:
            r.sections.append(sec)
        r.documents.add(doc_id)
        r.pages.add(item.page)
        if item.canon_mark:
            mark_sections.setdefault(item.canon_mark, set()).add(sec)
        if item.expanded_range and RANGE not in r.flags:
            r.flags.append(RANGE)
        if item.component_of and COMPONENT not in r.flags:
            r.flags.append(COMPONENT)

    rows = list(groups.values())
    for r in rows:
        if not r.mark:
            r.flags.insert(0, NO_MARK)
        # дубль ищем по марке целиком, а не по строке: в двух разделах одно
        # и то же изделие часто названо чуть по-разному
        if len(r.sections) > 1 or len(mark_sections.get(r.key[0], ())) > 1:
            r.flags.insert(0, DUPLICATE)
        if r.excluded:
            r.flags.insert(0, EXCLUDED)
        r.sections.sort()
    rows.sort(key=lambda r: ((not r.has_mark), (r.mark or r.name).lower()))

    totals = {
        'total': len(rows),
        'no_mark': sum(1 for r in rows if not r.has_mark),
        'duplicates': sum(1 for r in rows if DUPLICATE in r.flags),
        'excluded': sum(1 for r in rows if r.excluded),
    }
    return rows, totals


def filtered(rows, q='', section='', flagged=False):
    q = (q or '').strip().lower()
    out = []
    for r in rows:
        if section and section not in r.sections:
            continue
        if flagged and not any(FLAG_LEVEL.get(f) == 'y' for f in r.flags):
            continue
        if q and q not in (r.mark or '').lower() and q not in (r.name or '').lower():
            continue
        out.append(r)
    return out
