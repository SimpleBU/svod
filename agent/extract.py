# -*- coding: utf-8 -*-
"""Извлечение данных из PDF проекта: классификация листов, спецификация, маркировки чертежей."""
import re
import pymupdf as fitz

SPEC_HDR = 'Наименование и техническая'

TITLE_PAT = re.compile(
    r'^(Отопление[^\n]{0,80}План[^\n]{0,60}|План[ы]?\s[^\n]{0,80}|Схем[аы][^\n]{0,100}|'
    r'Общие данные[^\n]{0,40}|Общие указания|Ведомость[^\n]{0,80}|Узл?[ыа]?[ \n][^\n]{0,80}|'
    r'Принципиальн[^\n]{0,80})$', re.M)

# Шифр раздела бывает со строчными буквами: СПСиА, ПРк. Прежний [А-ЯЁ]+
# такие шифры не брал вовсе — том разбирался как приложения, и
# спецификация молча не находилась.
# Средний сегмент бывает не только числом: «ПР-01/24-8.2-ОВ1». Прежний
# (?:-\d+)? такой шифр не брал, и лист оставался без кода.
DOCCODE_PAT = re.compile(r'ПР-[\d/._]+(?:-[\d.]+)?-[А-ЯЁ][А-Яа-яЁё]*\d*(?:\.[А-ЯЁ][А-Яа-яЁё]*\d*)?')


def _corner_text(page, fx, fy):
    """Текст правого нижнего угла листа в видимой ориентации."""
    rot = page.rotation_matrix
    w, h = page.rect.width, page.rect.height
    out = []
    for word in page.get_text('words'):
        c = fitz.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2) * rot
        if c.x > w * fx and c.y > h * fy:
            out.append((round(c.y / 6), c.x, word[4]))
    out.sort()
    return re.sub(r'\s+', ' ', ' '.join(t for _, _, t in out))[:800]


def classify_pages(doc, progress=None):
    """Классифицирует страницы: spec / vt / plan / schema / general / appendix / other.

    progress(done, total) — необязательный колбэк хода работы (нужен вызывающему
    из очереди; на поведение не влияет).
    """
    pages = []
    total = len(doc)
    for pno in range(total):
        if progress:
            progress(pno, total)
        page = doc[pno]
        text = page.get_text()
        codes = set(DOCCODE_PAT.findall(text))
        has_spec_hdr = SPEC_HDR in text
        # название листа из штампа: правый нижний угол
        r = page.rect
        clip = fitz.Rect(r.width * 0.5, r.height * 0.7, r.width, r.height)
        stamp_text = page.get_text(clip=clip)
        titles = [l.strip() for l in text.splitlines() if TITLE_PAT.match(l.strip())]
        stamp_titles = [l.strip() for l in stamp_text.splitlines() if TITLE_PAT.match(l.strip())]
        cands = stamp_titles or titles
        # приоритет «План…» над мелкими узлами/схемами на том же листе
        plans = [t for t in cands if re.search(r'\bПлан', t)]
        title = plans[0] if plans else (cands[0] if cands else '')

        spec_code = any(c.endswith('.СО') for c in codes)
        vt_code = any(c.endswith('.ВТ') for c in codes)

        if not codes:
            kind = 'appendix'
        elif has_spec_hdr and spec_code:
            kind = 'spec'
        elif vt_code and not spec_code and not has_spec_hdr:
            kind = 'vt'
        elif re.search(r'\bПлан', title) and 'Схем' not in title.split('.')[0]:
            kind = 'plan'
        elif 'Схем' in title:
            kind = 'schema'
        elif re.search(r'Общие (данные|указания)|Ведомость', title):
            kind = 'general'
        elif pno < 6:
            kind = 'cover'
        else:
            # чертёжный лист без явного названия: по содержимому
            if re.search(r'Отопление[^\n]{0,60}План|^План \d', text, re.M):
                kind = 'plan'
            elif re.search(r'^Схема ', text, re.M):
                kind = 'schema'
            else:
                kind = 'other'
        # основная надпись (правый нижний угол видимого листа): название листа
        # переносится на 2-3 строки, и «3-11 этажа» не попадает в title.
        # координаты слов — до применения /Rotate, поэтому приводим их к
        # видимой системе, а не режем clip'ом
        stamp = _corner_text(page, 0.70, 0.78)
        pages.append({'page': pno + 1, 'kind': kind, 'title': title[:120],
                      'stamp': stamp, 'codes': sorted(codes)})
    return pages


# ---------------------------------------------------------------- спецификация

SECTION_PAT = re.compile(r'^\d\s*секция$', re.I)

# текст печати «В ПРОИЗВОДСТВО РАБОТ», попадающий поверх таблиц
OVERLAY_PAT = re.compile(
    r'ООО\s*<<[^>]*>>|ООО\s*«[^»]*ИНЖИНИРИНГ[^»]*»|В ПРОИЗВОДСТВО РАБОТ|'
    r'\d{2}\s+(ЯНВАРЯ|ФЕВРАЛЯ|МАРТА|АПРЕЛЯ|МАЯ|ИЮНЯ|ИЮЛЯ|АВГУСТА|СЕНТЯБРЯ|ОКТЯБРЯ|НОЯБРЯ|ДЕКАБРЯ)\s+\d{4}',
    re.I)

# Печать ставится поверх таблицы под углом, поэтому в ячейки попадают не целые
# слова, а их обрывки: «<< РОИ ТШП-0,66…», «АБО Acti9 iC60L…», «ИНЖИ ДСТ …».
# Такие обрывки — подстроки слов печати; собираем их в один корпус и срезаем
# по краям ячейки, пока в остатке сохраняются цифры (т.е. сама марка).
_STAMP_WORDS = (
    'ооо мд инжиниринг в производство работ гип малышев подп дата '
    'января февраля марта апреля мая июня июля августа сентября октября '
    'ноября декабря согласовано'
)
_STAMP_CORPUS = _STAMP_WORDS.replace(' ', '')
_STAMP_PUNCT = {'<<', '>>', '«', '»', ')', '(', '.', ',', '|'}


def _is_stamp_fragment(tok):
    t = tok.strip().lower().replace('ё', 'е')
    if not t or t in _STAMP_PUNCT:
        return True
    if any(ch.isdigit() for ch in t):
        return False
    t = t.strip('.,()<>«»')
    if not t or len(t) > 12:
        return False
    if not re.fullmatch(r'[а-я]+', t):
        return False
    return t in _STAMP_CORPUS


def _strip_stamp_fragments(s):
    """Срезает обрывки текста печати по краям значения ячейки."""
    toks = s.split()
    if not any(any(ch.isdigit() for ch in t) for t in toks):
        return s          # в ячейке нет цифр — не марка, не трогаем
    while toks and _is_stamp_fragment(toks[0]):
        toks.pop(0)
    while toks and _is_stamp_fragment(toks[-1]):
        toks.pop()
    return ' '.join(toks) if toks else s


def _clean_cell(s):
    return _strip_stamp_fragments(OVERLAY_PAT.sub('', s).strip()).strip()


def _header_map(row):
    """Отображение колонок таблицы по заголовку."""
    m = {}
    for i, cell in enumerate(row):
        c = (cell or '').replace('\n', ' ')
        if 'Поз' in c and 'pos' not in m:
            m['pos'] = i
        elif 'Наименование' in c:
            m['name'] = i
        elif 'марка' in c:
            m['mark'] = i
        elif 'Код' in c:
            m['code'] = i
        elif 'Поставщик' in c:
            m['vendor'] = i
        elif 'Ед' in c and 'unit' not in m:
            m['unit'] = i
        elif c.strip().startswith('Кол'):
            m['qty'] = i
        elif 'Масса' in c:
            m['mass'] = i
        elif 'Приме' in c:
            m['note'] = i
    return m if {'name', 'unit', 'qty'} <= set(m) else None


def _hlines(page):
    """Отрезки-кандидаты в зачёркивания: (горизонтальные, вертикальные).

    Вертикальные нужны для повёрнутых на 90° таблиц (спецификации ЭОМ).
    """
    hl, vl = [], []
    for dr in page.get_drawings():
        for it in dr['items']:
            if it[0] == 'l':
                a, b = it[1], it[2]
                if abs(a.y - b.y) < 0.7 and abs(a.x - b.x) > 3:
                    hl.append((min(a.x, b.x), max(a.x, b.x), a.y))
                elif abs(a.x - b.x) < 0.7 and abs(a.y - b.y) > 3:
                    vl.append((min(a.y, b.y), max(a.y, b.y), a.x))
            elif it[0] == 're':
                r = it[1]
                if r.height < 1.5 and r.width > 3:
                    hl.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif r.width < 1.5 and r.height > 3:
                    vl.append((r.y0, r.y1, (r.x0 + r.x1) / 2))
    return hl, vl


def _is_struck(w, lines):
    """Перечёркнуто ли слово линией (горизонтальной или вертикальной)."""
    hl, vl = lines
    x0, y0, x1, y1 = w[:4]
    if any(y0 + 1 < h[2] < y1 - 1 and
           min(h[1], x1) - max(h[0], x0) > (x1 - x0) * 0.5 for h in hl):
        return True
    return any(x0 + 1 < v[2] < x1 - 1 and
               min(v[1], y1) - max(v[0], y0) > (y1 - y0) * 0.5 for v in vl)


def parse_spec(doc, spec_pages, progress=None):
    """Извлекает позиции спецификации со страниц spec_pages (номера с 1).

    Учитывает изменения: зачёркнутые значения количества отбрасываются,
    полностью зачёркнутые строки помечаются excluded=True.
    """
    items = []
    section = ''
    category = ''
    for si, pno in enumerate(spec_pages):
        if progress:
            progress(si, len(spec_pages))
        page = doc[pno - 1]
        words = page.get_text('words')
        hl = _hlines(page)
        # координаты слов — в видимой системе, ячеек find_tables — в системе
        # страницы без учёта /Rotate; приводим слова к системе таблицы
        rot = page.rotation_matrix
        tabs = page.find_tables()
        for tab in tabs.tables:
            rows = tab.extract()
            hdr = None
            for ri, row in enumerate(rows):
                if hdr is None:
                    cand = _header_map(row)
                    if cand:
                        hdr = cand
                    continue
                def cell(key):
                    i = hdr.get(key)
                    if i is None or i >= len(row):
                        return ''
                    return _clean_cell((row[i] or '').replace('\n', ' ').strip())
                def cell_bbox(key):
                    i = hdr.get(key)
                    try:
                        return tab.rows[ri].cells[i]
                    except (IndexError, TypeError):
                        return None
                def cell_words(key):
                    bb = cell_bbox(key)
                    if not bb:
                        return []
                    r = fitz.Rect(bb)
                    out = []
                    for w in words:
                        c = fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) * rot
                        if r.contains(c):
                            out.append(w)
                    # порядок чтения: для повёрнутых листов — по координате в
                    # системе таблицы (сверху вниз, слева направо)
                    out.sort(key=lambda w: (round((fitz.Point(
                        (w[0] + w[2]) / 2, (w[1] + w[3]) / 2) * rot).y / 3),
                        (fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) * rot).x))
                    return out
                name, unit, qty = cell('name'), cell('unit'), cell('qty')
                if not name and not qty:
                    continue
                if name and not unit and not qty:
                    if SECTION_PAT.match(name):
                        section = name
                    else:
                        category = name
                    continue
                # зачёркивания
                nw = cell_words('name')
                excluded = bool(nw) and all(_is_struck(w, hl) for w in nw)
                qw = cell_words('qty')
                toks = re.findall(r'\d+(?:[.,]\d+)?', qty)
                qty_val = _num(qty) if len(toks) <= 1 else None
                if qw:
                    live = [w[4] for w in qw if not _is_struck(w, hl)]
                    if not live:
                        excluded = True
                        qty_val = 0.0
                    elif len(live) < len(qw) or qty_val is None:
                        qty_val = _num(''.join(live)) if len(live) == 1 else _num(live[-1])
                        qty = ' '.join(live)
                elif qty_val is None and len(toks) > 1:
                    # ячейка с двумя числами (изм.), слова ячейки не сматчились:
                    # выбираем незачёркнутое число по всей странице
                    live_t = [t for t in toks
                              if any(w[4].strip() == t and not _is_struck(w, hl) for w in words)]
                    struck_t = [t for t in toks
                                if any(w[4].strip() == t and _is_struck(w, hl) for w in words)]
                    pick = [t for t in live_t if t not in struck_t] or live_t or toks[-1:]
                    qty_val = _num(pick[-1])
                items.append({
                    'page': pno, 'section': section, 'category': category,
                    'pos': cell('pos'), 'name': name, 'mark': cell('mark'),
                    'vendor': cell('vendor'), 'unit': unit, 'qty_raw': cell('qty'),
                    'qty': qty_val, 'excluded': excluded, 'note': cell('note'),
                })
    _fix_docref_marks(items)
    _flag_assemblies(items)
    return items


# ---- пост-обработка спецификации -------------------------------------------

# «ПР-01/24-1-ЭОМ лист 14» в колонке марки — это ссылка на лист схемы, а не
# марка изделия; обозначение щита стоит в «Примечании» («ЩМк», «1ВП1(8.1)»).
DOCREF_MARK = re.compile(r'^\s*ПР[-\s][\d/.\-]+.*?\bлист\b\s*\d+', re.I)
# «ЩМ08..ЩМ12», «ЩМ08…ЩМ12» — диапазон обозначений однотипных щитов
RANGE_PAT = re.compile(r'^([^\d]*?)(\d+)\s*(?:\.\.\.?|…|-|–)\s*([^\d]*?)(\d+)$')


def expand_range(text):
    """«ЩМ08..ЩМ12» -> [ЩМ08 … ЩМ12]; иначе [text]."""
    t = (text or '').strip()
    m = RANGE_PAT.match(t)
    if not m:
        return [t] if t else []
    pre, a, pre2, b = m.group(1).strip(), m.group(2), m.group(3).strip(), m.group(4)
    if pre2 and pre2 != pre:
        return [t]
    lo, hi = int(a), int(b)
    if not pre or not 0 <= hi - lo <= 60:
        return [t]
    width = len(a) if a.startswith('0') else 0
    return [f'{pre}{str(i).zfill(width)}' for i in range(lo, hi + 1)]


def _fix_docref_marks(items):
    """Марка-ссылка на лист -> обозначение из «Примечания» (+ раскрытие диапазона)."""
    for it in items:
        mark = (it.get('mark') or '').strip()
        if not mark or not DOCREF_MARK.match(mark):
            continue
        note = (it.get('note') or '').strip()
        variants = [v for v in expand_range(note) if len(v) >= 2]
        if not variants:
            continue
        it['sheet_ref'] = mark
        it['mark'] = variants[0]
        it['mark_variants'] = variants


def _pos_parent(pos):
    pos = (pos or '').strip()
    return pos.rsplit('.', 1)[0] if '.' in pos else ''


def _flag_assemblies(items):
    """Помечает составные изделия («в составе:») и их комплектующие.

    Комплектующие внутри щита (корпуса, реле, автоматы, клеммы, сальники) на
    чертежах не подписываются в принципе — сверять их по чертежам нельзя.
    """
    composite = {(it.get('pos') or '').strip() for it in items
                 if re.search(r'в состав[еa]\s*:?\s*$', (it.get('name') or '').strip(), re.I)}
    if not composite:
        return
    by_pos = {}
    for it in items:
        pos = (it.get('pos') or '').strip()
        if pos:
            by_pos.setdefault(pos, it)
    for it in items:
        pos = (it.get('pos') or '').strip()
        if pos in composite:
            it['composite'] = True
            continue
        parent = _pos_parent(pos)
        while parent:
            if parent in composite:
                it['component_of'] = parent
                host = by_pos.get(parent, {})
                it['component_host'] = (host.get('mark') or host.get('name', ''))[:60]
                break
            parent = _pos_parent(parent)


def _num(s):
    s = (s or '').replace(' ', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------- чертежи

def page_label_counts(doc, pno, marks):
    """Считает вхождения каждой нормализованной марки на странице pno (с 1).

    marks: dict canon_mark -> список вариантов написания (сырых строк для поиска).
    Возвращает dict canon_mark -> count.
    """
    text = doc[pno - 1].get_text()
    counts = {}
    for canon, variants in marks.items():
        n = 0
        # ищем самый длинный вариант первым, чтобы не двоить
        for v in sorted(set(variants), key=len, reverse=True):
            n = max(n, len(re.findall(re.escape(v), text)))
        if n:
            counts[canon] = n
    return counts
