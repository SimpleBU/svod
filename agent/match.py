# -*- coding: utf-8 -*-
"""Нормализация марок, подсчёт на чертежах, сверка со спецификацией."""
import re

CHECKABLE_UNITS = {'шт.', 'шт', 'компл.', 'компл', 'к-т', 'к-т.'}
M_UNITS = {'м', 'м.', 'м.п.', 'пог.м', 'пог. м', 'км'}

# хвост-ссылка на стандарт в конце марки: «… ТУ 27.40.39.110-…»,
# «… ГОСТ IEC 60947-5-1-2014», «… ТУ BY 691768257.001-2014», в т.ч. латиницей
_MARK_CUT = re.compile(
    r',?\s+\b(ТУ|TY|ГОСТ|GOST|ОСТ|СТО|DIN|EN|ISO)\b[\s.]*'
    r'(?:[A-Za-zА-Яа-я]{1,4}[\s.]*)?\d.*$', re.I)
_LR = re.compile(r'\s*(лев|прав)\.?\s*,?', re.I)
# «УЭРМ-1.4 на 2 этаже» — та же марка, отдельной строкой спецификации;
# на чертеже подписана без уточнения места
_PLACE = re.compile(r'\s+на\s+\d+\s*(?:-?м\s*)?этаж[еа]?\s*$', re.I)


def norm_text(s):
    """Канонизация строки для поиска: регистр, ø/x, пробелы."""
    s = s.replace(' ', ' ')
    s = s.replace('Ø', 'ø').replace('∅', 'ø')
    s = s.replace('x', 'х').replace('X', 'х')  # латиница -> кириллица
    s = re.sub(r'\s+', ' ', s)
    return s.strip().lower()


def canon_mark(mark):
    """Каноническая марка: без ТУ/ГОСТ-хвоста и лев./прав."""
    m = _MARK_CUT.sub('', mark)
    if not m.strip():          # марка целиком была ссылкой на стандарт
        m = mark
    m = _LR.sub(' ', m)
    m = _PLACE.sub('', m)
    return norm_text(m)


# Хвосты, которые пишут в спецификации, но не на чертеже: имя производителя,
# степень защиты, климатическое исполнение. «ЩРн-12 IP31 EKF PROxima» на схеме
# подписан как «ЩРН-12 У3 IP31» — совпасть строкой они не могут.
_VENDORS = (
    'ekf|proxima|proхima|dekraft|iek|schneider|se|systeme|abb|legrand|dkc|'
    'tdm|era|aledo|arte lamp|bonjurabajur|tp-link|lg|onи|texenergo|кэаз|'
    'эра|световые технологии|ilumia|gauss|feron'
)
_MARK_TAIL = re.compile(
    r'\s*(?:\b(?:' + _VENDORS + r')\b|ip\s?\d{2}|\bу[хл]?\d\b|\bт\d\b|'
    r'\bкл\.?\s?защиты\b)\s*', re.I)


def mark_core(canon):
    """Ядро марки без вендорных и исполнительных хвостов ('щрн-12')."""
    core = _MARK_TAIL.sub(' ', ' ' + canon + ' ')
    core = re.sub(r'[\s,;]+', ' ', core).strip(' ,;-')
    return core


def is_searchable(canon, relaxed=False):
    """Пригодна ли марка для текстового поиска на чертежах.

    relaxed — для обозначений из «Примечания» спецификации («ЩМк»): это
    заведомо позиционные обозначения с чертежей, они бывают короткими и без
    цифр, но искать их на листах осмысленно.
    """
    if len(canon) < (3 if relaxed else 4):
        return False
    if not re.search(r'\d', canon) and not (relaxed and len(canon) >= 3):
        return False
    if canon.startswith(('гост', 'ту ', 'ост', 'сто')):
        return False
    # марка-ссылка на стандарт (DIN 16892, EN 877, ISO ...) — не марка изделия
    if re.match(r'^(din|en|iso|сп|снип)\b', canon):
        return False
    # ссылка на лист комплекта («пр-01/24-1-эом лист 14») — не марка изделия
    if re.match(r'^пр[-\s][\d/.\-]+.*\bлист\b', canon):
        return False
    return True


def spec_members(it):
    """Обозначения позиции на чертежах: список наборов написаний.

    Каждый элемент списка — отдельная единица оборудования («ЩМ08», «ЩМ09»…
    из раскрытого диапазона «ЩМ08..ЩМ12»); внутри набора — написания одного
    и того же («щрн-12 ip31 ekf proхima» и ядро «щрн-12»). Количества по
    разным обозначениям складываются, по написаниям одного — берётся максимум.
    """
    members = []
    from_note = bool(it.get('mark_variants'))
    for raw in (it.get('mark_variants') or [it.get('mark') or '']):
        canon = canon_mark(raw)
        if not is_searchable(canon, relaxed=from_note):
            continue
        spellings = {canon}
        core = mark_core(canon)
        if core != canon and len(core) >= 5 and re.search(r'\d', core):
            spellings.add(core)
        members.append(spellings)
    return members


def build_vocab(spec_items):
    """canon_mark -> {'variants': set, 'members': [set], 'items': [indices]}."""
    vocab = {}
    for idx, it in enumerate(spec_items):
        mark = (it.get('mark') or '').strip()
        if not mark:
            continue
        canon = canon_mark(mark)
        if not is_searchable(canon, relaxed=bool(it.get('mark_variants'))):
            continue
        v = vocab.setdefault(canon, {'variants': set(), 'members': [], 'items': []})
        members = spec_members(it) or [{canon}]
        for sp in members:
            if sp not in v['members']:
                v['members'].append(sp)
            v['variants'] |= sp
        v['items'].append(idx)
    return vocab


# ------------------------------------------------ количественные подписи

# «УЭРМ-2.5 (10 шт.)», «ЯУР (трехфазный ввод) (50 шт.)», «Розетки МОП (12 шт.)»
QTY_PAREN = re.compile(r'\((\d{1,4})\s*шт\.?\s*\)')
# «Гайка шестигранная М8 - 3 шт.», «…(BPD2105) - 1 шт.»
QTY_DASH = re.compile(r'[-–—]\s*(\d{1,4})\s*шт\.?')
# «Труба ВГП 25х3,2 - 2 м (8шт.)» — кратность к подписанной длине
LEN_MULT = re.compile(r'\(\s*(\d{1,4})\s*шт\.?\s*\)')


def caption_qty(tail):
    """Количество из подписи на чертеже: '(10 шт.)' / '- 3 шт.' -> int|None."""
    m = QTY_PAREN.search(tail)
    if m:
        return int(m.group(1))
    m = QTY_DASH.search(tail)
    if m:
        return int(m.group(1))
    return None


def tag_presence_counts(doc, page_infos, vocab):
    """Счёт по уникальным позиционным обозначениям («ЩМ08»…«ЩМ12»).

    Обозначение — уникальная единица оборудования: сколько бы листов её ни
    показывали (силовой план, план освещения, схема), это один щит. Поэтому
    количество = число найденных обозначений, без множителей и повторов.
    Применимо только к позициям с раскрытым диапазоном (несколько обозначений).
    """
    sums, detail = {}, {}
    for canon, v in vocab.items():
        members = v.get('members') or []
        if len(members) < 2:
            continue
        found, pages = 0, set()
        for spellings in members:
            hit_pages = set()
            for pi in page_infos:
                text = norm_text(doc[pi['page'] - 1].get_text().replace('\n', ' '))
                if any(re.search(r'(?<![\w.\-])' + re.escape(sp) + r'(?![\w.])', text)
                       for sp in spellings):
                    hit_pages.add(pi['page'])
            if hit_pages:
                found += 1
                pages |= hit_pages
        if found:
            sums[canon] = float(found)
            detail[canon] = sorted(pages)
    return sums, detail


def assembly_pages(doc, page_infos, threshold=4):
    """Листы типовых узлов: подписи «- N шт.» перечисляют состав одного узла.

    Такие количества относятся к одному узлу, а не ко всему зданию, поэтому
    отдельно помечаются и не дают ложных расхождений.
    """
    out = set()
    for pi in page_infos:
        text = doc[pi['page'] - 1].get_text()
        if len(QTY_DASH.findall(text)) >= threshold and re.search(
                r'Узел|узл[аы]|конструкц|крепл', text, re.I):
            out.add(pi['page'])
    return out


def checkable(it):
    unit = (it.get('unit') or '').strip().lower()
    return unit in CHECKABLE_UNITS


def m_checkable(it):
    unit = (it.get('unit') or '').strip().lower()
    return unit in M_UNITS


DIM_PAT = re.compile(r'(?<![\d.,])(\d{1,3}(?:[.,]\d+)?(?:[хx]\d+(?:[.,]\d+)?){1,2})\b')


def cable_key(mark, core):
    """Ключ кабеля: «марка|сечение» в нормализованном виде."""
    core = core.replace('×', 'х').replace('x', 'х')
    core = re.sub(r'\s+', '', core)
    return f'{norm_text(mark)}|{norm_text(core)}'


def spec_cable_key(item):
    """Ключ кабеля для позиции спецификации ЭОМ (марка в «Тип, марка»,
    сечение — в наименовании: «3×6(N,PE)-0,66»). Иначе None."""
    mark = (item.get('mark') or '').strip()
    name = (item.get('name') or '').strip()
    if not mark or not name:
        return None
    if not re.match(r'^[\dхx×]+[\dхx×,.\s()A-ZА-Яa-zа-я+-]*$', name):
        return None
    if not re.search(r'\d\s*[хx×]\s*\d', name):
        return None
    return cable_key(mark, name)


def m_key_variants(it):
    """(канонический ключ, варианты поиска) для позиции в м/км.

    Кабель (ЭОМ) -> ключ «марка|сечение», поиск по чертежам не ведётся
    (метраж берётся из кабельного журнала).
    Иначе ключ — марка, если пригодна для поиска, иначе типоразмер из
    наименования (для труб: '16х2.2' -> ищем 'ø16х2.2' и '16х2.2').
    """
    ck = spec_cable_key(it)
    if ck:
        return ck, set()
    mark = (it.get('mark') or '').strip()
    if mark:
        canon = canon_mark(mark)
        if is_searchable(canon):
            return canon, {canon}
    m = DIM_PAT.search(norm_text(it.get('name') or ''))
    if m:
        dim = m.group(1)
        return 'ø' + dim, {'ø' + dim, dim}
    return None, None


def build_m_vocab(spec_items):
    vocab = {}
    for idx, it in enumerate(spec_items):
        if it.get('excluded') or not m_checkable(it):
            continue
        canon, variants = m_key_variants(it)
        if canon is None:
            continue
        v = vocab.setdefault(canon, {'variants': set(), 'items': []})
        v['variants'] |= variants
        v['items'].append(idx)
    return vocab


LEN_PAT = re.compile(r'[-–—]\s*(\d+(?:[.,]\d+)?)\s*(м|км)\b')


def count_lengths_on_pages(doc, page_infos, mvocab):
    """Для м/км-позиций: сумма подписанных длин и число меток на чертежах.

    Длина берётся из подписей вида «<марка> - 5 м» (на одной строке).
    Возвращает (length_sums, presence_counts, detail).
    """
    lengths = {c: 0.0 for c in mvocab}
    presence = {c: 0.0 for c in mvocab}
    detail = {c: [] for c in mvocab}
    for pi in page_infos:
        mult = page_multiplier(pi)
        lines = [norm_text(l) for l in doc[pi['page'] - 1].get_text().splitlines()]
        for canon, v in mvocab.items():
            page_len, page_n = 0.0, 0
            variants = sorted(v['variants'], key=len, reverse=True)
            if not variants:
                continue
            for line in lines:
                hit = next((var for var in variants if var in line), None)
                if not hit:
                    continue
                page_n += 1
                tail = line[line.index(hit) + len(hit):]
                lm = LEN_PAT.search(tail)
                if lm:
                    val = float(lm.group(1).replace(',', '.'))
                    # «Труба ВГП 25х3,2 - 2 м (8 шт.)»: длина одного отрезка,
                    # кратность указана в скобках
                    km = LEN_MULT.search(tail[lm.end():])
                    if km:
                        val *= int(km.group(1))
                    page_len += val * (1000 if lm.group(2) == 'км' else 1)
            if page_n:
                lengths[canon] += page_len * mult
                presence[canon] += page_n * mult
                detail[canon].append((pi['page'], page_n, mult))
    return lengths, presence, detail


FLOORS_PAT = re.compile(r'(\d+)\s*-\s*(\d+)\s*этаж|этаж\s*(\d+)\s*-\s*(\d+)', re.I)


def floors_multiplier(title):
    """Множитель этажей из названия листа: 'План 3-11 этажа' -> 9."""
    m = FLOORS_PAT.search(title or '')
    if m:
        g = [x for x in m.groups() if x]
        if len(g) == 2:
            a, b = int(g[0]), int(g[1])
            if 0 < b - a < 40:
                return b - a + 1
    return 1


LEGEND_PAT = re.compile(r'^([\w.\-]{3,20})\s+[-–—]\s+(\S{8,})\s*$')


def _page_aliases(lines, vocab):
    """Расшифровки на листе («Колл-10.1ж - SF40-10-...») -> alias -> canon.

    Возвращает (alias_map, legend_rhs_count): сколько строк легенды
    упоминают каждую марку (чтобы не считать легенду за оборудование).
    """
    alias_map = {}
    legend_rhs = {}
    for line in lines:
        m = LEGEND_PAT.match(line)
        if not m:
            continue
        alias, rhs = m.group(1), m.group(2).replace(' ', '')
        if len(rhs) < 10:
            continue
        for canon in vocab:
            c_ns = canon.replace(' ', '')
            if len(c_ns) >= 10 and (c_ns.startswith(rhs) or rhs.startswith(c_ns)):
                alias_map[alias] = canon
                legend_rhs[canon] = legend_rhs.get(canon, 0) + 1
                break
    return alias_map, legend_rhs


def build_doc_aliases(doc, page_infos, vocab):
    """Карта алиасов по всему документу.

    Объединяет расшифровки со всех листов; для алиасов, встречающихся на
    чертежах без расшифровки (легенда неполная), пытается вывести марку из
    числа ответвлений: «Колл-7.1ж» -> единственная марка вида SF40-7-...
    """
    alias_map = {}
    seen_tokens = set()
    for pi in page_infos:
        raw_text = doc[pi['page'] - 1].get_text()
        lines = [norm_text(l) for l in raw_text.splitlines()]
        am, _ = _page_aliases(lines, vocab)
        alias_map.update(am)
        text = norm_text(raw_text.replace('\n', ' '))
        seen_tokens |= set(re.findall(r'(?<![\w.\-])(колл-[\w.]+)', text))
    for tok in seen_tokens - set(alias_map):
        m = re.match(r'колл-(\d+)', tok)
        if not m:
            continue
        n = m.group(1)
        cands = [c for c in vocab if re.match(r'^s[af]\d+-' + n + r'-', c)]
        if len(cands) == 1:
            alias_map[tok] = cands[0]
    return alias_map


def page_multiplier(pi):
    """Множитель этажей листа: из названия либо из текста штампа."""
    return max(floors_multiplier(pi.get('title', '')),
               floors_multiplier(pi.get('stamp', '')))


def longer_keys(vocab):
    """variant -> более длинные варианты других марок, содержащие его.

    «уэрм-2.4» входит в «уэрм-2.4 вариант 2»: без этого поправка на вложение
    приписывает базовой марке чужие вхождения и подписи.
    """
    allv = sorted({v for d in vocab.values() for v in d['variants']},
                  key=len, reverse=True)
    return {v: [o for o in allv if len(o) > len(v) and v in o] for v in allv}


def _caption_sum(lines, variants, aliases, longer=None):
    """Сумма количественных подписей марки на листе.

    На схемах компоновки и однолинейных схемах количество подписано текстом
    («УЭРМ-2.5 (10 шт.)»), а сама марка нарисована один раз. Там, где подпись
    есть, она и есть количество — счёт вхождений её не заменяет.
    """
    longer = longer or {}
    keys = sorted(set(variants) | set(aliases), key=len, reverse=True)
    total = 0.0
    for line in lines:
        hit = next((k for k in keys if k in line), None)
        if not hit:
            continue
        # подпись принадлежит более длинной марке, встреченной в той же строке
        if any(o in line for o in longer.get(hit, ())):
            continue
        q = caption_qty(line[line.index(hit) + len(hit):])
        if q:
            total += q
    return total


def caption_counts_on_pages(doc, page_infos, vocab, doc_aliases=None):
    """Количества марок по подписям на чертежах (точный источник).

    Возвращает (sums: canon -> шт, detail: canon -> [листы]).
    Множитель этажей не применяется: подпись уже даёт итог по объекту
    (напр. «УЭРМ-1.4 (11 шт.)» на схеме компоновки).
    """
    sums, detail = {}, {}
    doc_aliases = doc_aliases or {}
    longer = longer_keys(vocab)
    for pi in page_infos:
        lines = [norm_text(l) for l in doc[pi['page'] - 1].get_text().splitlines()]
        for canon, v in vocab.items():
            aliases = [a for a, t in doc_aliases.items() if t == canon]
            n = _caption_sum(lines, v['variants'], aliases, longer)
            if n:
                sums[canon] = sums.get(canon, 0.0) + n
                detail.setdefault(canon, []).append(pi['page'])
    return sums, detail


def count_on_pages(doc, page_infos, vocab, doc_aliases=None, assembly=None):
    """Подсчёт вхождений марок на страницах.

    Марка ищется и в тексте без пробелов (переносы в ячейках спецификации
    вставляют пробелы). Вхождения алиасов из расшифровок («Колл-10.1ж»)
    прибавляются к марке; сами строки легенды не считаются. Если рядом с
    маркой стоит количественная подпись («(10 шт.)»), берётся число из неё.

    page_infos: [{'page': n, 'title': str}], возвращает:
      counts: canon -> суммарное кол-во (с учётом множителя этажей)
      detail: canon -> [(page, raw_count, multiplier)]
      asm_only: canon -> True, если марка встречена только на листах узлов
    """
    counts = {c: 0.0 for c in vocab}
    raw = {c: 0.0 for c in vocab}
    detail = {c: [] for c in vocab}
    asm_hits = {c: 0 for c in vocab}
    all_hits = {c: 0 for c in vocab}
    assembly = assembly or set()
    doc_aliases = doc_aliases or {}
    longer = longer_keys(vocab)
    for pi in page_infos:
        raw_text = doc[pi['page'] - 1].get_text()
        lines = [norm_text(l) for l in raw_text.splitlines()]
        text = norm_text(raw_text.replace('\n', ' '))
        text_ns = text.replace(' ', '')
        mult = page_multiplier(pi)
        page_alias, legend_rhs = _page_aliases(lines, vocab)
        alias_map = dict(doc_aliases, **page_alias)
        # сколько строк легенды на этом листе приходится на каждый алиас
        legend_lines = {}
        for line in lines:
            lm = LEGEND_PAT.match(line)
            if lm and lm.group(1) in alias_map:
                legend_lines[lm.group(1)] = legend_lines.get(lm.group(1), 0) + 1
        for canon, v in vocab.items():
            # разные обозначения одной позиции («ЩМ08»…«ЩМ12») складываются,
            # написания одного обозначения — максимум
            n = 0
            for spellings in v.get('members') or [v['variants']]:
                best = 0
                for var in sorted(spellings, key=len, reverse=True):
                    direct = len(re.findall(re.escape(var), text))
                    despaced = len(re.findall(re.escape(var.replace(' ', '')), text_ns))
                    # вычитаем вхождения внутри более длинных марок
                    nested = sum(len(re.findall(re.escape(o), text))
                                 for o in longer.get(var, ()))
                    best = max(best, direct - nested, despaced - nested, 0)
                n += best
            # марка из легенды — это строка расшифровки, не единица оборудования
            n = max(n - legend_rhs.get(canon, 0), 0)
            own_aliases = [a for a, t in alias_map.items() if t == canon]
            for alias in own_aliases:
                occ = len(re.findall(
                    r'(?<![\w.\-])' + re.escape(alias) + r'(?![\w.])', text))
                n += max(occ - legend_lines.get(alias, 0), 0)
            if n:
                # если количество подписано на листе, оно и есть количество
                cap = _caption_sum(lines, v['variants'], own_aliases, longer)
                n = cap or n
                counts[canon] += n * mult
                raw[canon] += n
                detail[canon].append((pi['page'], n, mult))
                all_hits[canon] += 1
                if pi['page'] in assembly:
                    asm_hits[canon] += 1
    asm_only = {c: True for c in vocab
                if all_hits[c] and asm_hits[c] == all_hits[c]}
    return counts, raw, detail, asm_only


def _len_ok(spec_qty, found, rel=0.02):
    """Совпадение метража с допуском (±2% подписи, ±15% измерение)."""
    tol = max(abs(spec_qty) * rel, 0.5)
    return abs(found - spec_qty) <= tol


def reconcile(spec_items, plan_counts, plan_detail, schema_counts, schema_detail, vocab,
              plan_raw=None, schema_raw=None, mvocab=None,
              plan_len=None, plan_pres=None, plan_len_detail=None,
              schema_len=None, schema_pres=None, schema_len_detail=None,
              plan_meas=None, measured_pages=None,
              journal_sums=None, journal_detail=None,
              light_sums=None, light_detail=None, device_sums=None,
              plan_asm=None, schema_asm=None,
              caption_sums=None, caption_detail=None,
              tag_sums=None, tag_detail=None):
    """Формирует строки отчёта. Возвращает (rows, uncheckable_rows)."""
    rows, unrows = [], []
    seen_marks = set()
    mvocab = mvocab or {}
    plan_asm = plan_asm or {}
    schema_asm = schema_asm or {}
    # группируем проверяемые позиции по канонической марке
    by_mark = {}
    by_mkey = {}
    for idx, it in enumerate(spec_items):
        if it.get('excluded'):
            continue
        mark = (it.get('mark') or '').strip()
        canon = canon_mark(mark) if mark else ''
        if checkable(it) and canon in vocab:
            by_mark.setdefault(canon, []).append(it)
            continue
        if m_checkable(it):
            mkey, _ = m_key_variants(it)
            if mkey in mvocab:
                by_mkey.setdefault(mkey, []).append(it)
                continue
        unrows.append(it)

    # --- метровые позиции: сверка длин
    for mkey, items in sorted(by_mkey.items()):
        unit = (items[0]['unit'] or '').strip().lower()
        k = 1000.0 if unit == 'км' else 1.0  # спецификация в км -> чертежи в м
        spec_qty = round(sum(i['qty'] or 0 for i in items), 2)
        pl = (plan_len or {}).get(mkey, 0) / k
        sl = (schema_len or {}).get(mkey, 0) / k
        pn = (plan_pres or {}).get(mkey, 0)
        sn = (schema_pres or {}).get(mkey, 0)
        meas = (plan_meas or {}).get(mkey, 0) / k
        journal = (journal_sums or {}).get(mkey)
        used_meas = False
        if pl == 0 and meas:
            pl = meas
            used_meas = True
        if journal is not None:
            # кабельный журнал — точный источник длин, сверяем по нему
            status = 'ок' if _len_ok(spec_qty, journal / k, 0) else 'расхождение'
            status += ' (по кабельному журналу)'
        else:
            sides = []
            if pl:
                sides.append(_len_ok(spec_qty, pl, 0.15 if used_meas else 0.02))
            if sl:
                sides.append(_len_ok(spec_qty, sl))
            if not sides:
                status = ('нет на чертежах' if pn == 0 and sn == 0
                          else 'есть на чертежах, метраж не подписан')
            elif all(sides):
                status = 'ок'
            elif any(sides):
                status = 'ок (частично)'
            else:
                status = 'расхождение'
            if used_meas and sides:
                status += ' (по измерению)'
        rows.append({
            'mark': mkey, 'names': ' | '.join(sorted({i['name'][:80] for i in items})),
            'unit': items[0]['unit'], 'spec_qty': spec_qty,
            'plan_qty': round(pl, 2), 'plan_raw': pn,
            'schema_qty': round(sl, 2), 'schema_raw': sn,
            'journal_qty': (round(journal / k, 2) if journal is not None else ''),
            'status': status,
            'spec_pages': sorted({i['page'] for i in items}),
            'plan_pages': ([d[0] for d in (plan_len_detail or {}).get(mkey, [])]
                           or (measured_pages if used_meas else [])
                           or (journal_detail or {}).get(mkey, [])),
            'schema_pages': [d[0] for d in (schema_len_detail or {}).get(mkey, [])],
            'sections': sorted({i['section'] for i in items}),
        })
    for canon, items in sorted(by_mark.items()):
        spec_qty = round(sum(i['qty'] or 0 for i in items), 2)
        p = plan_counts.get(canon, 0)
        s = schema_counts.get(canon, 0)
        pr = (plan_raw or {}).get(canon, p)
        sr = (schema_raw or {}).get(canon, s)
        # аппараты защиты: на схемах подписаны серией и параметрами
        dev = (device_sums or {}).get(canon)
        if dev is not None:
            dqty, dpages = dev
            rows.append({
                'mark': canon, 'names': ' | '.join(sorted({i['name'][:80] for i in items})),
                'unit': items[0]['unit'], 'spec_qty': spec_qty,
                'plan_qty': p, 'plan_raw': pr, 'schema_qty': dqty, 'schema_raw': sr,
                'journal_qty': dqty,
                'status': ('ок' if abs(dqty - spec_qty) < 0.001 else 'расхождение')
                          + ' (по схемам аппаратов)',
                'spec_pages': sorted({i['page'] for i in items}),
                'plan_pages': [], 'schema_pages': dpages,
                'sections': sorted({i['section'] for i in items}),
            })
            continue
        # уникальные позиционные обозначения («ЩМ08»…«ЩМ12») — точный источник
        tag = (tag_sums or {}).get(canon)
        if tag is not None:
            rows.append({
                'mark': canon, 'names': ' | '.join(sorted({i['name'][:80] for i in items})),
                'unit': items[0]['unit'], 'spec_qty': spec_qty,
                'plan_qty': p, 'plan_raw': pr, 'schema_qty': s, 'schema_raw': sr,
                'journal_qty': tag,
                'status': ('ок' if abs(tag - spec_qty) < 0.001 else 'расхождение')
                          + ' (по обозначениям на чертежах)',
                'spec_pages': sorted({i['page'] for i in items}),
                'plan_pages': [d[0] for d in plan_detail.get(canon, [])],
                'schema_pages': [d[0] for d in schema_detail.get(canon, [])],
                'sections': sorted({i['section'] for i in items}),
            })
            continue
        # количество подписано на чертеже («УЭРМ-2.5 (10 шт.)») — точный источник
        cap = (caption_sums or {}).get(canon)
        if cap is not None and not (plan_asm.get(canon) or schema_asm.get(canon)):
            cpages = (caption_detail or {}).get(canon, [])
            rows.append({
                'mark': canon, 'names': ' | '.join(sorted({i['name'][:80] for i in items})),
                'unit': items[0]['unit'], 'spec_qty': spec_qty,
                'plan_qty': p, 'plan_raw': pr, 'schema_qty': s, 'schema_raw': sr,
                'journal_qty': cap,
                'status': ('ок' if abs(cap - spec_qty) < 0.001 else 'расхождение')
                          + ' (по подписям на чертежах)',
                'spec_pages': sorted({i['page'] for i in items}),
                'plan_pages': [q for q in cpages
                               if q in {d[0] for d in plan_detail.get(canon, [])}],
                'schema_pages': [q for q in cpages
                                 if q in {d[0] for d in schema_detail.get(canon, [])}] or cpages,
                'sections': sorted({i['section'] for i in items}),
            })
            continue
        # ведомость осветительного оборудования — точный источник количества
        lkey = canon.replace(' ', '')
        light = (light_sums or {}).get(lkey)
        if light is not None:
            rows.append({
                'mark': canon, 'names': ' | '.join(sorted({i['name'][:80] for i in items})),
                'unit': items[0]['unit'], 'spec_qty': spec_qty,
                'plan_qty': p, 'plan_raw': pr, 'schema_qty': s, 'schema_raw': sr,
                'journal_qty': light,
                'status': ('ок' if abs(light - spec_qty) < 0.001 else 'расхождение')
                          + ' (по ведомости освещения)',
                'spec_pages': sorted({i['page'] for i in items}),
                'plan_pages': (light_detail or {}).get(lkey, []),
                'schema_pages': [],
                'sections': sorted({i['section'] for i in items}),
            })
            continue
        # совпадение допустимо и по сырому счёту (без множителя этажей)
        if p == 0 and s == 0:
            # комплектующее щита, которого нет ни на планах, ни на схемах:
            # корпуса, клеммы, шины, сальники на чертежах не подписываются —
            # это не пропуск, а свойство комплекта, поэтому не «расхождение»
            host = next((i for i in items if i.get('component_of')), None)
            if host is not None:
                for i in items:
                    unrows.append(dict(
                        i, uncheck_reason='комплектующее щита (поз. {}: {}) — '
                                          'на чертежах не подписывается'.format(
                                              host['component_of'],
                                              host.get('component_host', ''))))
                continue
            status = 'нет на чертежах'
        elif spec_qty in (p, pr) and spec_qty in (s, sr):
            status = 'ок'
        elif spec_qty in (p, pr) or spec_qty in (s, sr):
            status = 'ок (частично)'
        elif plan_asm.get(canon) or schema_asm.get(canon):
            # марка встречена только на листах типовых узлов: подпись «- N шт.»
            # относится к одному узлу, общее количество по ней не выводится
            status = 'узел: кол-во на 1 узел'
        else:
            status = 'расхождение'
        rows.append({
            'mark': canon, 'names': ' | '.join(sorted({i['name'][:80] for i in items})),
            'unit': items[0]['unit'], 'spec_qty': spec_qty,
            'plan_qty': p, 'plan_raw': pr, 'schema_qty': s, 'schema_raw': sr,
            'journal_qty': '', 'status': status,
            'spec_pages': sorted({i['page'] for i in items}),
            'plan_pages': [d[0] for d in plan_detail.get(canon, [])],
            'schema_pages': [d[0] for d in schema_detail.get(canon, [])],
            'sections': sorted({i['section'] for i in items}),
        })
        seen_marks.add(canon)
    return rows, unrows
