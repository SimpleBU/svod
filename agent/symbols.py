# -*- coding: utf-8 -*-
"""Картинки условных обозначений: вырезаем символ со страницы PDF.

`agent.general` находит строки таблицы УГО и прямоугольник ячейки, где
стоит символ. Здесь эта ячейка превращается в PNG: у половины обозначений
графика векторная и текстом не извлекается вообще — вырезать картинкой
единственный способ показать эксперту легенду тома.

Порядок работы с координатами: `find_tables` отдаёт прямоугольники в той же
системе, что и `page.rect`, то есть уже с учётом поворота листа. Поэтому
клип передаётся в `get_pixmap` как есть, а `rotation` в строке остаётся
как признак для отладки, а не как поправка.

Пустая ячейка — нормальный случай: обозначение бывает чисто текстовым
(В1, РМ-1С). Тогда картинка не пишется, и портал показывает подпись.
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz

DPI = 150
PAD = 2.0           # запас вокруг ячейки, пункты: символ бывает вплотную к рамке
EDGE = 8            # полоса у края, где сплошная линия считается разлиновкой:
                    # запас PAD плюс толщина самой линии
MAX_PX = 320        # шире не нужно: в интерфейсе плитка ~80 px
INK = 245           # темнее этого считается краской, светлее — фоном
MIN_INK_RATIO = 0.002   # меньше — ячейка пустая, картинку не пишем
TRIM = 0.01         # доля краски, которую разрешено срезать с каждого края
SPARSE = 0.05       # плотность краски, ниже которой рамку обрезаем жёстче


@dataclass
class SymbolImage:
    name: str
    code: str
    page: int
    png: bytes | None
    width: int = 0
    height: int = 0
    blank: bool = False     # в ячейке нет графики, обозначение текстовое


def _ink_bbox(pix, trim=TRIM):
    """Границы краски в пиксельных координатах. -> (x0, y0, x1, y1) | None.

    Границы берутся не по крайнему тёмному пикселю, а по накопленной доле
    краски: одинокая точка от соседней разлиновки не должна растягивать
    плитку на всю ячейку. Линейные обозначения («прокладка кабеля в трубе»)
    при этом остаются целыми — у них краска размазана по всей ширине.

    Считаем полным проходом: ячейка УГО — сотня на полсотни пикселей,
    это дешевле, чем тянуть numpy в зависимости ради тридцати картинок.
    """
    w, h, n = pix.width, pix.height, pix.n
    data, stride = pix.samples, pix.stride
    cols, rows, ink = [0] * w, [0] * h, 0
    for y in range(h):
        base = y * stride
        for x in range(w):
            p = base + x * n
            if data[p] < INK or (n >= 3 and (data[p + 1] < INK or data[p + 2] < INK)):
                cols[x] += 1
                rows[y] += 1
                ink += 1
    # линии разлиновки таблицы у самого края — не часть символа.
    # Сплошную линию внутри ячейки (обозначение прокладки кабеля) не трогаем:
    # она не прижата к краю.
    edge_x = list(range(min(EDGE, w))) + list(range(max(0, w - EDGE), w))
    edge_y = list(range(min(EDGE, h))) + list(range(max(0, h - EDGE), h))
    rule_x = [x for x in edge_x if cols[x] >= h * 0.9]
    rule_y = [y for y in edge_y if rows[y] >= w * 0.9]
    # горизонтальная линия добавляет по одному пикселю в каждую колонку —
    # без этой поправки любая колонка выглядит «непустой» и плитка
    # растягивается на всю ячейку
    for x in range(w):
        cols[x] = max(0, cols[x] - len(rule_y))
    for y in range(h):
        rows[y] = max(0, rows[y] - len(rule_x))
    for x in rule_x:
        cols[x] = 0
    for y in rule_y:
        rows[y] = 0
    ink = sum(cols)
    if ink < w * h * MIN_INK_RATIO:
        return None
    # если внутри рамки почти нет краски, её растянула случайная метка
    # с соседней колонки — обрезаем жёстче. Линейные обозначения этим
    # не задеты: у них краска размазана по всей ширине и плотность высокая
    box = _box(cols, rows, ink, trim)
    x0, y0, x1, y1 = box
    if ink / max(1, (x1 - x0 + 1) * (y1 - y0 + 1)) < SPARSE:
        box = _box(cols, rows, ink, trim * 6)
    return box


def _box(cols, rows, ink, trim):
    return (_edge(cols, ink, trim, False), _edge(rows, ink, trim, False),
            _edge(cols, ink, trim, True), _edge(rows, ink, trim, True))


def _edge(counts, ink, trim, from_end):
    """Первый индекс, за которым накопилось больше `trim` доли всей краски."""
    limit = ink * trim
    acc = 0
    order = range(len(counts) - 1, -1, -1) if from_end else range(len(counts))
    last = 0
    for i in order:
        acc += counts[i]
        last = i
        if acc > limit:
            break
    return last


def crop(page, bbox, dpi=DPI, pad=PAD):
    """PNG ячейки, обрезанный по краске. -> (bytes, w, h) | (None, 0, 0)."""
    rect = fitz.Rect(bbox) + (-pad, -pad, pad, pad)
    rect = rect & page.rect
    if rect.is_empty:
        return None, 0, 0
    pix = page.get_pixmap(clip=rect, dpi=dpi)
    box = _ink_bbox(pix)
    if box is None:
        return None, 0, 0
    x0, y0, x1, y1 = box
    scale = dpi / 72.0
    # обратно в пункты страницы, с полем в один пиксель по краям
    tight = fitz.Rect(rect.x0 + (x0 - 1) / scale, rect.y0 + (y0 - 1) / scale,
                      rect.x0 + (x1 + 2) / scale, rect.y0 + (y1 + 2) / scale)
    tight = tight & page.rect
    if tight.is_empty:
        return None, 0, 0
    out_dpi = dpi
    if tight.width * scale > MAX_PX:
        out_dpi = max(72, int(MAX_PX * 72 / tight.width))
    pix = page.get_pixmap(clip=tight, dpi=out_dpi)
    return pix.tobytes('png'), pix.width, pix.height


def render(pdf, symbols, dpi=DPI, progress=None) -> list[SymbolImage]:
    """Картинки для строк таблицы УГО, найденных `agent.general`."""
    own = not hasattr(pdf, 'load_page')
    doc = fitz.open(str(pdf)) if own else pdf
    out = []
    try:
        total = len(symbols) or 1
        for i, s in enumerate(symbols, 1):
            page = doc[s.page - 1]
            png, w, h = crop(page, s.bbox, dpi=dpi)
            out.append(SymbolImage(name=s.name, code=s.code, page=s.page,
                                   png=png, width=w, height=h, blank=png is None))
            if progress:
                progress(i, total)
        return out
    finally:
        if own:
            doc.close()


def main():
    """Отладочный запуск: python -m agent.symbols <файл.pdf> [папка]"""
    import os
    import sys
    from .general import general

    path = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else 'symbols_out'
    os.makedirs(outdir, exist_ok=True)
    g = general(path)
    imgs = render(path, g.symbols)
    for i, im in enumerate(imgs, 1):
        mark = 'пусто' if im.blank else f'{im.width}x{im.height}'
        print(f'{i:3} {mark:>9}  {im.code!r:12} {im.name[:56]}')
        if im.png:
            name = f'{i:02}.png'
            with open(os.path.join(outdir, name), 'wb') as f:
                f.write(im.png)
    print(f'символов: {len(imgs)}, с графикой: {sum(1 for i in imgs if not i.blank)}'
          f' -> {outdir}')


if __name__ == '__main__':
    main()
