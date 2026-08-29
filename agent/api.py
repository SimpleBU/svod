# -*- coding: utf-8 -*-
"""Фасад пайплайна: единственная точка входа портала в agent/.

Правило: портал не знает про pymupdf, agent/ не знает про портал.
Здесь только обёртки над существующими модулями — никакой новой логики
разбора. CLI `python -m agent.run` продолжает работать как раньше.

Реализованы:
  intake    — состав тома, номенклатура и флаги готовности (этап приёмки);
  passport  — паспорт тома по листу «Общие данные» и расхождения (этап 1);
  checkplan — план проверки: критичные позиции спецификации (этап 2).

Сверка (`reconcile`) остаётся в `agent.run` и переезжает сюда позже.
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
from .general import general as parse_general
from .norms import norms as parse_norms, problems as norm_problems
from .symbols import render as render_symbols
from .criticality import checkplan as build_checkplan, stats as checkplan_stats

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
S_GENERAL = 'разбор общих данных'
S_SYMBOLS = 'условные обозначения'

# уровни расхождений: портал раскрашивает их сам
RED, AMBER, OK = 'red', 'amber', 'ok'


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


# --------------------------------------------------------------- этап 1

@dataclass
class Finding:
    """Расхождение между тем, что бюро объявило, и тем, что лежит в файле."""
    code: str
    level: str
    text: str
    sheets: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


@dataclass
class PassportResult:
    sheets: list = field(default_factory=list)        # ведомость рабочих чертежей
    refs: list = field(default_factory=list)          # ссылочные и прилагаемые
    volumes: list = field(default_factory=list)       # комплекты раздела
    symbols: list = field(default_factory=list)       # строки таблицы УГО
    symbol_images: list = field(default_factory=list)  # их картинки, если считали
    revisions: list = field(default_factory=list)     # регистрация изменений
    norms: list = field(default_factory=list)         # нормативная база
    findings: list = field(default_factory=list)      # расхождения
    general_pages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def as_dict(self):
        return {
            'sheets': len(self.sheets),
            'refs': len(self.refs),
            'volumes': len(self.volumes),
            'symbols': len(self.symbols),
            'revisions': len(self.revisions),
            'norms': len(self.norms),
            'findings': [f.as_dict() for f in self.findings],
            'warnings': self.warnings,
        }


def _sheet_gaps(gen):
    nos = [s.no for s in gen.sheets if s.no]
    if not nos:
        return []
    have = set(nos)
    return [n for n in range(1, max(nos) + 1) if n not in have]


def _findings(gen, res, norms_list, filename='', submission_codes=()):
    """Расхождения между объявленным и фактическим.

    Правило то же, что во флагах готовности: тревожная отметка ставится
    там, где есть настоящая проблема. Всё, что сошлось, сюда не попадает —
    портал показывает это отдельной свёрнутой строкой.
    """
    out = []
    gaps = _sheet_gaps(gen)
    if gaps:
        out.append(Finding('sheet_gap', RED,
                           'В ведомости чертежей пропущены листы: '
                           + ', '.join(str(g) for g in gaps), gaps))

    # шифры подачи плюс шифры, встреченные внутри самого тома: спецификацию
    # и кабельный журнал бюро часто подшивает в тот же файл
    known = {_norm_code(c) for c in submission_codes if c}
    known |= {_norm_code(s.code) for s in res.sheets if s.code}
    known.discard('')
    for d in gen.refs:
        if known and not _present(_norm_code(d.code), known):
            tail = f' на {d.sheets_declared} листах' if d.sheets_declared else ''
            out.append(Finding('ref_missing', RED,
                               f'Объявлен {d.code} «{d.title}»{tail} — '
                               'в подаче такого файла нет', [d.src_page]))

    spec_pages = res.kind_counts.get('spec', 0)
    for d in gen.refs:
        if d.code.upper().endswith('.СО') or '.СО(' in d.code.upper():
            if d.sheets_declared and spec_pages and d.sheets_declared != spec_pages:
                out.append(Finding(
                    'spec_sheets', AMBER,
                    f'Спецификация {d.code} объявлена на {d.sheets_declared} листах, '
                    f'разобрано {spec_pages}', [d.src_page]))

    declared = _filename_revisions(filename)
    registered = {e.number for e in gen.revisions if e.number}
    if declared and registered and declared - registered:
        out.append(Finding(
            'revision_mismatch', AMBER,
            'В имени файла заявлены изменения '
            + ', '.join(str(n) for n in sorted(declared))
            + ', в томе зарегистрированы '
            + ', '.join(str(n) for n in sorted(registered)), []))

    for n in norm_problems(norms_list):
        tail = f' — заменён на {n.replaced_by}' if n.replaced_by else (
            f' — {n.note}' if n.note else '')
        out.append(Finding('norm', AMBER, f'{n.code}{tail}', []))

    # одна строка на все обозначения, а не по строке на каждое: шесть жёлтых
    # отметок подряд перестают читаться как проблема
    flat_marks = [_flat(r.mark) for r in res.spec if (r.mark or '').strip()]
    names = ' '.join(r.name for r in res.spec).lower()
    named = [s for s in gen.symbols
             if len((s.code or '').strip()) >= 3 and re.search(r'\w', s.code or '')]
    unused = []
    for s in named:
        flat = _flat(s.code)
        hit = any(flat and flat in m for m in flat_marks) or s.code.strip().lower() in names
        if not hit:
            unused.append(s)
    # проверка осмысленна только у легенды оборудования: если с номенклатурой
    # не сошлось вообще ничего, это легенда другого рода — обозначения систем
    # (В1, Т3.1) в спецификации не встречаются по своей природе
    if unused and len(named) - len(unused) >= 2:
        codes = ', '.join(s.code.strip() for s in unused[:8])
        tail = ' и ещё ' + str(len(unused) - 8) if len(unused) > 8 else ''
        out.append(Finding(
            'symbol_unused', AMBER,
            f'{len(unused)} обозначений из легенды не встречаются в спецификации '
            f'тома: {codes}{tail}', sorted({s.page for s in unused})))

    bad = res.capabilities.unreadable_font_pages
    if bad:
        out.append(Finding('unreadable', RED,
                           f'Нечитаемых листов: {len(bad)} — смотреть глазами', bad))
    return out


def _flat(code):
    return re.sub(r'[\s\-–—_.,()"«»/]', '', (code or '')).lower().replace('ё', 'е')


def _present(code, known):
    """Шифр считается сданным только при точном совпадении.

    Сравнение «по началу» было бы удобнее, но делает проверку бесполезной:
    шифр тома ПР-01/24-3-СПСиА является началом шифров всех его приложений,
    и объявленный, но не сданный .АЛ1 считался бы присутствующим.
    """
    return bool(code) and code in known


def _norm_code(code):
    """«ПР-01/24-3-СПСиА.СО(л. 1-3)» и «ПР-01/24-3-СПСиА.СО» — один документ.

    Хвост в скобках указывает листы, а не другой шифр, и сравнению мешает.
    """
    code = re.sub(r'\([^)]*\)', ' ', code or '')
    return re.sub(r'[\s\-–—_.()/]', '', code).upper()


def _filename_revisions(filename):
    """Номера изменений из имени файла: «ПР-01.24-1-ЭОМ (Изм. 1-4).pdf» -> {1,2,3,4}.

    Берём числа только из блока «Изм. …», а не из всего имени: в шифре тома
    цифр не меньше, и без этого проверка сравнивает номер договора с номером
    изменения.
    """
    m = re.search(r'изм[.\s№]*([\d\s,;и\-–]+)', filename or '', re.I)
    if not m:
        return set()
    out = set()
    for part in re.split(r'[,;\s]|и', m.group(1)):
        part = part.strip()
        if not part:
            continue
        rng = re.fullmatch(r'(\d+)\s*[-–]\s*(\d+)', part)
        if rng:
            out.update(range(int(rng.group(1)), int(rng.group(2)) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def passport(pdf, res=None, filename='', submission_codes=(), with_images=True,
             progress=None) -> PassportResult:
    """Паспорт тома: что бюро объявило в общих данных и что с этим не так.

    res — результат intake(), если он уже посчитан вызывающим: тогда листы
    не классифицируются второй раз. filename и submission_codes нужны для
    проверок «ревизия из имени файла» и «объявлено — в подаче нет».
    """
    p = _Progress(progress)
    own = not hasattr(pdf, 'load_page')
    doc = fitz.open(str(pdf)) if own else pdf
    try:
        if res is None:
            res = intake(doc, progress=progress)
        pages = [{'page': s.page, 'kind': s.kind} for s in res.sheets]
        gen = parse_general(doc, pages=pages, progress=p.stage(S_GENERAL))
        images = []
        if with_images and gen.symbols:
            images = render_symbols(doc, gen.symbols, progress=p.stage(S_SYMBOLS))
        norms_list = parse_norms(gen.guidelines_text, gen.refs)
        return PassportResult(
            sheets=gen.sheets, refs=gen.refs, volumes=gen.volumes,
            symbols=gen.symbols, symbol_images=images, revisions=gen.revisions,
            norms=norms_list,
            findings=_findings(gen, res, norms_list, filename, submission_codes),
            general_pages=gen.general_pages, warnings=gen.warnings)
    finally:
        if own:
            doc.close()


# --------------------------------------------------------------- этап 2

def checkplan(res: IntakeResult, psp: PassportResult):
    """План проверки по уже разобранному тому.

    PDF не открывается: пересчёт после правки порогов стоит доли секунды,
    а не двадцать минут повторного разбора.
    """
    return build_checkplan(res.spec, psp.revisions, psp.symbols,
                           res.capabilities.as_dict(), res.kind_counts)


def main():
    """Отладочный запуск: python -m agent.api <файл.pdf>"""
    import sys
    from pathlib import Path
    path = Path(sys.argv[1])
    last = ['']

    def show(stage, done, total):
        line = f'{stage}: {done}/{total}'
        if line != last[0]:
            print('\r' + line.ljust(60), end='', flush=True)
            last[0] = line

    res = intake(path, progress=show)
    psp = passport(path, res=res, filename=path.name, progress=show)
    rows = checkplan(res, psp)
    print('\r' + ' ' * 60)

    print(f'{path.name}: {res.pages_total} страниц, {len(res.spec)} строк спецификации')
    print('  листы:', ', '.join(f'{KIND_LABELS.get(k, k)} {v}'
                                for k, v in res.kind_counts.items()))
    print(f'  ведомость чертежей: {len(psp.sheets)} · ссылочных и прилагаемых: '
          f'{len(psp.refs)} · комплектов раздела: {len(psp.volumes)} · '
          f'УГО: {len(psp.symbols)} · изменений: {len(psp.revisions)} · '
          f'нормативов: {len(psp.norms)}')
    print('  расхождения:' if psp.findings else '  расхождений нет')
    for f in psp.findings:
        print(f'    [{f.level}] {f.text}')
    s = checkplan_stats(rows)
    print(f"  план проверки: предложено {s['proposed']} из {s['total']} "
          f"({100 * s['proposed'] // max(1, s['total'])} %)")
    for row in rows[:10]:
        why = ' · '.join(x.text for x in row.reasons) or '—'
        print(f'    {row.cls} {row.score:>3}  {row.name[:36]:36} {row.mark[:20]:20} '
              f'{str(row.qty or ""):>8} {row.unit:5} | {why}')


if __name__ == '__main__':
    main()
