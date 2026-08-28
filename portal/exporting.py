# -*- coding: utf-8 -*-
"""Выгрузка в Excel — в тех же стилях, что отчёт сверки (agent/report.py)."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from agent.api import KIND_LABELS, KIND_ORDER

from .flags import document_flags
from .nomenclature import EXCLUDED

FONT = 'Arial'
HDR_FILL = PatternFill('solid', fgColor='D9E1F2')
MUTED = Font(name=FONT, size=9, color='808080')
LEVEL_FILL = {
    'g': PatternFill('solid', fgColor='C6EFCE'),
    'y': PatternFill('solid', fgColor='FFEB9C'),
    'r': PatternFill('solid', fgColor='FFC7CE'),
}


def _sheet(wb, title, headers, widths):
    ws = wb.create_sheet(title)
    for i, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(1, i, h)
        c.font = Font(name=FONT, bold=True, size=10)
        c.fill = HDR_FILL
        c.alignment = Alignment(wrap_text=True, vertical='center')
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(headers))}1'
    return ws


def workbook_bytes(project, submission, documents, rows, totals):
    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet('Сводка')
    ws.column_dimensions['A'].width = 46
    ws.column_dimensions['B'].width = 60
    lines = [
        ('Объект', project.name),
        ('Шифр', project.code),
        ('Проектное бюро', project.bureau),
        ('Подача', submission.label if submission else ''),
        ('Томов в комплекте', len(documents)),
        ('Листов всего', sum(d.pages_total or 0 for d in documents)),
        ('Позиций в номенклатуре', totals['total']),
        ('— без марки (машинной сверке не поддаются)', totals['no_mark']),
        ('— дубли между разделами', totals['duplicates']),
        ('— исключено изменениями', totals['excluded']),
    ]
    for i, (k, v) in enumerate(lines, 1):
        ws.cell(i, 1, k).font = Font(name=FONT, bold=(i == 1), size=10)
        ws.cell(i, 2, v).font = Font(name=FONT, size=10)
    note = ('Приёмка комплекта: состав томов и сводная номенклатура по данным '
            'спецификаций. Сверка с чертежами не выполнялась. Документ не '
            'является заключением экспертизы.')
    c = ws.cell(len(lines) + 2, 1, note)
    c.font = Font(name=FONT, italic=True, size=9)
    c.alignment = Alignment(wrap_text=True)

    kinds = [k for k in KIND_ORDER
             if any((d.kind_counts or {}).get(k) for d in documents)]
    ws = _sheet(wb, 'Состав комплекта',
                ['Шифр', 'Раздел', 'Изменения', 'Файл', 'Листов']
                + [KIND_LABELS[k] for k in kinds]
                + ['Готовность', 'Флаги'],
                [26, 28, 14, 40, 9] + [13] * len(kinds) + [14, 60])
    for d in documents:
        fl = document_flags(d.capabilities or {}, d.section)
        level = max((f['level'] for f in fl), key=lambda x: 'gyr'.index(x)) if fl else 'g'
        row = ([d.cipher or d.filename, d.section_label or d.section, d.revision,
                d.filename, d.pages_total or 0]
               + [(d.kind_counts or {}).get(k, 0) for k in kinds]
               + [{'g': 'готов', 'y': 'с оговоркой', 'r': 'требует глаз'}[level],
                  '; '.join(f['label'] for f in fl)])
        ws.append(row)
        i = ws.max_row
        for j in range(1, len(row) + 1):
            cell = ws.cell(i, j)
            cell.font = Font(name=FONT, size=9)
            cell.alignment = Alignment(wrap_text=(j in (4, len(row))), vertical='top')
        ws.cell(i, len(row) - 1).fill = LEVEL_FILL[level]

    ws = _sheet(wb, 'Номенклатура',
                ['Марка', 'Наименование', 'Ед.', 'Кол-во', 'Разделы', 'Томов',
                 'Листы спец.', 'Отметки'],
                [28, 60, 8, 11, 16, 8, 20, 34])
    for r in rows:
        ws.append([r.mark or '—', r.name, r.unit, r.qty if r.qty else None,
                   ', '.join(r.sections), len(r.documents),
                   ', '.join(str(p) for p in sorted(r.pages)[:12]),
                   '; '.join(r.flags)])
        i = ws.max_row
        for j in range(1, 9):
            cell = ws.cell(i, j)
            cell.font = MUTED if EXCLUDED in r.flags else Font(name=FONT, size=9)
            cell.alignment = Alignment(wrap_text=(j == 2), vertical='top')

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
