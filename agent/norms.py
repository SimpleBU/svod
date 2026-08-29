# -*- coding: utf-8 -*-
"""Нормативная база тома: что бюро объявило в общих указаниях.

Два источника, оба на листе общих данных:
  1. текст общих указаний — «Чертежи разработаны в соответствии с ТНПА: …»;
  2. ведомость ссылочных документов — в томах автоматизации нормативы
     перечислены именно там, а не в тексте.

Статус документа берётся из `norms_registry.yaml` — реестра, который ведёт
эксперт руками. Машина не выдумывает статусов: чего нет в реестре, то так
и показывается — «нет в реестре». Это осознанно: неверное «заменён»
дороже честного «не знаю».

LLM не используется.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'norms_registry.yaml')

# Регистр важен: без него «СП» ловится внутри слов «спецификация», «стояк».
# Номер обязателен и начинается с цифры — «СПДС» и «Стоянки» отсеиваются сами.
PREFIXES = ('ГОСТ Р МЭК', 'ГОСТ Р ИСО', 'ГОСТ Р', 'ГОСТ', 'СП', 'СНиП', 'СанПиН',
            'СНиП РК', 'ВСН', 'РД', 'СТО', 'ТР ЕАЭС', 'ТР ТС', 'НПБ', 'ПБ')
P_NORM = re.compile(r'(?<![А-ЯЁA-Za-zа-яё])(' + '|'.join(
    p.replace(' ', r'\s+') for p in PREFIXES) + r')\s*(\d[\d.\-/]*\d\*?)')
P_FZ = re.compile(r'[N№]\s*(\d{1,4})\s*[-\s]?\s*ФЗ')
P_PP = re.compile(r'[Пп]остановлени\w*\s+[Пп]равительства[^;\n]{0,90}?[N№]\s*(\d{1,4})')
P_PUE = re.compile(r'(?<![А-ЯЁA-Za-zа-яё])ПУЭ|Правила\s+устройства\s+электроустановок', re.I)
# «СП 51.13330.2011 „Защита от шума“. Актуализированная редакция СНиП 23-03-2003» —
# здесь СНиП часть названия свода правил, а не самостоятельная ссылка,
# и замечанием это быть не должно
P_CONTEXT = re.compile(r'актуализирован\w*\s+редакци|взамен', re.I)
CONTEXT_WINDOW = 60
# оговорка относится только к старым документам: «актуализированная редакция
# СНиП …» — часть названия свода правил. Сам свод правил, стоящий рядом,
# ссылкой быть не перестаёт
CONTEXT_PREFIXES = ('СНИП', 'ВСН', 'НПБ', 'ПБ')

STATUSES = ('active', 'superseded', 'cancelled', 'verify')
UNKNOWN = 'unknown'

FROM_GUIDELINES = 'указания'
FROM_REFS = 'ссылочные'


@dataclass
class NormRef:
    code: str                       # как показываем: «СП 484.1311500.2020»
    key: str                        # как сравниваем: «СП4841311500.2020» без разделителей
    status: str = UNKNOWN
    title: str = ''
    replaced_by: str = ''
    note: str = ''
    sources: list[str] = field(default_factory=list)
    contextual: bool = False        # упомянут внутри названия другого документа

    @property
    def known(self):
        return self.status != UNKNOWN

    def as_dict(self):
        d = asdict(self)
        d['known'] = self.known
        return d


def _key(code):
    """Ключ сравнения: «СП 52.13330-2016» и «СП 52.13330.2016» — одно и то же."""
    return re.sub(r'[\s.\-–—*]', '', (code or '').upper())


def _display(prefix, number):
    prefix = re.sub(r'\s+', ' ', prefix).strip()
    return prefix + ' ' + number


def extract(text):
    """Перечень нормативов из текста. -> [(код, упомянут_в_названии_другого)]."""
    text = text or ''
    out, seen = [], set()

    def add(code, pos=None):
        k = _key(code)
        if not k or k in seen:
            return
        seen.add(k)
        ctx = bool(pos is not None
                   and k.startswith(CONTEXT_PREFIXES)
                   and P_CONTEXT.search(text[max(0, pos - CONTEXT_WINDOW):pos]))
        out.append((code, ctx))

    for m in P_NORM.finditer(text):
        add(_display(m.group(1), m.group(2).rstrip('.,;')), m.start())
    for m in P_FZ.finditer(text):
        add('№ ' + m.group(1) + '-ФЗ')
    for m in P_PP.finditer(text):
        add('Постановление Правительства РФ № ' + m.group(1))
    if P_PUE.search(text):
        add('ПУЭ')
    return out


def load_registry(path=REGISTRY_PATH):
    """-> {ключ: запись}. Реестра может не быть — тогда все статусы unknown."""
    try:
        import yaml
    except ImportError:
        return {}
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or []
    reg = {}
    for item in data:
        code = (item or {}).get('code')
        if not code:
            continue
        status = item.get('status', UNKNOWN)
        reg[_key(code)] = {
            'code': code,
            'status': status if status in STATUSES else UNKNOWN,
            'title': item.get('title', ''),
            'replaced_by': item.get('replaced_by', ''),
            'note': item.get('note', ''),
        }
    return reg


def norms(guidelines_text='', refs=None, registry=None) -> list[NormRef]:
    """Нормативы тома со статусами.

    refs — список DocRef из agent.general: в томах автоматизации нормативы
    объявлены ведомостью ссылочных документов, а не текстом указаний.
    """
    registry = load_registry() if registry is None else registry
    found = {}

    def collect(text, source):
        for code, ctx in extract(text):
            k = _key(code)
            cur = found.get(k)
            if cur is None:
                found[k] = NormRef(code=code, key=k, sources=[source], contextual=ctx)
            else:
                if source not in cur.sources:
                    cur.sources.append(source)
                cur.contextual = cur.contextual and ctx

    collect(guidelines_text, FROM_GUIDELINES)
    for d in refs or []:
        collect(f'{d.code} {d.title}', FROM_REFS)

    out = []
    for ref in found.values():
        item = registry.get(ref.key)
        if item:
            ref.code = item['code'] or ref.code      # написание из реестра
            ref.status = item['status']
            ref.title = item['title']
            ref.replaced_by = item['replaced_by']
            ref.note = item['note']
        out.append(ref)
    out.sort(key=lambda r: (r.status == UNKNOWN, r.code))
    return out


def problems(items):
    """Только то, из чего получается замечание эксперту.

    Документ, упомянутый внутри названия другого («актуализированная
    редакция СНиП 23-03-2003»), замечанием не считается: это часть
    наименования свода правил, а не самостоятельная ссылка.
    """
    return [r for r in items
            if r.status in ('superseded', 'cancelled', 'verify') and not r.contextual]


def main():
    """Отладочный запуск: python -m agent.norms <файл.pdf>"""
    import sys
    from .general import general

    g = general(sys.argv[1])
    items = norms(g.guidelines_text, g.refs)
    labels = {'active': 'действует', 'superseded': 'заменён',
              'cancelled': 'отменён', 'verify': 'проверить', UNKNOWN: 'нет в реестре'}
    for r in items:
        tail = ' -> ' + r.replaced_by if r.replaced_by else ''
        ctx = ' (в названии другого документа)' if r.contextual else ''
        print(f'  {labels[r.status]:>13}  {r.code:<28} {r.title[:38]}{tail}{ctx}'
              f'  [{", ".join(r.sources)}]')
    print(f'  всего: {len(items)}, в реестре: {sum(1 for r in items if r.known)}, '
          f'замечаний: {len(problems(items))}')


if __name__ == '__main__':
    main()
