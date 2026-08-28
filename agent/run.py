# -*- coding: utf-8 -*-
"""Агент сверки: PDF проекта -> Excel-отчёт о расхождениях.

Запуск:  python -m agent.run <файл.pdf|папка> [-o выходная_папка]
"""
import argparse
import sys
from pathlib import Path

import pymupdf as fitz

from .extract import classify_pages, parse_spec
from .match import (assembly_pages, build_doc_aliases, build_vocab,
                    build_m_vocab, canon_mark, caption_counts_on_pages,
                    count_on_pages, count_lengths_on_pages, page_multiplier,
                    reconcile, tag_presence_counts)
from .measure import measure_plan_pages, map_measured_to_mkeys
from .cable_journal import journal_pages, parse_journal
from .lighting import parse_lighting_lists
from .devices import count_devices, match_spec_devices, spec_device_key
from .report import write_report


def process(pdf_path: Path, out_dir: Path):
    doc = fitz.open(pdf_path)
    pages = classify_pages(doc)
    spec_pages = [p['page'] for p in pages if p['kind'] == 'spec']
    if not spec_pages:
        print(f'  ! {pdf_path.name}: спецификация не найдена, пропуск')
        return None
    items = parse_spec(doc, spec_pages)
    vocab = build_vocab(items)
    mvocab = build_m_vocab(items)
    plan_pages = [p for p in pages if p['kind'] == 'plan']
    schema_pages = [p for p in pages if p['kind'] == 'schema']
    doc_aliases = build_doc_aliases(doc, plan_pages + schema_pages, vocab)
    asm = assembly_pages(doc, plan_pages + schema_pages)
    pc, praw, pd_, pasm = count_on_pages(doc, plan_pages, vocab, doc_aliases, asm)
    sc, sraw, sd_, sasm = count_on_pages(doc, schema_pages, vocab, doc_aliases, asm)
    tsums, tdetail = tag_presence_counts(doc, plan_pages + schema_pages, vocab)
    csums, cdetail = caption_counts_on_pages(
        doc, [p for p in plan_pages + schema_pages if p['page'] not in asm],
        vocab, doc_aliases)
    # листы типовых узлов повторяют один и тот же фрагмент — в метраж не идут
    pl, pp, pld = count_lengths_on_pages(
        doc, [p for p in plan_pages if p['page'] not in asm], mvocab)
    sl, sp, sld = count_lengths_on_pages(
        doc, [p for p in schema_pages if p['page'] not in asm], mvocab)
    measured, measured_pages = measure_plan_pages(doc, plan_pages, page_multiplier)
    plan_meas = map_measured_to_mkeys(measured, mvocab, items)
    jpages = journal_pages(doc)
    jsums, jdetail = parse_journal(doc, jpages) if jpages else ({}, {})
    lsums, ldetail = parse_lighting_lists(doc, plan_pages + schema_pages)
    lsums = {k.replace(' ', ''): v for k, v in lsums.items()}
    ldetail = {k.replace(' ', ''): v for k, v in ldetail.items()}
    dcounts, ddetail = count_devices(doc, schema_pages + plan_pages)
    spec_marks = {}
    for it in items:
        mark = (it.get('mark') or '').strip()
        if mark and not it.get('excluded') and spec_device_key(mark):
            spec_marks[canon_mark(mark)] = mark
    dsums = match_spec_devices(dcounts, ddetail, spec_marks)
    rows, unrows = reconcile(items, pc, pd_, sc, sd_, vocab, praw, sraw,
                             journal_sums=jsums, journal_detail=jdetail,
                             light_sums=lsums, light_detail=ldetail,
                             device_sums=dsums,
                             plan_asm=pasm, schema_asm=sasm,
                             caption_sums=csums, caption_detail=cdetail,
                             tag_sums=tsums, tag_detail=tdetail,
                             mvocab=mvocab,
                             plan_len=pl, plan_pres=pp, plan_len_detail=pld,
                             schema_len=sl, schema_pres=sp, schema_len_detail=sld,
                             plan_meas=plan_meas, measured_pages=measured_pages)
    out = out_dir / (pdf_path.stem + '_сверка.xlsx')
    notes = ('Штучные позиции (шт./компл.): кол-во = число вхождений марки на листах '
             'Планов/Схем. Для листов типовых этажей (напр. «План 3-11 этажа») применён '
             'множитель числа этажей; колонки «без множителя» показывают сырой счёт; '
             'совпадение засчитывается по любому из двух вариантов. '
             'Метровые позиции (м/км): кол-во = сумма длин из подписей вида «Марка - 5 м» '
             'на чертежах, допуск ±2%; в колонках «число меток» — сколько раз марка '
             'встречена. Если длины не подписаны, метраж труб измеряется по векторной '
             'геометрии планов (Revit 1:1): масштаб листа определяется по размерным '
             'цепочкам, сегменты труб относятся к ближайшей метке диаметра; статус '
             '«(по измерению)», допуск ±15%. Вертикальные стояки в планах не видны и в '
             'измеренный метраж не входят. '
             'Кабели (ЭОМ и слаботочка): если в комплекте есть кабельный журнал '
             '(.КЖ), метраж берётся из него — сумма длин по ключу «марка + сечение» '
             'с учётом кратности «N(...)»; сверка точная, статус '
             '«(по кабельному журналу)». '
             'Светильники: если на планах есть «Ведомость осветительного '
             'оборудования», количество берётся из её колонки «Кол.» с учётом '
             'множителя этажей — статус «(по ведомости освещения)». '
             'Аппараты защиты (ЭОМ): на принципиальных схемах они подписаны серией '
             'и параметрами отдельными строками («ВА-105 1Р», «In=10А», «Ir=B10А»), '
             'поэтому артикул спецификации («ВА105-1P-010A-B») разбирается на серию, '
             'полюсность, номинал и характеристику — статус «(по схемам аппаратов)». '
             'Количественные подписи на чертежах: если рядом с маркой стоит '
             '«(10 шт.)» или «- 3 шт.» (схемы компоновки УЭРМ, однолинейные '
             'схемы, узлы), количество берётся из подписи, а не из числа '
             'вхождений марки; для длин учитывается кратность вида '
             '«Труба ВГП 25х3,2 - 2 м (8 шт.)». '
             'Листы типовых узлов: подписи «- N шт.» перечисляют состав одного '
             'узла, поэтому марки, встреченные только там, получают статус '
             '«узел: кол-во на 1 узел» вместо расхождения. '
             'Составные изделия («в составе:»): сверяется головная позиция '
             '(щит, панель), а её комплектующие (корпуса, автоматы, клеммы, '
             'сальники) выводятся в «Непроверяемые» — на чертежах они не '
             'подписываются. Если в колонке марки стоит ссылка на лист '
             '(«ПР-01/24-1-ЭОМ лист 14»), обозначение берётся из «Примечания» '
             '(«ЩМк»), диапазоны «ЩМ08..ЩМ12» раскрываются. '
             'Марка сопоставляется также без вендорного хвоста и исполнения '
             '(«ЩРн-12 IP31 EKF PROxima» -> «ЩРН-12»); обрывки текста печати, '
             'попавшие в ячейки таблицы, отбрасываются. '
             'Позиции, исключённые изменениями (зачёркнутые), не учитываются; '
             'на повёрнутых листах учитываются вертикальные зачёркивания.')
    write_report(out, pdf_path.stem, rows, unrows, pages, notes)
    n_bad = sum(1 for r in rows if r['status'] in ('расхождение', 'нет на чертежах'))
    print(f'  {pdf_path.name}: {len(items)} позиций, сверено {len(rows)} марок, '
          f'проблемных {n_bad}, непроверяемых {len(unrows)} -> {out.name}')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('-o', '--out', default=None)
    a = ap.parse_args()
    src = Path(a.path)
    pdfs = sorted(src.glob('*.pdf')) if src.is_dir() else [src]
    out_dir = Path(a.out) if a.out else (src if src.is_dir() else src.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        try:
            process(pdf, out_dir)
        except Exception as e:
            print(f'  ! {pdf.name}: ошибка {e}', file=sys.stderr)


if __name__ == '__main__':
    main()
