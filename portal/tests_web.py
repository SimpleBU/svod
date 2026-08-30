# -*- coding: utf-8 -*-
"""Дымовая проверка веб-части: каждая страница отдаётся и рисуется.

Тест не про красоту, а про то, что ни один шаблон не падает и ни одна
страница не отвечает пятисоткой. Данные засеиваются похожими на реальный
том ПР-0124-3-СПСиА: расхождения паспорта, нормативы, УГО, план проверки,
строки сверки и замечания — то, на чём ломаются шаблоны.

Запуск: python -m portal.tests_web
"""
import os
import pathlib
import sys
import tempfile

TMP = pathlib.Path(tempfile.mkdtemp(prefix='svod-smoke-'))
os.environ['DATABASE_URL'] = 'sqlite:///' + str(TMP / 'smoke.db')
os.environ['STORAGE_BACKEND'] = 'local'
os.environ['STORAGE_DIR'] = str(TMP / 'files')
os.environ['RUN_MIGRATIONS'] = '0'
os.environ['INLINE_WORKER'] = '0'
os.environ['ADMIN_EMAIL'] = 'expert@example.com'
os.environ['ADMIN_PASSWORD'] = 'smoke-password-123'
os.environ.pop('REDIS_URL', None)

from fastapi.testclient import TestClient           # noqa: E402

from . import models                                # noqa: E402
from .db import SessionLocal, engine                # noqa: E402
from .models import (Base, CheckItem, CheckPlan, DeclaredSheet, DocRef,
                     Document, MatchItem, NormRef, Project, Remark,
                     RevisionEntry, Sheet, SpecItem, Submission, Symbol)
from .web.app import app                            # noqa: E402

FAILED = []


def check(cond, what):
    print(('  ok   ' if cond else '  ПАДАЕТ ') + what)
    if not cond:
        FAILED.append(what)


def seed():
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        org = models.Org(name='Внутренняя экспертиза')
        s.add(org)
        s.flush()
        p = Project(org_id=org.id, name='Мангазе на речном', code='ПР-01/24',
                    bureau='ООО "ЭНЭКА"')
        s.add(p)
        s.flush()
        sub = Submission(project_id=p.id, label='Первая подача')
        s.add(sub)
        s.flush()
        doc = Document(
            org_id=org.id, submission_id=sub.id,
            filename='ПР-0124-3-СПСиА.pdf', cipher='ПР-0124-3-СПСиА',
            section='', section_label='', revision='Изм. 1',
            file_key='documents/1.pdf', pages_total=22, status=models.DONE,
            kind_counts={'plan': 8, 'schema': 1, 'spec': 3, 'general': 2,
                         'cover': 3, 'other': 5},
            capabilities={'spec': True},
            findings=[
                {'code': 'norm', 'level': 'amber',
                 'text': 'СП 35-101-2001 — редакция 2001 года; тему закрывает '
                         'СП 59.13330 — проверить, нужна ли ссылка'},
                {'code': 'symbol_unused', 'level': 'amber', 'sheets': [4],
                 'text': '5 обозначений из легенды не встречаются в '
                         'спецификации тома: АРМ, РМ-4К, KLZ, KLO, ШУВ'},
            ],
            match_stats={'plan_keys': 11, 'uncheckable': 16},
            pages_rendered=22)
        s.add(doc)
        s.flush()

        for n in range(1, 10):
            s.add(DeclaredSheet(document_id=doc.id, no=n, title=f'Лист {n}',
                                revisions=[1] if n in (1, 3, 6) else []))
        s.add(Sheet(document_id=doc.id, page=1, kind='cover', title='Титул'))
        s.add(Sheet(document_id=doc.id, page=4, kind='plan', title='План -1 этажа'))
        s.add(DocRef(document_id=doc.id, kind='attached', code='ПР-01/24-3-СПСиА.АЛ1',
                     title='Алгоритм работы системы', present=True))
        s.add(DocRef(document_id=doc.id, kind='attached', code='ПР-01/24-3-СПСиА.РР1',
                     title='Расчет источников питания', present=False))
        s.add(NormRef(document_id=doc.id, code='СП 484.1311500.2020',
                      title='Системы пожарной сигнализации', status='active'))
        s.add(NormRef(document_id=doc.id, code='СНиП 35-01-2001',
                      title='Доступность зданий', status='superseded',
                      replaced_by='СП 59.13330.2020'))
        s.add(NormRef(document_id=doc.id, code='СП 35-101-2001',
                      title='Проектирование зданий', status='verify'))
        s.add(Symbol(document_id=doc.id, code='РМ-4К', name='Релейный модуль', used=False))
        s.add(Symbol(document_id=doc.id, code='АМ-1', name='Адресная метка', used=True))
        s.add(RevisionEntry(document_id=doc.id, number=1, sheets='1 3 6',
                            content='Добавлены реле РМ-1С', basis='доп. задание АК'))
        s.add(SpecItem(document_id=doc.id, page=20, pos='8',
                       name='Адресный релейный модуль на 1 выход',
                       mark='РМ-1С-R3', canon_mark='рм1сr3', unit='шт.', qty=64))
        s.add(SpecItem(document_id=doc.id, page=20, pos='',
                       name='Гайка М8', mark='', canon_mark='', unit='шт.', qty=148))

        plan = CheckPlan(org_id=org.id, document_id=doc.id, version=1,
                         status=models.DRAFT)
        s.add(plan)
        s.flush()
        quote = [{'kind': 'revision', 'text': 'Исправлены марки шкафов управления ПДЗ.'}]
        for n, (cls, name, mark, eye) in enumerate([
            ('A', 'Шкаф управления пожарный, 11 кВт', 'ШУН/В-11-03-R3', False),
            ('A', 'Шкаф управления пожарный, 22 кВт', 'ШУН/В-22-03-R3', False),
            ('B', 'Адресный релейный модуль на 1 выход', 'РМ-1С-R3', False),
            ('C', 'Гайка М8', '', True),
        ]):
            s.add(CheckItem(
                plan_id=plan.id, key=f'k{n}', pos='SU' if cls == 'A' else '',
                name=name, mark=mark, unit='шт.', qty=10 + n, cls=cls, score=90 - n,
                reasons=[{'code': 'changed', 'text': 'затронута изменением', 'weight': 3},
                         {'code': 'top', 'text': 'топ по объёму', 'weight': 1}],
                verifiable_by=['только глазами'] if eye else ['по планам', 'по схемам'],
                evidence=quote))

        s.add(MatchItem(document_id=doc.id, kind='count', mark='РМ-1С-R3',
                        marks=['РМ-1С-R3'], names='Адресный релейный модуль на 1 выход',
                        unit='шт.', spec_qty=64, plan_qty=0, schema_qty=0,
                        status='нет на чертежах', level='red', in_plan=True,
                        spec_pages=[20], keys=['k2']))
        s.add(MatchItem(document_id=doc.id, kind='count', mark='ШУН/В-11-03-R3',
                        marks=['ШУН/В-11-03-R3'], names='Шкаф управления пожарный, 11 кВт',
                        unit='шт.', spec_qty=2, plan_qty=2, schema_qty=0,
                        status='ок (частично)', level='amber', in_plan=True,
                        spec_pages=[21], plan_pages=[9], keys=['k0']))
        s.add(MatchItem(document_id=doc.id, kind='count', mark='АМ-1-R3',
                        marks=['АМ-1-R3'], names='Метка адресная на 1 шлейф',
                        unit='шт.', spec_qty=70, plan_qty=70, schema_qty=0,
                        status='ок', level='ok', spec_pages=[20], plan_pages=[8]))

        s.add(Remark(org_id=org.id, document_id=doc.id, source='passport',
                     key='p:symbol_unused:abc0123456', status=models.OPEN,
                     level='amber', subject='Условные обозначения',
                     text='5 обозначений из легенды не встречаются в спецификации',
                     evidence='проверка паспорта тома: symbol_unused',
                     sheets=[4], page=4))
        s.commit()
        return p.id, doc.id


def main():
    project_id, doc_id = seed()
    # контекстный менеджер нужен: без него не отработает startup, а в нём
    # заводится первый пользователь из ADMIN_EMAIL/ADMIN_PASSWORD
    with TestClient(app) as c:
        run(c, project_id, doc_id)


def run(c, project_id, doc_id):
    # редиректы не проглатываем: страница, отдавшая 303 на форму входа,
    # иначе выглядит как успешно отрисованная
    c.follow_redirects = False
    r = c.post('/login', data={'email': os.environ['ADMIN_EMAIL'],
                               'password': os.environ['ADMIN_PASSWORD'], 'next': '/'})
    check(r.status_code == 303, f'вход по паролю ({r.status_code})')

    pages = [
        ('список объектов', f'/'),
        ('приёмка — поток находок', f'/projects/{project_id}?tab=intake&doc={doc_id}'),
        ('приёмка, фильтр «критично»',
         f'/projects/{project_id}?tab=intake&doc={doc_id}&flt=red'),
        ('фрагмент приёмки', f'/projects/{project_id}/intake?doc={doc_id}'),
        ('состав комплекта', f'/projects/{project_id}?tab=composition'),
        ('паспорт тома', f'/projects/{project_id}?tab=passport&doc={doc_id}'),
        ('номенклатура', f'/projects/{project_id}?tab=nomenclature'),
        ('план проверки', f'/projects/{project_id}?tab=checkplan&doc={doc_id}'),
        ('план проверки, фильтр «только A»',
         f'/projects/{project_id}?tab=checkplan&doc={doc_id}&flt=a'),
        ('сверка с чертежами', f'/projects/{project_id}?tab=match&doc={doc_id}'),
        ('сверка, фильтр «расхождения»',
         f'/projects/{project_id}?tab=match&doc={doc_id}&flt=problems'),
        ('сверка, поиск', f'/projects/{project_id}?tab=match&doc={doc_id}&q=шун'),
        ('замечания тома', f'/projects/{project_id}?tab=remarks&doc={doc_id}'),
        ('замечания подачи',
         f'/projects/{project_id}?tab=remarks&doc={doc_id}&scope=all'),
        ('лист как режим',
         f'/projects/{project_id}?tab=sheet&doc={doc_id}&page=4&from=match'),
        ('фрагмент таблицы плана', f'/projects/{project_id}/checkplan?doc={doc_id}'),
        ('фрагмент панели сверки', f'/projects/{project_id}/match?doc={doc_id}'),
        ('фрагмент номенклатуры', f'/projects/{project_id}/nomenclature'),
        ('ложные срабатывания', '/feedback'),
        ('ложные срабатывания, фильтр по сверке', '/feedback?source=match'),
        ('предпросмотр письма бюро',
         f'/projects/{project_id}/letter?doc={doc_id}&scope=doc'),
        ('новый объект', '/projects/new'),
    ]
    for what, url in pages:
        r = c.get(url)
        check(r.status_code == 200, f'{what} ({r.status_code})')

    from sqlalchemy import select
    with SessionLocal() as s:
        mid = s.scalars(select(MatchItem.id)).first()
        cid = s.scalars(select(CheckItem.id)).first()
        pid = s.scalars(select(CheckPlan.id)).first()
        rid = s.scalars(select(Remark.id)).first()

    r = c.post(f'/api/match-items/{mid}/remark', data={'status': 'open', 'flt': 'plan'})
    check(r.status_code == 200 and 'mrow-' in r.text, 'решение по строке сверки')
    r = c.post(f'/api/check-items/{cid}/decision', data={'value': 'take'})
    check(r.status_code == 200, 'галочка в плане проверки')
    r = c.post(f'/api/check-plans/{pid}/bulk',
               data={'value': 'take', 'scope': 'A'})
    check(r.status_code == 200, 'массовое действие «взять все A»')
    r = c.post(f'/api/documents/{doc_id}/passport-remark',
               data={'index': '0', 'status': 'open'})
    check(r.status_code == 200, 'замечание с расхождения паспорта')
    r = c.post(f'/api/remarks/{rid}', data={'status': 'sent'})
    check(r.status_code == 200, 'смена статуса замечания')
    body = c.get(f'/projects/{project_id}?tab=intake&doc={doc_id}').text
    key = ''
    import re as _re
    m = _re.search(r'name="key" value="([^"]+)"', body)
    key = m.group(1) if m else ''
    r = c.post(f'/api/documents/{doc_id}/finding',
               data={'key': key, 'status': 'open', 'flt': '', 'next_one': '1'})
    check(r.status_code == 200 and 'intake' in r.text, 'решение по находке в приёмке')
    r = c.get(f'/projects/{project_id}?tab=intake&doc={doc_id}&f={key}&fp=1')
    check(r.status_code == 200 and 'Почему это не расхождение' in r.text,
          'форма ложного срабатывания открывается')
    r = c.post(f'/api/documents/{doc_id}/finding',
               data={'key': key, 'status': 'dismissed', 'flt': '',
                     'reason': 'other_name',
                     'comment': 'на плане подписано без индекса R3'})
    check(r.status_code == 200, 'снятие находки как ложной тревоги')

    from .models import AlgoFeedback
    with SessionLocal() as s2:
        fb = s2.scalars(select(AlgoFeedback)).all()
    check(len(fb) == 1 and fb[0].reason == 'other_name'
          and 'R3' in fb[0].comment, 'ложное срабатывание записано с причиной')
    check(bool(fb) and fb[0].machine.get('status'),
          'машинный вывод сохранён снимком')
    r = c.get('/feedback.json')
    check(r.status_code == 200 and r.json()['count'] == 1
          and r.json()['items'][0]['comment'], 'выгрузка json содержит комментарий')
    r = c.get('/feedback.xlsx')
    check(r.status_code == 200 and len(r.content) > 4000, 'выгрузка xlsx собирается')
    r = c.post(f'/api/documents/{doc_id}/finding',
               data={'key': key, 'status': 'open', 'flt': ''})
    check(r.status_code == 200, 'возврат находки в работу')
    r = c.get('/feedback.json')
    check(r.json()['count'] == 0, 'возвращённая находка уходит из выгрузки')

    r = c.get(f'/projects/{project_id}/letter.docx?doc={doc_id}&scope=doc')
    check(r.status_code in (200, 404), f'письмо бюро ({r.status_code})')

    body = c.get(f'/projects/{project_id}?tab=match&doc={doc_id}&flt=plan').text
    check('Схемы' not in body, 'пустая колонка «Схемы» не выводится')
    check('сошлось частично' in body, 'статус «ок (частично)» переписан словами')
    check(body.count('в плане проверки') == 0,
          'тег «в плане проверки» скрыт при том же фильтре')

    body = c.get(f'/projects/{project_id}?tab=nomenclature').text
    check('0 дублей' not in body and '0 исключено' not in body,
          'нули в статистике номенклатуры не выводятся')

    body = c.get(f'/projects/{project_id}?tab=passport&doc={doc_id}').text
    check('<details' in body, 'секции паспорта свёрнуты')
    check('НОРМАТИВОВ' not in body.upper() or 'обозначений</span>' not in body,
          'пять крупных счётчиков заменены строкой состояния')

    body = c.get(f'/projects/{project_id}?tab=remarks&doc={doc_id}').text
    check('symbol_unused' not in body, 'внутренний код проверки не протекает в интерфейс')

    print()
    if FAILED:
        print(f'ПАДАЕТ: {len(FAILED)}')
        sys.exit(1)
    print('дымовая проверка пройдена')


if __name__ == '__main__':
    main()
