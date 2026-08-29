# -*- coding: utf-8 -*-
"""Проверки картинки листа: масштаб, координаты, поиск марок.

Запуск:  python -m agent.tests_render

Главное здесь — координаты. Листы в комплекте повёрнуты, текстовый слой
живёт в неповёрнутой системе, а эксперт кликает по тому, что видит.
Если этот перевод разъедется, метки замечаний окажутся не там, где их
поставили, и доверие к меткам будет потеряно целиком.
"""
import os
import resource
import time

import pymupdf as fitz

from .render import (PIXEL_BUDGET, _norm, find_marks, page_crop, page_image,
                     to_pdf_rect)

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Example')

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def run():
    path = os.path.join(BASE, 'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf')
    if not os.path.exists(path):
        print('  пропущен реальный том (нет файла)')
        return 0

    doc = fitz.open(path)

    # --- бюджет пикселей: самый большой лист комплекта не должен съедать память
    big = max(range(len(doc)), key=lambda i: doc[i].rect.width * doc[i].rect.height)
    t0 = time.time()
    im = page_image(doc, big + 1)
    dt = time.time() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    check(im.width * im.height <= PIXEL_BUDGET * 1.02,
          f'лист {big + 1} отрисован в {im.width * im.height / 1e6:.0f} Мпикс — '
          'бюджет не сработал')
    check(dt < 5, f'самый большой лист рисуется {dt:.1f} с')
    check(rss < 1200, f'пик памяти {rss:.0f} МБ на одном листе')

    # --- обзор обычного листа: читаемый размер и вменяемый вес
    im = page_image(doc, 21)
    check(im.width >= 1800, f'обзорная картинка узкая: {im.width} px')
    check(len(im.png) < 3 * 1024 * 1024, f'обзор весит {len(im.png)//1024} КБ')
    check(im.png[:4] == b'\x89PNG', 'обзор — не png')

    # --- кроп: дешевле обзора и не выходит за лист
    t0 = time.time()
    cr = page_crop(doc, 21, (0.3, 0.3, 0.5, 0.5), width=1600)
    check(time.time() - t0 < 1.0, 'кроп рисуется дольше секунды')
    check(cr.width <= 1600, 'кроп шире запрошенного')
    # вырожденная рамка не должна ронять рендер
    cr = page_crop(doc, 21, (0.5, 0.5, 0.5, 0.5), width=800)
    check(cr.width > 0, 'нулевая рамка уронила кроп')

    # --- поиск марок: координаты внутри листа, а не «за краем»
    hits = find_marks(doc, 21, ['ЩМ08'])
    check('ЩМ08' in hits and hits['ЩМ08'], 'марка ЩМ08 на листе 21 не найдена')
    for h in hits.get('ЩМ08', []):
        check(0 <= h['x'] <= 1 and 0 <= h['y'] <= 1,
              f'координаты марки вне листа: {h} — потерян поворот листа')

    # --- перевод в координаты PDF и обратно
    page = doc[20]
    check(page.rotation == 90, 'ожидался повёрнутый лист — проверка теряет смысл')
    a = hits['ЩМ08'][0]
    back = _norm(to_pdf_rect(page, a) * ~page.rotation_matrix, page)
    check(all(abs(a[k] - back[k]) < 1e-3 for k in 'xywh'),
          f'координаты не пережили круг через PDF: {a} -> {back}')

    # --- чего нет, того нет: несуществующая марка не выдумывается
    check(not find_marks(doc, 21, ['ЫЫЫ-000']), 'найдена несуществующая марка')
    doc.close()

    if FAILS:
        print('ОШИБКИ:')
        for f in FAILS:
            print(' -', f)
    else:
        print('agent.render: все проверки пройдены')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(run())
