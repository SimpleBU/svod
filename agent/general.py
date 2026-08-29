# -*- coding: utf-8 -*-
"""Разбор листа «Общие данные»: что бюро само объявило в томе.

Лист общих данных — машиночитаемый паспорт тома. Бюро перечисляет там ведомость
рабочих чертежей, соседние тома раздела, ссылочные и прилагаемые документы,
нормативную базу и условные обозначения. Всё объявленное можно немедленно
сверить с фактическим файлом — это первые замечания, не требующие ни сверки
планов, ни LLM.

Разбор построен на `page.find_tables()`: ведомости разлинованы, и табличный
поиск берёт их вместе с повёрнутыми листами. Нужные таблицы опознаются
по строке заголовка и по данным колонок, а не по подписи над таблицей:
подпись у бюро набрана как попало («Ведомоть ссылочных»), а колонка
«Наименование» в одном томе называется «Обоснование».

Проверено на пяти реальных томах: ЭОМ, СПСиА, ПРК, ОВ1, АСУД.
LLM не используется, результат детерминирован.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import pymupdf as fitz

# --------------------------------------------------------------- сигнатуры

# синонимы заголовков колонок: первый вариант — каноническое имя
COL_SHEET = ('лист',)
COL_CODE = ('обозначение',)
COL_NAME = ('наименование', 'обоснование')
COL_NOTE = ('примечание',)

GROUP_ATTACHED = 'прилагаем'
GROUP_REFERENCED = 'ссылочн'

REFERENCED, ATTACHED, VOLUME = 'referenced', 'attached', 'volume'

# суффиксы отдельных документов комплекта: .КЖ, .СО, .АЛ1, .РР2, .ЗД1
DOC_SUFFIX = re.compile(r'\.[А-ЯЁ]{2}\d*(?:\(|$|\s)')

REV_NUMS = re.compile(r'\d+')
REV_MARK = re.compile(r'\((Зам\.|Нов\.|-)\)')
SHEETS_COUNT = re.compile(r'на\s+(\d+)[-\s]*(?:и|ти|х|ой|ом)?\s*листах?', re.I)
# второй способ записи числа листов: «ПР-01/24-3-СПСиА.СО(л. 1-3)»
SHEETS_RANGE = re.compile(r'\(\s*л\.\s*(\d+)\s*[-–—]\s*(\d+)\s*\)', re.I)

# страницы, которые вообще стоит смотреть
# только лист общих данных: гоняться за словом «Условные обозначения» по всем
# листам нельзя — find_tables на плотном чертеже А1 стоит десятки секунд
P_GENERAL = re.compile(r'Ведомость\s+(?:рабочих\s+)?чертежей|Ведомос\w*\s+ссылочных'
                       r'|Ведомость\s+основных\s+комплектов|Общие\s+указания', re.I)
P_REVISION = re.compile(r'Содержание\s+изменения', re.I)
# «ПР-01/24-1-ОВ1.СО», «П-01/24-3-ВК (л.1-40)» — шифр документа, а не текст
# изменения: одно слово, начинается с прописной, содержит цифру
DOC_CODE_ONLY = re.compile(r'[A-ZА-ЯЁ][\w\-./]*\d[\w\-./]*(?:\s*\(л\.[\d\-, ]+\))?')

MAX_CODE = 12       # длиннее — это шифр документа, а не условное обозначение
MIN_CODE = 6        # короче — это условное обозначение, а не шифр документа
MIN_NAME = 15       # средняя длина подписи в таблице УГО


def _norm(s):
    return re.sub(r'\s+', ' ', (s or '')).replace('ё', 'е').strip().lower()


def _clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


# ------------------------------------------------------------------ модели

@dataclass
class DeclaredSheet:
    """Строка ведомости рабочих чертежей основного комплекта."""
    no: int | None
    title: str
    revisions: list[int] = field(default_factory=list)
    mark: str = ''          # Зам. / Нов. / -
    note_raw: str = ''
    src_page: int = 0


@dataclass
class DocRef:
    """Строка ведомости ссылочных, прилагаемых документов или комплектов раздела."""
    kind: str               # referenced | attached | volume
    code: str
    title: str
    sheets_declared: int | None = None
    note_raw: str = ''
    src_page: int = 0


@dataclass
class SymbolRow:
    """Строка таблицы условных обозначений: подпись и место символа на листе.

    Если `code` пуст, обозначение графическое — его придётся вырезать
    картинкой по `bbox` (этим занимается agent/symbols.py).
    """
    name: str
    code: str
    page: int
    bbox: tuple
    rotation: int = 0


@dataclass
class RevisionEntry:
    """Строка листа регистрации изменений."""
    number: int | None
    sheets: str
    content: str
    doc_code: str = ''      # к какому документу комплекта относится строка
    basis: str = ''
    src_page: int = 0


@dataclass
class GeneralResult:
    general_pages: list[int] = field(default_factory=list)
    revision_pages: list[int] = field(default_factory=list)
    sheets: list[DeclaredSheet] = field(default_factory=list)
    refs: list[DocRef] = field(default_factory=list)
    volumes: list[DocRef] = field(default_factory=list)
    symbols: list[SymbolRow] = field(default_factory=list)
    revisions: list[RevisionEntry] = field(default_factory=list)
    guidelines_text: str = ''
    warnings: list[str] = field(default_factory=list)

    def gaps(self):
        """Пропуски в нумерации ведомости чертежей — первое замечание тома."""
        nos = [s.no for s in self.sheets if s.no]
        if not nos:
            return []
        return [n for n in range(1, max(nos) + 1) if n not in set(nos)]

    def as_dict(self):
        d = asdict(self)
        d['guidelines_text'] = len(self.guidelines_text)
        d['gaps'] = self.gaps()
        return d


# ------------------------------------------------------------ поиск страниц

def find_pages(doc, pages=None):
    """-> (страницы общих данных, страницы регистрации изменений), 1-based.

    pages — результат classify_pages, если он уже посчитан вызывающим:
    тогда общие данные берутся ещё и оттуда.
    """
    general, revision = [], []
    known = {pi['page'] for pi in pages if pi.get('kind') == 'general'} if pages else set()
    for pno in range(len(doc)):
        text = doc[pno].get_text()
        if P_REVISION.search(text):
            revision.append(pno + 1)
        elif P_GENERAL.search(text) or (pno + 1) in known:
            general.append(pno + 1)
    return general, revision


# ----------------------------------------------------------------- таблицы

def _tables(page):
    """Все таблицы листа, включая рамку.

    Рамку отбрасывать нельзя: у части бюро ведомость сливается с рамкой
    в одну таблицу на двадцать колонок, и первые девять листов оказываются
    внутри неё. Вместо фильтра по площади ищем строку заголовка внутри
    каждой таблицы — см. `_locate`.
    """
    return [t for t in page.find_tables().tables if t.row_count >= 2]


def _find_col(cells, alts, used):
    for j, c in enumerate(cells):
        if j not in used and any(c.startswith(a) for a in alts):
            return j
    return None


def _locate(rows, req, opt=None, validate=None):
    """Найти строку заголовка и колонки. -> (индекс строки, {канон: колонка}).

    Заголовок стоит не обязательно первой строкой: над ведомостью бывает
    подпись, шапка объекта, обозначение тома, а в таблице-рамке — десяток
    пустых строк. Поэтому перебираем все строки и выбираем ту, под которой
    больше всего похожих на данные строк.
    """
    opt = opt or {}
    best_n, best_i, best_idx = 0, None, {}
    for i, r in enumerate(rows):
        cells = [_norm(c) for c in r]
        idx, used, ok = {}, set(), True
        for canon, alts in list(req.items()) + list(opt.items()):
            j = _find_col(cells, alts, used)
            if j is None:
                if canon in req:
                    ok = False
                    break
                continue
            idx[canon] = j
            used.add(j)
        if not ok:
            continue
        body = rows[i + 1:]
        n = (sum(1 for rr in body if validate([_clean(c) for c in rr], idx))
             if validate else len(body))
        if n > best_n:
            best_n, best_i, best_idx = n, i, idx
    return best_i, best_idx


def _cell(cells, idx, key, default=''):
    j = idx.get(key)
    return cells[j] if j is not None and j < len(cells) else default


# ------------------------------------------------------------- разборщики

def _revisions(note):
    """«Изм.1,2 ,И3,з4м . 4 (- )» -> ([1, 2, 3, 4], '-').

    В колонке «Примечание» наложены слои разных ревизий: бюро дописывает
    отметку поверх прежней, и текстовый слой сохраняет обе. Поэтому из строки
    берётся множество номеров, а не её порядок.
    """
    if not note:
        return [], ''
    nums = sorted({int(n) for n in REV_NUMS.findall(note) if 0 < int(n) < 100})
    m = REV_MARK.search(re.sub(r'\s+', '', note))
    return nums, (m.group(1) if m else '')


def _is_sheet_row(cells, idx):
    return _cell(cells, idx, 'sheet').isdigit() and bool(_cell(cells, idx, 'name'))


def _parse_sheets(rows, page):
    """Ведомость рабочих чертежей основного комплекта."""
    hrow, idx = _locate(rows, {'sheet': COL_SHEET, 'name': COL_NAME},
                        {'note': COL_NOTE}, _is_sheet_row)
    if hrow is None:
        return []
    out = []
    for r in rows[hrow + 1:]:
        cells = [_clean(c) for c in r]
        if not _is_sheet_row(cells, idx):
            continue
        note = _cell(cells, idx, 'note')
        if not note:                      # колонки «Примечание» может не быть
            j = idx['name'] + 1
            note = ' '.join(cells[j:j + 2]) if len(cells) > j else ''
        nums, mark = _revisions(note)
        out.append(DeclaredSheet(no=int(_cell(cells, idx, 'sheet')),
                                 title=_cell(cells, idx, 'name'),
                                 revisions=nums, mark=mark,
                                 note_raw=note, src_page=page))
    return out


def _is_doc_row(cells, idx):
    code = _cell(cells, idx, 'code')
    # шифр документа длинный и содержит цифру: ПР-01/24-1-ЭОМ.КЖ, DKC-2019.FCP,
    # ГОСТ 21.101-2020. Короткая подпись вроде РМ-1С — условное обозначение
    return (len(code) >= MIN_CODE and bool(re.search(r'\d', code))
            and bool(_cell(cells, idx, 'name')))


def _parse_codes(rows, page):
    """Ведомость ссылочных и прилагаемых документов либо комплектов раздела."""
    hrow, idx = _locate(rows, {'code': COL_CODE, 'name': COL_NAME},
                        {'note': COL_NOTE}, _is_doc_row)
    if hrow is None:
        return []
    out, group = [], None
    for r in rows[hrow + 1:]:
        cells = [_clean(c) for c in r]
        code = _cell(cells, idx, 'code')
        title = _cell(cells, idx, 'name')
        note = _cell(cells, idx, 'note')
        if not code:                      # строка-заголовок группы
            g = _norm(title) or _norm(' '.join(cells))
            if GROUP_ATTACHED in g:
                group = ATTACHED
            elif GROUP_REFERENCED in g:
                group = REFERENCED
            continue
        if not _is_doc_row(cells, idx):
            continue
        declared = None
        for c in cells:
            if not c:
                continue
            m = SHEETS_COUNT.search(c)
            if m:
                declared = int(m.group(1))
                break
            m = SHEETS_RANGE.search(c)
            if m:
                declared = int(m.group(2)) - int(m.group(1)) + 1
                break
        kind = group or (REFERENCED if DOC_SUFFIX.search(code) else VOLUME)
        out.append(DocRef(kind=kind, code=code, title=title,
                          sheets_declared=declared, note_raw=note, src_page=page))
    return out


def _parse_symbols(table, rows, page, rotation):
    """Таблица условных обозначений.

    Отличается от ведомости данными, а не заголовком: в колонке обозначений
    у неё либо пусто (символ графический), либо короткая подпись внутри
    рамки — РМ-1С, KLZ, В1.2. У ведомости там шифр документа, он длинный
    и со слэшем: ПР-01/24-3-СПСиА.АЛ1.
    """
    hrow, idx = _locate(rows, {'code': COL_CODE, 'name': COL_NAME},
                        validate=lambda c, i: bool(_cell(c, i, 'name')))
    if hrow is None:
        return []
    body = rows[hrow + 1:]
    if len(body) < 4:
        return []
    codes, names = [], []
    for r in body:
        cells = [_clean(c) for c in r]
        codes.append(_cell(cells, idx, 'code'))
        names.append(_cell(cells, idx, 'name'))
    named = [n for n in names if len(n) >= 4]
    if len(named) < 4:
        return []
    if any(len(c) > MAX_CODE or '/' in c for c in codes):
        return []
    if sum(len(n) for n in named) / len(named) < MIN_NAME:
        return []
    col = idx['code']
    out = []
    for i, name in enumerate(names):
        if len(name) < 4:
            continue
        try:
            bbox = tuple(round(v, 1) for v in table.rows[hrow + 1 + i].cells[col])
        except Exception:
            bbox = tuple(round(v, 1) for v in table.rows[hrow + 1 + i].bbox)
        out.append(SymbolRow(name=name, code=codes[i], page=page,
                             bbox=bbox, rotation=rotation))
    return out


def _parse_revisions(page, pno):
    """Лист регистрации изменений.

    Здесь таблица — это сам штамп на весь лист, а строка заголовка стоит
    третьей: над ней шапка объекта и обозначение тома. Если колонки
    «Содержание изменения» нет — молчим и оставляем предупреждение,
    вместо того чтобы выдумывать строки.
    """
    out = []
    for t in _tables(page):
        rows = t.extract()
        hrow, idx = _locate(
            rows, {'content': ('содержание изменения',)},
            {'number': ('изм',), 'sheet': COL_SHEET, 'note': COL_NOTE},
            lambda c, i: bool(_cell(c, i, 'content')))
        if hrow is None:
            continue
        doc_code = ''
        for r in rows[hrow + 1:]:
            cells = [_clean(c) for c in r]
            content = _cell(cells, idx, 'content')
            if not content:
                continue
            # строка вида «ПР-01/24-1-ОВ1.СО» — не изменение, а шифр документа,
            # к которому относятся следующие строки
            if DOC_CODE_ONLY.fullmatch(content):
                doc_code = content
                continue
            num = _cell(cells, idx, 'number')
            out.append(RevisionEntry(
                number=int(num) if num.isdigit() else None,
                sheets=_cell(cells, idx, 'sheet'),
                content=content,
                doc_code=doc_code,
                basis=_cell(cells, idx, 'note'),
                src_page=pno))
    return out


# ------------------------------------------------------------------ склейка

def _dedupe_sheets(items):
    by_no = {}
    for s in items:
        cur = by_no.get(s.no)
        if cur is None:
            by_no[s.no] = s
            continue
        if len(s.title) > len(cur.title):
            cur.title = s.title
        cur.revisions = sorted(set(cur.revisions) | set(s.revisions))
        cur.mark = cur.mark or s.mark
        cur.note_raw = cur.note_raw or s.note_raw
    return [by_no[n] for n in sorted(by_no)]


def _dedupe_refs(items):
    by_code = {}
    for d in items:
        cur = by_code.get(d.code)
        if cur is None:
            by_code[d.code] = d
            continue
        if len(d.title) > len(cur.title):
            cur.title = d.title
        cur.sheets_declared = cur.sheets_declared or d.sheets_declared
        cur.note_raw = cur.note_raw or d.note_raw
        if cur.kind == VOLUME and d.kind != VOLUME:
            cur.kind = d.kind       # явная группа сильнее догадки по шифру
    return list(by_code.values())


def _dedupe_symbols(items):
    seen, out = set(), []
    for s in items:
        key = (s.page, _norm(s.name))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


# ------------------------------------------------------------------- фасад

def general(pdf, pages=None, progress=None) -> GeneralResult:
    """Разбор общих данных тома. pdf — путь или открытый документ."""
    own = not hasattr(pdf, 'load_page')
    doc = fitz.open(str(pdf)) if own else pdf
    res = GeneralResult()
    try:
        gen, rev = find_pages(doc, pages)
        res.general_pages, res.revision_pages = gen, rev
        total = len(gen) + len(rev) or 1
        done = 0

        for pno in gen:
            page = doc[pno - 1]
            res.guidelines_text += page.get_text() + '\n'
            for t in _tables(page):
                rows = t.extract()
                # разборщики идут независимо: широкая таблица-рамка держит
                # и ведомость чертежей, и ведомость документов сразу,
                # каждый находит свой заголовок и свои колонки
                res.sheets += _parse_sheets(rows, pno)
                sym = _parse_symbols(t, rows, pno, page.rotation)
                res.symbols += sym
                if not sym:
                    res.refs += _parse_codes(rows, pno)
            done += 1
            if progress:
                progress(done, total)

        # одна и та же ведомость попадается дважды — отдельной таблицей
        # и внутри таблицы-рамки: строки складываем, а не задваиваем
        res.sheets = _dedupe_sheets(res.sheets)
        res.refs = _dedupe_refs(res.refs)
        res.symbols = _dedupe_symbols(res.symbols)

        # ведомость комплектов раздела отделяется от ссылочных по группе
        res.volumes = [r for r in res.refs if r.kind == VOLUME]
        res.refs = [r for r in res.refs if r.kind != VOLUME]

        for pno in rev:
            res.revisions += _parse_revisions(doc[pno - 1], pno)
            done += 1
            if progress:
                progress(done, total)

        if not res.sheets:
            res.warnings.append('ведомость рабочих чертежей не распознана')
        if rev and not res.revisions:
            res.warnings.append('листы регистрации изменений найдены, '
                                'но таблица не разобрана')
        return res
    finally:
        if own:
            doc.close()


def main():
    """Отладочный запуск: python -m agent.general <файл.pdf>"""
    import sys
    path = sys.argv[1]
    r = general(path)
    print(path)
    print('  общие данные: листы', r.general_pages, '| регистрация изменений:', r.revision_pages)
    print('  ведомость чертежей:', len(r.sheets), 'листов, пропуски:', r.gaps() or 'нет')
    print('  ссылочных:', sum(1 for d in r.refs if d.kind == REFERENCED),
          '| прилагаемых:', sum(1 for d in r.refs if d.kind == ATTACHED),
          '| комплектов раздела:', len(r.volumes),
          '| УГО:', len(r.symbols),
          '| изменений:', len(r.revisions))
    for d in r.refs:
        print('   ', d.kind, d.code, '|', d.title[:46],
              '|', f'листов: {d.sheets_declared}' if d.sheets_declared else '')
    for s in r.symbols[:6]:
        print('    УГО', repr(s.code), '|', s.name[:52], '|', s.bbox)
    for e in r.revisions[:4]:
        print('    изм', e.number, '|', e.sheets[:24].replace('\n', ' '),
              '|', e.content[:64].replace('\n', ' '))
    print('  warnings:', r.warnings or 'нет')


if __name__ == '__main__':
    main()
