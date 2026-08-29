# -*- coding: utf-8 -*-
"""Картинка листа: обзор, миниатюра, кроп под зум, поиск марок.

Единственное место, где PDF открывается ради изображения, а не ради
разбора. Портал зовёт только эти функции и про pymupdf по-прежнему
не знает.

Два правила, из которых следует всё остальное:

1. **Масштаб задаётся бюджетом пикселей, а не dpi.** В комплекте попадаются
   листы 2520×594 мм: при фиксированных 150 dpi это 52 Мпикс и 408 МБ
   памяти на один лист. Бюджет держит любой лист в тех же рамках, что А3.
2. **Координаты — в долях отрисованной картинки.** Листы бывают повёрнуты
   (у ЭОМ почти все на 90°), и эксперт кликает по тому, что видит.
   `page.rect` у pymupdf уже учитывает поворот, поэтому доли картинки и
   доли `page.rect` — одно и то же, и перевод туда-обратно живёт здесь,
   в двух функциях, а не расползается по проекту.

Формат — PNG: у pymupdf нет webp, а линейной графикеjpeg противопоказан
(на тонких линиях он и тяжелее, и грязнее). Лист А3 при обзорной ширине —
около 180 КБ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf as fitz

# сколько пикселей позволено одной картинке: 12 Мпикс — это ~150 МБ пиковой
# памяти на рендер, замерено на самом большом листе комплекта
PIXEL_BUDGET = 12_000_000
OVERVIEW_WIDTH = 2200      # обзор: читаются подписи, но не мелкие выноски
THUMB_WIDTH = 240
MAX_CROP_WIDTH = 3000      # кроп под зум крупнее экрана не нужен


@dataclass
class PageImage:
    png: bytes
    width: int
    height: int
    zoom: float            # во сколько раз картинка крупнее точек PDF
    rotation: int

    def as_dict(self):
        return {'width': self.width, 'height': self.height,
                'zoom': self.zoom, 'rotation': self.rotation}


def _open(pdf):
    """-> (документ, нужно ли его закрывать)."""
    if hasattr(pdf, 'load_page'):
        return pdf, False
    return fitz.open(str(pdf)), True


def _zoom_for(rect, width=None, budget=PIXEL_BUDGET):
    """Масштаб под целевую ширину, но не дороже бюджета пикселей."""
    if rect.width <= 0 or rect.height <= 0:
        return 1.0
    zoom = (width / rect.width) if width else 1.0
    limit = (budget / (rect.width * rect.height)) ** 0.5
    return max(min(zoom, limit), 0.02)


def page_image(pdf, page: int, width: int = OVERVIEW_WIDTH,
               budget: int = PIXEL_BUDGET) -> PageImage:
    """Лист целиком. page — номер страницы PDF, начиная с 1."""
    doc, own = _open(pdf)
    try:
        p = doc[page - 1]
        zoom = _zoom_for(p.rect, width, budget)
        pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return PageImage(png=pix.tobytes('png'), width=pix.width,
                         height=pix.height, zoom=zoom, rotation=p.rotation)
    finally:
        if own:
            doc.close()


def page_crop(pdf, page: int, box, width: int = 1600,
              budget: int = PIXEL_BUDGET) -> PageImage:
    """Кроп видимой области под зум.

    box — (x0, y0, x1, y1) в долях листа, как их видит эксперт. Кроп
    вчетверо мельче листа рисуется за сотые доли секунды, поэтому зум
    делается им, а не тайлами.
    """
    doc, own = _open(pdf)
    try:
        p = doc[page - 1]
        r = p.rect
        x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in box]
        if x1 - x0 < 0.005 or y1 - y0 < 0.005:      # защита от нулевой области
            x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
        clip = fitz.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height,
                         r.x0 + x1 * r.width, r.y0 + y1 * r.height)
        zoom = _zoom_for(clip, min(width, MAX_CROP_WIDTH), budget)
        pix = p.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
        return PageImage(png=pix.tobytes('png'), width=pix.width,
                         height=pix.height, zoom=zoom, rotation=p.rotation)
    finally:
        if own:
            doc.close()


def _norm(rect, page):
    """Прямоугольник из поиска по тексту -> доли листа, как его видит эксперт.

    Текстовый слой живёт в неповёрнутых координатах, а `page.rect` — в
    повёрнутых: на листе ЭОМ с поворотом 90° сырой поиск даёт y = 1.16,
    то есть «за краем листа». `page.rotation_matrix` — это и есть тот
    единственный перевод, о котором предупреждает план.
    """
    r = rect * page.rotation_matrix
    pr = page.rect
    return {'x': round((r.x0 - pr.x0) / pr.width, 5),
            'y': round((r.y0 - pr.y0) / pr.height, 5),
            'w': round(abs(r.width) / pr.width, 5),
            'h': round(abs(r.height) / pr.height, 5)}


def to_pdf_rect(page, anchor):
    """Доли листа -> прямоугольник PDF. Нужен для аннотаций в PDF.

    Обратная к `_norm` и единственная: перевод систем координат делается
    в одном месте, иначе поворот листа всплывает багом в каждом втором
    экране.
    """
    r = page.rect
    x = r.x0 + float(anchor.get('x', 0)) * r.width
    y = r.y0 + float(anchor.get('y', 0)) * r.height
    w = float(anchor.get('w', 0)) * r.width
    h = float(anchor.get('h', 0)) * r.height
    if w <= 0 or h <= 0:                     # точка — маленький квадрат вокруг
        side = min(r.width, r.height) * 0.012
        return fitz.Rect(x - side, y - side, x + side, y + side)
    return fitz.Rect(x, y, x + w, y + h)


# марка на чертеже подписана без «мусорных» хвостов: ищем ядро без пробелов
_TRIM = re.compile(r'[\s ]+')


def find_marks(pdf, page: int, marks, limit: int = 40) -> dict:
    """Где марки подписаны на листе. -> {марка: [{x,y,w,h}, ...]} в долях.

    Это то, что превращает расхождение из числа в картинку: машина уже
    знает, что марка на листе встречается, — здесь она запоминает где.

    Важно понимать границу: поиск находит **подписи**, а не оборудование.
    Если проектировщик марку не подписал, здесь будет пусто, и это ровно
    тот случай, который сверка называет «нет на чертежах».
    """
    doc, own = _open(pdf)
    out = {}
    try:
        p = doc[page - 1]
        r = p.rect
        for mark in marks:
            text = _TRIM.sub(' ', (mark or '')).strip()
            if len(text) < 2:
                continue
            try:
                hits = p.search_for(text, quads=False)
            except Exception:
                hits = []
            if not hits:
                continue
            out[mark] = [_norm(h, p) for h in hits[:limit]]
        return out
    finally:
        if own:
            doc.close()


def page_count(pdf) -> int:
    doc, own = _open(pdf)
    try:
        return len(doc)
    finally:
        if own:
            doc.close()
