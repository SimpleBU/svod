# -*- coding: utf-8 -*-
"""Единый поток находок по тому.

До этого расхождения жили в трёх местах: расхождения состава — на вкладке
паспорта, расхождения количеств — в сверке, непроверяемые машиной позиции —
в плане проверки. Эксперт собирал картину сам и нигде не видел ответа на
вопрос «я закончил?».

Здесь они складываются в один список с общим уровнем и общим решением.
Решение хранится там же, где и раньше — в `remark`, по устойчивому ключу,
поэтому ничего не переносится и не дублируется.
"""
from dataclasses import dataclass, field

from . import checkplan as plan_service
from . import match as match_service, remarks as remark_service
from .models import Remark

LEVEL_ORDER = {'red': 0, 'amber': 1, 'grey': 2}
SOURCE_LABELS = {'match': 'сверка с чертежами', 'passport': 'паспорт тома',
                 'sheet': 'метка на листе'}


@dataclass
class Finding:
    source: str                 # match | passport | sheet
    key: str
    level: str                  # red | amber | grey
    title: str
    summary: str
    document_id: int
    pages: list = field(default_factory=list)
    remark: Remark | None = None
    item: object = None         # MatchItem, если находка из сверки
    raw: dict | None = None     # расхождение паспорта как есть
    # почему позиция вообще попала в проверку — из плана проверки, чтобы
    # эксперт не ходил за основанием на соседнюю вкладку
    reasons: list = field(default_factory=list)
    quote: str = ''

    @property
    def decided(self):
        return self.remark is not None

    @property
    def verdict(self):
        if self.remark is None:
            return ''
        return self.remark.status

    @property
    def source_label(self):
        return SOURCE_LABELS.get(self.source, self.source)


def _match_summary(i):
    unit = i.unit or 'шт.'
    parts = [f'по спецификации {i.spec_qty:g} {unit}' if i.spec_qty is not None else '']
    if i.plan_qty is not None:
        parts.append(f'на планах {i.plan_qty:g}')
    if i.schema_qty:
        parts.append(f'на схемах {i.schema_qty:g}')
    return ' · '.join(p for p in parts if p)


def _plan_by_key(session, doc):
    """Позиции плана проверки по ключу — одним запросом, а не по строке."""
    plan = plan_service.current_plan(session, doc.id)
    if plan is None:
        return {}
    return {i.key: i for i in plan_service.items(session, plan.id)}


def collect(session, doc):
    """Находки одного тома: сверка + паспорт, уже с решениями эксперта."""
    known = remark_service.by_key(session, doc.id)
    plan = _plan_by_key(session, doc)
    out = []

    for i in match_service.items(session, doc.id):
        if i.level not in ('red', 'amber'):
            continue
        key = remark_service.match_key(i)
        reasons, quote = [], ''
        for k in (i.keys or []):
            ci = plan.get(k)
            if ci is None:
                continue
            for r in (ci.reasons or []):
                if r not in reasons:
                    reasons.append(r)
            quote = quote or next((e.get('text') for e in (ci.evidence or [])
                                   if e.get('kind') == 'revision' and e.get('text')), '')
        out.append(Finding(
            source='match', key=key, level=i.level,
            title=' · '.join(i.marks or []) or i.mark or '—',
            summary=match_service.status_label(i.status) + ' · ' + _match_summary(i),
            document_id=doc.id,
            pages=list(i.plan_pages or []) + list(i.schema_pages or []),
            remark=known.get(key), item=i, reasons=reasons, quote=quote))

    for n, f in enumerate(doc.findings or []):
        key = remark_service.passport_key(f)
        out.append(Finding(
            source='passport', key=key, level=f.get('level', 'red'),
            title=remark_service.PASSPORT_TITLES.get(f.get('code', ''), 'Расхождение состава'),
            summary=f.get('text', ''), document_id=doc.id,
            pages=list(f.get('sheets') or []),
            remark=known.get(key), raw=dict(f, index=n)))

    # без решения — вперёд: это и есть очередь работы
    out.sort(key=lambda x: (x.decided, LEVEL_ORDER.get(x.level, 3), x.title))
    return out


def stats(rows):
    open_rows = [f for f in rows if not f.decided]
    return {
        'total': len(rows),
        'open': len(open_rows),
        'decided': len(rows) - len(open_rows),
        'red': sum(1 for f in open_rows if f.level == 'red'),
        'amber': sum(1 for f in open_rows if f.level == 'amber'),
        'remarks': sum(1 for f in rows
                       if f.remark is not None and f.remark.status != 'dismissed'),
        'percent': round((len(rows) - len(open_rows)) * 100 / len(rows)) if rows else 0,
    }


def submission_stats(session, documents):
    """Сводка приёмки по всей подаче — ответ на «сколько работы осталось»."""
    total = {'total': 0, 'open': 0, 'decided': 0, 'red': 0, 'amber': 0,
             'remarks': 0, 'uncheckable': 0}
    for d in documents:
        st = stats(collect(session, d))
        for k in ('total', 'open', 'decided', 'red', 'amber', 'remarks'):
            total[k] += st[k]
        total['uncheckable'] += (d.match_stats or {}).get('uncheckable', 0)
    total['percent'] = (round(total['decided'] * 100 / total['total'])
                        if total['total'] else 0)
    return total

FILTERS = {
    'open': ('без решения', lambda f: not f.decided),
    'red': ('критично', lambda f: f.level == 'red' and not f.decided),
    'amber': ('на усмотрение', lambda f: f.level == 'amber' and not f.decided),
    'decided': ('решённые', lambda f: f.decided),
}


def filtered(rows, flt=''):
    fn = FILTERS.get(flt, (None, None))[1]
    return [f for f in rows if fn(f)] if fn else rows


def by_key(rows, key):
    for f in rows:
        if f.key == key:
            return f
    return rows[0] if rows else None


def proposed_text(finding):
    """Формулировка, которую портал предлагает до решения эксперта."""
    if finding.source == 'match' and finding.item is not None:
        return remark_service.match_text(finding.item)
    if finding.raw is not None:
        return finding.raw.get('text', '')
    return ''


def context(session, doc, flt='', key=''):
    """Всё, что показывает вкладка «Приёмка» по одному тому."""
    rows = collect(session, doc)
    shown = filtered(rows, flt)
    current = by_key(shown, key) if shown else None
    groups = []
    for source in ('match', 'passport'):
        part = [f for f in shown if f.source == source]
        if part:
            groups.append((SOURCE_LABELS[source], part))
    return {
        'doc': doc, 'rows': rows, 'shown': shown, 'groups': groups,
        'current': current, 'flt': flt,
        'stats': stats(rows), 'filters': FILTERS,
        'text': proposed_text(current) if current is not None else '',
        'position': (shown.index(current) + 1) if current in shown else 0,
        'uncheckable': (doc.match_stats or {}).get('uncheckable', 0),
    }
