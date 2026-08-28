# -*- coding: utf-8 -*-
"""Фасад пайплайна: единственная точка входа портала в agent/.

Правило: портал не знает про pymupdf, agent/ не знает про портал.
Здесь только обёртки над существующими модулями — никакой новой логики
разбора. CLI `python -m agent.run` продолжает работать как раньше.

Сейчас реализована приёмка (`intake`): состав комплекта, номенклатура тома
и флаги готовности к сверке. Сверка (`reconcile`) остаётся в `agent.run`
и переезжает сюда на днях 2-3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pymupdf as fitz

from .extract import classify_pages, parse_spec
from .match import canon_mark, page_multiplier
from .cable_journal import journal_pages
from .measure import detect_scale

LIGHTING_TITLE = 'Ведомость осветительного'

# человеческие названия типов листов — их же показывает портал
KIND_LABELS = {
    'plan': 'планы',
    'schema': 'схемы',
    'spec': 'спецификация',
    'vt': 'ведомости',
    'general': 'общие данные',
    'appendix': 'приложения',
    'cover': 'титульные',
    'other': 'прочее',
}
KIND_ORDER = ['plan', 'schema', 'spec', 'vt', 'general', 'appendix', 'cover', 'other']

# стадии разбора — текст показывается эксперту как есть
S_CLASSIFY = 'классификация листов'
S_SPEC = 'разбор спецификации'
S_CAPS = 'проверка готовности'


@dataclass
class Sheet:
    page: int
    kind: str
    code: str | None
    title: str | None
    mult: int


@dataclass
class SpecRow:
    page: int
    pos: str
    name: str
    mark: str
    canon: str
    unit: str
    qty: float | None
    qty_raw: str
    section: str
    category: str
    note: str
    excluded: bool
    composite: bool = False
    component_of: str = ''
    expanded_range: bool = False


@dataclass
class Capabilities:
    """Ответ на вопрос «насколько машине можно верить по этому тому»."""
    has_spec: bool = False              # без неё сверка невозможна вообще
    has_cable_journal: bool = False     # .КЖ -> точный метраж кабеля
    has_lighting_list: bool = False     # ведомость освещения -> точный счёт светильников
    has_vector_geometry: bool = False   # Revit 1:1 -> измерение труб возможно
    rotated_spec: bool = False
    unreadable_font_pages: list[int] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


@dataclass
class IntakeResult:
    pages_total: int
    sheets: list[Sheet]
    spec: list[SpecRow]
    capabilities: Capabilities
    kind_counts: dict

    def as_dict(self):
        return {
            'pages_total': self.pages_total,
            'kind_counts': self.kind_counts,
            'capabilities': self.capabilities.as_dict(),
            'spec_rows': len(self.spec),
        }


def _noop(*_a, **_kw):
    pass


class _Progress:
    """Приводит колбэки модулей к виду progress(stage, done, total)."""

    def __init__(self, cb):
        self.cb = cb or _noop

    def stage(self, name):
        self._name = name
        return self

    def __call__(self, done, total):
        self.cb(self._name, done, total)

    def tick(self, name, done, total):
        self.cb(name, done, total)


def _unreadable_pages(doc, progress):
    """Листы, у которых текст извлекается не полностью (нечитаемые шрифты).

    Три признака, любого достаточно: предупреждение mupdf при извлечении
    текста («unknown cid font»), символы-заменители в тексте, и пустой
    текстовый слой на листе — скан или шрифт, который не разбирается.
    Такие листы эксперт обязан посмотреть глазами, и портал обязан сказать
    об этом вслух, а не молча выдать «нет на чертежах».
    """
    tools = getattr(fitz, 'TOOLS', None)
    warn = getattr(tools, 'mupdf_warnings', None) if tools else None
    bad = []
    total = len(doc)
    for pno in range(total):
        progress.tick(S_CAPS, pno, total)
        if warn:
            try:
                warn(reset=True)
            except TypeError:
                warn()
        text = doc[pno].get_text()
        msg = ''
        if warn:
            try:
                msg = warn(reset=True) or ''
            except TypeError:
                msg = warn() or ''
        if (re.search(r'font|cid|glyph', msg, re.I)
                or text.count('�') > 5
                or len(text.strip()) < 30):
            bad.append(pno + 1)
    return bad


def _has_vector_geometry(doc, plan_pages, sample=3):
    """Масштаб определяется по размерным цепочкам -> геометрию можно измерять."""
    for pi in plan_pages[:sample]:
        try:
            if detect_scale(doc[pi['page'] - 1]):
                return True
        except Exception:
            continue
    return False


def intake(pdf, progress=None) -> IntakeResult:
    """Приёмка тома: состав листов, спецификация, флаги готовности.

    pdf: путь к файлу или открытый документ. LLM не используется,
    результат детерминирован. Сверка здесь не выполняется — она дороже
    на порядок и запускается отдельно.

    progress(stage, done, total) — необязательный колбэк хода работы.
    """
    p = _Progress(progress)
    own = not hasattr(pdf, 'load_page')
    doc = fitz.open(str(pdf)) if own else pdf
    try:
        pages = classify_pages(doc, progress=p.stage(S_CLASSIFY))
        sheets = [Sheet(page=pi['page'], kind=pi['kind'],
                        code=(pi['codes'][0] if pi.get('codes') else None),
                        title=pi.get('title') or None,
                        mult=page_multiplier(pi))
                  for pi in pages]

        spec_pages = [pi['page'] for pi in pages if pi['kind'] == 'spec']
        items = parse_spec(doc, spec_pages, progress=p.stage(S_SPEC)) if spec_pages else []
        spec = []
        for it in items:
            mark = (it.get('mark') or '').strip()
            spec.append(SpecRow(
                page=it['page'], pos=(it.get('pos') or '').strip(),
                name=(it.get('name') or '').strip(), mark=mark,
                canon=canon_mark(mark) if mark else '',
                unit=(it.get('unit') or '').strip(), qty=it.get('qty'),
                qty_raw=(it.get('qty_raw') or '').strip(),
                section=(it.get('section') or '').strip(),
                category=(it.get('category') or '').strip(),
                note=(it.get('note') or '').strip(),
                excluded=bool(it.get('excluded')),
                composite=bool(it.get('composite')),
                component_of=(it.get('component_host') or it.get('component_of') or ''),
                expanded_range=len(it.get('mark_variants') or []) > 1,
            ))

        plan_pages = [pi for pi in pages if pi['kind'] == 'plan']
        drawings = plan_pages + [pi for pi in pages if pi['kind'] == 'schema']
        lighting = any(LIGHTING_TITLE in doc[pi['page'] - 1].get_text()
                       for pi in drawings)
        caps = Capabilities(
            has_spec=bool(spec_pages),
            has_cable_journal=bool(journal_pages(doc)),
            has_lighting_list=lighting,
            has_vector_geometry=_has_vector_geometry(doc, plan_pages),
            rotated_spec=any(doc[n - 1].rotation for n in spec_pages),
            unreadable_font_pages=_unreadable_pages(doc, p),
        )

        counts = {}
        for s in sheets:
            counts[s.kind] = counts.get(s.kind, 0) + 1
        kind_counts = {k: counts[k] for k in KIND_ORDER if k in counts}
        p.tick(S_CAPS, len(doc), len(doc))
        return IntakeResult(pages_total=len(doc), sheets=sheets, spec=spec,
                            capabilities=caps, kind_counts=kind_counts)
    finally:
        if own:
            doc.close()


def main():
    """Отладочный запуск: python -m agent.api <файл.pdf>"""
    import sys
    import json
    path = Path(sys.argv[1])
    last = ['']

    def show(stage, done, total):
        line = f'{stage}: {done}/{total}'
        if line != last[0]:
            print('\r' + line.ljust(60), end='', flush=True)
            last[0] = line

    r = intake(path, progress=show)
    print('\r' + ' ' * 60)
    print(json.dumps(r.as_dict(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
