# -*- coding: utf-8 -*-
"""Измерение метража трубопроводов по векторной геометрии планов (экспорт Revit, 1:1).

Масштаб листа определяется по размерным цепочкам (число в мм / длина размерной
линии). Трубы — насыщенно-цветные линии; каждый сегмент относится к ближайшей
метке диаметра (ø16х2.2, Ø15 и т.п.). Вертикальные стояки в планах не видны.
"""
import math
import re
from collections import Counter, defaultdict

import numpy as np

MM = 25.4 / 72  # pt -> мм бумаги

DIM_LABEL = re.compile(r'ø(\d{1,3}(?:[.,]\d+)?)(?:х(\d+(?:[.,]\d+)?))?$')
ASSIGN_RADIUS_PT = 150  # макс. расстояние сегмент-метка


def _collect_geometry(page):
    """Один проход по get_drawings: гор. линии (для масштаба) + цветные сегменты."""
    H, segs = [], []
    for dr in page.get_drawings():
        col = dr.get('color')
        colored = col and (max(col) - min(col) >= 0.25)
        for it in dr['items']:
            if it[0] != 'l':
                continue
            a, b = it[1], it[2]
            if abs(a.y - b.y) < 0.5 and abs(a.x - b.x) > 5:
                H.append((min(a.x, b.x), max(a.x, b.x), a.y))
            if colored:
                L = math.hypot(a.x - b.x, a.y - b.y)
                if L > 1:
                    segs.append(((a.x + b.x) / 2, (a.y + b.y) / 2, L))
    return H, segs


def detect_scale(page, H=None):
    """Масштаб листа (напр. 100 для 1:100) по размерным цепочкам, иначе None."""
    words = page.get_text('words')
    if H is None:
        H, _ = _collect_geometry(page)
    ratios = []
    for w in words:
        if not re.fullmatch(r'\d{3,5}', w[4]):
            continue
        val = int(w[4])
        if not 100 <= val <= 30000:
            continue
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        best = None
        for h in H:
            if h[0] - 3 <= cx <= h[1] + 3 and abs(h[2] - cy) < 12:
                L = (h[1] - h[0]) * MM
                if L > 2:
                    dist = abs(h[2] - cy)
                    if best is None or dist < best[0]:
                        best = (dist, val / L)
        if best:
            ratios.append(round(best[1] / 10) * 10)
    if not ratios:
        return None
    val, n = Counter(ratios).most_common(1)[0]
    return val if n >= 3 and val > 0 else None


def measure_pipes(page, scale, segs):
    """Метраж по цветным линиям, ключи: '16х2.2' (полный типоразмер) или 'ду15'.

    Возвращает (sums_м, unassigned_м).
    """
    labels = []  # (x, y, [ключи]) — парная метка Ø6,35/Ø9,52 даёт два ключа
    for w in page.get_text('words'):
        t = w[4].replace('∅', 'ø').replace('Ø', 'ø').replace('x', 'х')
        parts = [DIM_LABEL.fullmatch(p) for p in t.split('/')]
        if parts and all(parts):
            ks = [((f'{m.group(1)}х{m.group(2)}' if m.group(2)
                    else f'ду{m.group(1)}').replace(',', '.')) for m in parts]
            labels.append(((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, ks))
    if not labels or not segs:
        return {}, 0.0
    keys = [ks for _, _, ks in labels]
    L = np.array([[lx, ly] for lx, ly, _ in labels])             # (m,2)
    S = np.array([[cx, cy] for cx, cy, _ in segs])               # (n,2)
    lens = np.array([sl for _, _, sl in segs]) * MM * scale / 1000.0
    d2 = ((S[:, None, :] - L[None, :, :]) ** 2).sum(axis=2)      # (n,m)
    nearest = d2.argmin(axis=1)
    ok = d2[np.arange(len(S)), nearest] < ASSIGN_RADIUS_PT ** 2
    sums = defaultdict(float)
    for i in np.nonzero(ok)[0]:
        ks = keys[nearest[i]]
        # парная метка (Ø6,35/Ø9,52): трасса нарисована линией на каждую трубу,
        # длину делим поровну между диаметрами пары
        for k in ks:
            sums[k] += lens[i] / len(ks)
    return dict(sums), float(lens[~ok].sum())


def measure_plan_pages(doc, page_infos, page_multiplier):
    """Суммарный измеренный метраж по всем планам с множителем этажей."""
    total = defaultdict(float)
    measured_pages = []
    for pi in page_infos:
        page = doc[pi['page'] - 1]
        H, segs = _collect_geometry(page)
        if not segs:
            continue
        scale = detect_scale(page, H)
        if not scale:
            continue
        sums, _ = measure_pipes(page, scale, segs)
        if not sums:
            continue
        mult = page_multiplier(pi)
        for k, v in sums.items():
            total[k] += v * mult
        measured_pages.append(pi['page'])
    return dict(total), measured_pages


def map_measured_to_mkeys(measured, mvocab, spec_items):
    """Ключи измерений ('16х2.2', 'ду15') -> ключи м-позиций ('ø16х2.2').

    Метраж «дуN» (стальные трубы, подписанные условным проходом Ø25)
    относится ровно к одной стальной позиции с этим условным проходом —
    с наибольшим количеством по спецификации.
    """
    out = {}
    # кандидаты на 'дуN': метка только диаметром (сталь Ø25, медь ∅6,35);
    # претендует позиция с наибольшим количеством по спецификации
    du_claim = {}
    for mkey, v in mvocab.items():
        dim = mkey.lstrip('ø').replace(',', '.')
        first = dim.split('х')[0]
        if ('ду' + first) in measured:
            qty = sum(spec_items[i]['qty'] or 0 for i in v['items'])
            cur = du_claim.get('ду' + first)
            if cur is None or qty > cur[1]:
                du_claim['ду' + first] = (mkey, qty)
    for mkey, v in mvocab.items():
        dim = mkey.lstrip('ø').replace(',', '.')
        first = dim.split('х')[0]
        val = measured.get(dim, 0.0)
        du = du_claim.get('ду' + first)
        if du and du[0] == mkey:
            val += measured['ду' + first]
        if val:
            out[mkey] = round(val, 1)
    return out
