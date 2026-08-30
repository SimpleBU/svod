# -*- coding: utf-8 -*-
"""Веб-часть портала приёмки.

Веб никогда не открывает PDF сам: любая работа с документом — задача
в очереди. Здесь только чтение из БД, постановка задач и подписанные
ссылки на загрузку.
"""
import logging
import re
from urllib.parse import quote
from datetime import datetime

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from agent.api import KIND_LABELS, KIND_ORDER

from .. import auth
from .. import checkplan as plan_service
from .. import config, findings as finding_service, models, nomenclature
from .. import passport as passport_service
from .. import letter, match as match_service, remarks as remark_service
from .. import sheets as sheet_service
from ..db import SessionLocal, upgrade_schema
from ..exporting import passport_workbook_bytes, workbook_bytes
from ..flags import readiness, document_flags
from ..models import (CheckItem, CheckPlan, Document, MatchItem, Org, Project,
                      Remark, Run, Submission, Symbol, User)
from ..naming import parse_filename
from ..queue import enqueue_intake, enqueue_match
from ..storage import get_storage, object_key

log = logging.getLogger(__name__)
BASE = config.BASE_DIR / 'portal' / 'web'

KIND_COLORS = {
    'plan': '#4f6b8f', 'schema': '#5f7fa8', 'spec': '#8b94a1',
    'vt': '#3c4655', 'general': '#6b7482', 'appendix': '#4a5361',
    'cover': '#3a4350', 'other': '#2f3742',
}
STATE_LABELS = {
    models.NEW: ('ожидает загрузки', 'idle'),
    models.QUEUED: ('в очереди', 'busy'),
    models.RUNNING: ('обработка', 'busy'),
    models.DONE: ('разобран', 'ok'),
    models.ERROR: ('ошибка разбора', 'bad'),
}

app = FastAPI(title=config.APP_NAME, docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')


@app.middleware('http')
async def require_login(request: Request, call_next):
    """Портал закрыт целиком, кроме формы входа и статики.

    Проверка стоит здесь, а не в зависимостях каждого роута: забыть повесить
    зависимость на новый роут легко, а тихо открытый наружу портал с чужой
    документацией — слишком дорогая ошибка.
    """
    if auth.is_public(request.url.path) or request.session.get(auth.SESSION_KEY):
        return await call_next(request)
    if request.url.path.startswith('/api/'):
        return JSONResponse({'detail': 'нужно войти'}, status_code=401)
    nxt = request.url.path
    if request.url.query:
        nxt += '?' + request.url.query
    return RedirectResponse('/login?next=' + quote(nxt, safe=''), status_code=303)


# Порядок важен: Starlette разворачивает промежуточные слои в обратном порядке
# добавления, поэтому сессия подключается ПОСЛЕ проверки входа — тогда она
# оказывается снаружи и request.session уже доступен внутри неё.
app.add_middleware(SessionMiddleware, secret_key=auth.secret_key(),
                   session_cookie='svod_session', max_age=auth.SESSION_MAX_AGE,
                   same_site='lax', https_only=config.HTTPS_ONLY)
def _user_context(request: Request):
    """Кто вошёл — нужно каждому шаблону, поэтому кладём один раз здесь,
    а не дописываем в контекст каждого ответа: забыть легко."""
    if not request.session.get(auth.SESSION_KEY):
        return {'user': None}
    with SessionLocal() as s:
        return {'user': auth.current_user(request, s)}


templates = Jinja2Templates(directory=str(BASE / 'templates'),
                            context_processors=[_user_context])


def _num(v):
    """1284 -> «1 284» (узкий пробел между разрядами)."""
    if v is None or v == '':
        return ''
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f'{int(n):,}'.replace(',', ' ') if abs(n - int(n)) < 1e-9 else f'{n:.2f}'
    return s


def _mb(v):
    return f'{(v or 0) / 1048576:.1f} МБ' if v else ''


def _plural(n, one, few, many):
    """«1 том / 2 тома / 5 томов» — иначе интерфейс звучит как бланк."""
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        return many
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _initials(label):
    """«Виталий Панасенко» -> ВП, «panasenko.ctt@gmail.com» -> ПК не выйдет,
    но PA лучше, чем почта целиком в интерфейсе."""
    parts = [p for p in re.split(r'[\s._@+-]+', (label or '').strip()) if p]
    if not parts:
        return '—'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][:1] + parts[1][:1]).upper()


templates.env.filters['num'] = _num
templates.env.filters['initials'] = _initials
templates.env.filters['mb'] = _mb
templates.env.filters['plural'] = _plural
templates.env.globals['evidence'] = remark_service.evidence_text
templates.env.globals['APP_NAME'] = config.APP_NAME
templates.env.globals['ORG_NAME'] = config.ORG_NAME
templates.env.globals['KIND_LABELS'] = KIND_LABELS
templates.env.globals['config_max'] = config.MAX_UPLOAD_MB


@app.on_event('startup')
def _startup():
    logging.basicConfig(level=logging.INFO)
    if config.RUN_MIGRATIONS:
        upgrade_schema()
    with SessionLocal() as s:
        if not s.scalar(select(Org).limit(1)):
            s.add(Org(name=config.ORG_NAME))
            s.commit()
        auth.ensure_admin(s)


def db():
    return SessionLocal()


def _uid(request: Request):
    """Кто принимает решение. Нужен этапу 3: у замечания должен быть автор."""
    return request.session.get(auth.SESSION_KEY)


def current_org(s):
    org = s.scalar(select(Org).order_by(Org.id).limit(1))
    if org is None:
        org = Org(name=config.ORG_NAME)
        s.add(org)
        s.commit()
    return org


# ------------------------------------------------------------------------ вход

@app.get('/login', response_class=HTMLResponse)
def login_form(request: Request, next: str = '/'):
    if request.session.get(auth.SESSION_KEY):
        return RedirectResponse(next or '/', status_code=303)
    return templates.TemplateResponse(request, 'login.html',
                                      {'request': request, 'next': next})


@app.post('/login', response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(''), password: str = Form(''),
                 next: str = Form('/')):
    with db() as s:
        user = auth.authenticate(s, email, password)
        if user is None:
            log.warning('неудачная попытка входа: %s', (email or '')[:80])
            return templates.TemplateResponse(
                request, 'login.html',
                {'request': request, 'next': next, 'email': email,
                 'error': 'Неверная почта или пароль'}, status_code=401)
        auth.login(request, user)
        log.info('вход: %s', user.email)
    target = next if next.startswith('/') and not next.startswith('//') else '/'
    return RedirectResponse(target, status_code=303)


@app.get('/logout')
def logout(request: Request):
    auth.logout(request)
    return RedirectResponse('/login', status_code=303)


# --------------------------------------------------------------- список объектов

@app.get('/', response_class=HTMLResponse)
def projects(request: Request):
    with db() as s:
        org = current_org(s)
        rows = []
        projects = s.scalars(select(Project).where(Project.org_id == org.id)
                             .order_by(Project.id.desc())).all()
        for p in projects:
            subs = p.submissions
            last = subs[-1] if subs else None
            docs = last.documents if last else []
            rows.append({'p': p, 'submission': last, 'volumes': len(docs),
                         'state': _project_state(docs)})
        processing = sum(1 for r in rows if r['state'][1] == 'busy')
        return templates.TemplateResponse(request, 'projects.html', {
            'request': request, 'rows': rows, 'processing': processing})


def _project_state(docs):
    if not docs:
        return ('файлы не загружены', 'idle')
    if any(d.status == models.ERROR for d in docs):
        return ('ошибка в одном из томов', 'bad')
    if any(d.status in (models.QUEUED, models.RUNNING, models.NEW) for d in docs):
        n = sum(1 for d in docs if d.status == models.DONE)
        return (f'обработка, {n} из {len(docs)} готово', 'busy')
    worst = 'g'
    for d in docs:
        lvl = readiness(document_flags(d.capabilities or {}, d.section))
        worst = max(worst, lvl, key=lambda x: 'gyr'.index(x))
    return ({'g': 'разобран, готов к проверке', 'y': 'разобран, есть оговорки',
             'r': 'разобран, часть листов требует глаз'}[worst],
            {'g': 'ok', 'y': 'warn', 'r': 'bad'}[worst])


@app.get('/projects/new', response_class=HTMLResponse)
def project_new(request: Request):
    return templates.TemplateResponse(request, 'project_new.html', {'request': request})


@app.post('/projects')
def project_create(name: str = Form(...), code: str = Form(''), bureau: str = Form(''),
                   label: str = Form('Первая подача')):
    with db() as s:
        org = current_org(s)
        p = Project(org_id=org.id, name=name.strip(), code=code.strip(),
                    bureau=bureau.strip())
        s.add(p)
        s.flush()
        s.add(Submission(project_id=p.id, label=label.strip() or 'Первая подача'))
        s.commit()
        return RedirectResponse(f'/projects/{p.id}', status_code=303)


# ------------------------------------------------------------------- объект

def _load(s, project_id):
    p = s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, 'объект не найден')
    subs = p.submissions
    if not subs:
        sub = Submission(project_id=p.id, label='Первая подача')
        s.add(sub)
        s.commit()
        subs = [sub]
    return p, subs[-1]


def _runs(s, docs):
    """Последний прогон разбора по каждому тому.

    Прогоны сверки сюда не попадают: состав комплекта показывает ход
    приёмки, и запущенная сверка не должна выглядеть как повторный разбор.
    """
    if not docs:
        return {}
    ids = [d.id for d in docs]
    out = {}
    for r in s.scalars(select(Run).where(Run.document_id.in_(ids),
                                         Run.kind == 'intake')
                       .order_by(Run.id)).all():
        out[r.document_id] = r
    return out


def _composition(s, project, submission):
    docs = submission.documents
    runs = _runs(s, docs)
    items = []
    for d in docs:
        counts = d.kind_counts or {}
        total = sum(counts.values()) or 1
        breakdown = [{'label': KIND_LABELS.get(k, k), 'count': counts[k],
                      'pct': round(counts[k] * 100 / total, 2),
                      'color': KIND_COLORS.get(k, '#2f3742')}
                     for k in KIND_ORDER if counts.get(k)]
        fl = document_flags(d.capabilities or {}, d.section) if d.status == models.DONE else []
        items.append({'d': d, 'run': runs.get(d.id), 'breakdown': breakdown,
                      'flags': fl, 'level': readiness(fl) if fl else 'g',
                      'state': STATE_LABELS.get(d.status, ('', 'idle'))})
    busy = any(d.status in (models.QUEUED, models.RUNNING) for d in docs)
    parsed = sum(d.pages_total or 0 for d in docs)
    ready = sum(1 for i in items
                if i['d'].status == models.DONE and i['level'] != 'r')
    # сводка приёмки: сколько работы осталось эксперту. Этого ответа не было
    # ни на одном экране, хотя это первый вопрос, с которым сюда заходят
    done_docs = [d for d in docs if d.status == models.DONE]
    return {'volumes': items, 'busy': busy, 'parsed': parsed, 'ready': ready,
            'summary': finding_service.submission_stats(s, done_docs),
            'project': project, 'submission': submission}


def _parsed_docs(sub):
    """Тома, по которым уже есть что показывать."""
    return [d for d in sub.documents if d.status == models.DONE]


def _pick_doc(sub, doc_id=None):
    docs = _parsed_docs(sub)
    if not docs:
        return None, []
    if doc_id:
        for d in docs:
            if d.id == doc_id:
                return d, docs
    return docs[0], docs


def _plan_ctx(s, doc, q='', flt=''):
    plan = plan_service.current_plan(s, doc.id)
    if plan is None:
        return {'doc': doc, 'plan': None, 'rows': [], 'stats': {}, 'q': q, 'flt': flt}
    rows = plan_service.items(s, plan.id)
    shown = plan_service.filtered(rows, q, flt)[:800]
    plan_service.mark_quotes(shown)
    return {'doc': doc, 'plan': plan, 'all_rows': rows,
            'rows': shown,
            'stats': plan_service.stats(rows),
            'show_pos': any((i.pos or '').strip() for i in rows),
            'filters': plan_service.FILTERS, 'q': q, 'flt': flt,
            'frozen': plan.status == models.FROZEN}


def _remarks_ctx(s, doc, sub, scope='doc'):
    """Замечания одного тома или всей подачи — письмо бюро уходит одно."""
    docs = _parsed_docs(sub) if scope == 'all' else [doc]
    groups = remark_service.groups(s, docs)
    rows = [r for _, rs in groups for r in rs]
    orphaned = set()
    for d in docs:
        orphaned |= {r.id for r in remark_service.orphaned(s, d.id)}
    return {'doc': doc, 'scope': scope, 'groups': groups, 'rows': rows,
            'stats': remark_service.stats(rows),
            'labels': remark_service.STATUS_LABELS,
            'orphaned': orphaned,
            # под замечанием стоит имя, а не почта целиком: этот текст
            # эксперт подписывает и отдаёт бюро
            'users': {u.id: (u.name or u.email.split('@')[0])
                      for u in s.scalars(select(User)).all()}}


@app.get('/projects/{project_id}', response_class=HTMLResponse)
def project_page(request: Request, project_id: int, tab: str = 'composition',
                 q: str = '', section: str = '', flagged: int = 0,
                 doc: int = 0, flt: str = '', scope: str = '',
                 page: int = 0, item: int = 0, only: int = 0, rem: int = 0,
                 from_tab: str = Query('', alias='from')):
    with db() as s:
        p, sub = _load(s, project_id)
        # сводка считается один раз за запрос: счётчик у вкладки и блок над
        # составом комплекта берут её из одного места, а не гоняют выборки дважды
        composition = _composition(s, p, sub)
        ctx = {'request': request, 'project': p, 'submission': sub, 'tab': tab,
               'q': q, 'section': section, 'flagged': flagged, 'flt': flt,
               'from': from_tab, 'composition': composition,
               'open_remarks': remark_service.open_count(s, _parsed_docs(sub)),
               'intake_open': composition['summary']['open']}
        if tab == 'nomenclature':
            ctx['nom'] = _nom_ctx(s, sub, q, section, flagged)
        elif tab in ('intake', 'passport', 'checkplan', 'match', 'remarks', 'sheet'):
            picked, docs = _pick_doc(sub, doc)
            ctx['docs'] = docs
            ctx['doc'] = picked
            if picked is not None and tab == 'intake':
                ctx['intake'] = finding_service.context(
                    s, picked, flt, request.query_params.get('f', ''))
            elif picked is not None and tab == 'passport':
                ctx['psp'] = passport_service.context(s, picked)
            elif picked is not None and tab == 'match':
                ctx['match'] = match_service.context(s, picked, q,
                                                     flt if flt else None)
            elif picked is not None and tab == 'sheet':
                ctx['sheet'] = sheet_service.context(
                    s, picked, page, item or None, only_marked=bool(only),
                    focus=rem or None)
            elif picked is not None and tab == 'remarks':
                ctx['remarks'] = _remarks_ctx(s, picked, sub, scope or 'doc')
            elif picked is not None:
                ctx['plan'] = _plan_ctx(s, picked, q, flt)
        return templates.TemplateResponse(request, 'project.html', ctx)


# ------------------------------------------------------- паспорт и план проверки

@app.get('/projects/{project_id}/checkplan', response_class=HTMLResponse)
def checkplan_rows(request: Request, project_id: int, doc: int = 0,
                   q: str = '', flt: str = ''):
    """Перерисовка таблицы при фильтрах и поиске."""
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        if picked is None:
            raise HTTPException(404, 'том не разобран')
        return templates.TemplateResponse(request, '_checkplan_table.html', {
            'request': request, 'project': p, 'plan': _plan_ctx(s, picked, q, flt)})


@app.get('/projects/{project_id}/intake', response_class=HTMLResponse)
def intake_panel(request: Request, project_id: int, doc: int = 0,
                 flt: str = '', f: str = ''):
    """Перерисовка поверхности приёмки: выбор находки и фильтры."""
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        if picked is None:
            raise HTTPException(404, 'том не разобран')
        return templates.TemplateResponse(request, '_intake_panel.html', {
            'request': request, 'project': p, 'doc': picked,
            'intake': finding_service.context(s, picked, flt, f)})


@app.post('/api/documents/{document_id}/finding', response_class=HTMLResponse)
def finding_decide(request: Request, document_id: int, key: str = Form(...),
                   status: str = Form(...), flt: str = Form(''),
                   next_one: str = Form('')):
    """Решение эксперта по находке — одно действие на оба источника.

    Расхождение сверки и расхождение состава решаются одной кнопкой:
    для эксперта это одна и та же работа, а что их считают разные модули —
    его не касается.
    """
    if status not in (models.OPEN, models.DISMISSED, models.SENT):
        raise HTTPException(400, 'неизвестное решение')
    with db() as s:
        doc = s.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, 'том не найден')
        rows = finding_service.collect(s, doc)
        found = next((f for f in rows if f.key == key), None)
        if found is None:
            raise HTTPException(404, 'находка не найдена')
        if found.source == 'match':
            remark_service.from_match(s, doc, found.item, status, _uid(request))
        else:
            remark_service.from_passport(s, doc, found.raw, status, _uid(request))
        # после решения открываем следующую нерешённую: эксперт идёт очередью
        after = finding_service.collect(s, doc)
        nxt = key
        if next_one:
            rest = [x for x in finding_service.filtered(after, flt) if not x.decided]
            nxt = rest[0].key if rest else ''
        project_id, _ = plan_service.project_of(s, doc)
        return templates.TemplateResponse(request, '_intake_panel.html', {
            'request': request, 'project': s.get(Project, project_id), 'doc': doc,
            'intake': finding_service.context(s, doc, flt, nxt)})


@app.get('/projects/{project_id}/match', response_class=HTMLResponse)
def match_panel(request: Request, project_id: int, doc: int = 0,
                q: str = '', flt: str = ''):
    """Перерисовка панели сверки: фильтры, поиск и опрос прогресса."""
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        if picked is None:
            raise HTTPException(404, 'том не разобран')
        return templates.TemplateResponse(request, '_match_panel.html', {
            'request': request, 'project': p, 'doc': picked,
            'match': match_service.context(s, picked, q, flt if flt else None)})


@app.post('/api/documents/{document_id}/match', response_class=HTMLResponse)
def match_start(request: Request, document_id: int):
    """Запуск сверки. Том должен быть разобран: сверять нечего до приёмки."""
    with db() as s:
        d = s.get(Document, document_id)
        if d is None:
            raise HTTPException(404, 'том не найден')
        if d.status != models.DONE:
            raise HTTPException(409, 'том ещё не разобран')
        project_id, _ = plan_service.project_of(s, d)
        d.match_stats = {}
        s.commit()
    enqueue_match(document_id)
    return RedirectResponse(f'/projects/{project_id}?tab=match&doc={document_id}',
                            status_code=303)


# ------------------------------------------------------------- замечания

@app.post('/api/match-items/{item_id}/remark', response_class=HTMLResponse)
def match_remark(request: Request, item_id: int, status: str = Form(...),
                 flt: str = Form('')):
    """Решение эксперта по строке сверки. В ответе — только эта строка.

    Набор колонок и активный фильтр приходят вместе с решением: подменённая
    строка обязана иметь ровно те же ячейки, что и остальная таблица.
    """
    if status not in (models.OPEN, models.DISMISSED, models.SENT):
        raise HTTPException(400, 'неизвестное решение')
    with db() as s:
        item = s.get(MatchItem, item_id)
        if item is None:
            raise HTTPException(404, 'строка сверки не найдена')
        doc = s.get(Document, item.document_id)
        item.remark = remark_service.from_match(s, doc, item, status, _uid(request))
        item.level_class = match_service.LEVELS.get(item.level, '')
        item.status_label = match_service.status_label(item.status)
        project_id, _ = plan_service.project_of(s, doc)
        return templates.TemplateResponse(
            request, '_match_row.html',
            {'request': request, 'i': item, 'flt': flt,
             'cols': match_service.columns(match_service.items(s, doc.id)),
             'project': s.get(Project, project_id)})


@app.post('/api/documents/{document_id}/passport-remark', response_class=HTMLResponse)
def passport_remark(request: Request, document_id: int, index: int = Form(...),
                    status: str = Form(...)):
    """Решение эксперта по расхождению паспорта. В ответе — только блок."""
    if status not in (models.OPEN, models.DISMISSED, models.SENT):
        raise HTTPException(400, 'неизвестное решение')
    with db() as s:
        doc = s.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, 'том не найден')
        findings = doc.findings or []
        if not 0 <= index < len(findings):
            raise HTTPException(404, 'расхождение не найдено')
        f = findings[index]
        remark = remark_service.from_passport(s, doc, f, status, _uid(request))
        ctx = dict(f, index=index, remark=remark,
                   level_class=passport_service.LEVELS.get(f.get('level'), ''))
        project_id, _ = plan_service.project_of(s, doc)
        return templates.TemplateResponse(
            request, '_finding.html',
            {'request': request, 'f': ctx, 'doc': doc,
             'project': s.get(Project, project_id)})


@app.get('/projects/{project_id}/letter', response_class=HTMLResponse)
def letter_preview(request: Request, project_id: int, doc: int = 0,
                   scope: str = 'all'):
    """Предпросмотр письма бюро.

    Это единственный документ, который видит внешняя сторона, а уходил он
    вслепую: файл просто скачивался. Здесь видно ровно то, что будет
    в docx, — до того, как письмо отправят.
    """
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        docs = _parsed_docs(sub) if scope == 'all' else (
            [picked] if picked is not None else [])
        groups = remark_service.for_letter(s, docs)
        total = sum(len(rs) for _, rs in groups)
        authors = {r.author_id for _, rs in groups for r in rs if r.author_id}
        author = ''
        if len(authors) == 1:
            u = s.get(User, authors.pop())
            author = u.label if u else ''
        return templates.TemplateResponse(request, 'letter.html', {
            'request': request, 'project': p, 'submission': sub, 'doc': picked,
            'groups': groups, 'total': total, 'author': author, 'scope': scope,
            'org_name': config.ORG_NAME, 'date': letter._date(),
            'levels': letter.LEVEL_WORDS})


@app.get('/projects/{project_id}/letter.docx')
def project_letter(project_id: int, doc: int = 0, scope: str = 'all'):
    """Письмо бюро: замечания в работе, том за томом, одним документом."""
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        docs = _parsed_docs(sub) if scope == 'all' else (
            [picked] if picked is not None else [])
        groups = remark_service.for_letter(s, docs)
        if not groups:
            raise HTTPException(404, 'нет замечаний в работе')
        total = sum(len(rs) for _, rs in groups)
        authors = {r.author_id for _, rs in groups for r in rs if r.author_id}
        author = ''
        if len(authors) == 1:
            u = s.get(User, authors.pop())
            author = (u.name or u.email) if u else ''
        data = letter.build(p, sub, groups, author=author,
                            org_name=config.ORG_NAME, total=total)
        name = letter.filename(p, sub)
    return Response(data, media_type=(
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
        headers={'Content-Disposition': "attachment; filename*=UTF-8''"
                 + quote(re.sub(r'[^\w\-.]', '_', name))})


@app.post('/api/projects/{project_id}/remarks/sent')
def remarks_mark_sent(request: Request, project_id: int, doc: int = Form(0),
                      scope: str = Form('all')):
    """Отметить замечания переданными — после того, как письмо ушло."""
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        docs = _parsed_docs(sub) if scope == 'all' else (
            [picked] if picked is not None else [])
        remark_service.mark_sent(s, docs, _uid(request))
        target = picked.id if picked is not None else 0
    return RedirectResponse(
        f'/projects/{project_id}?tab=remarks&doc={target}&scope={scope}',
        status_code=303)


@app.post('/api/remarks/{remark_id}/anchor', response_class=HTMLResponse)
def remark_anchor(request: Request, remark_id: int, page: int = Form(...),
                  x: float = Form(...), y: float = Form(...)):
    """Поставить метку замечанию, которое машина нашла без координат."""
    with db() as s:
        remark = s.get(Remark, remark_id)
        if remark is None:
            raise HTTPException(404, 'замечание не найдено')
        doc = s.get(Document, remark.document_id)
        remark_service.place(s, remark, page, x, y, _uid(request))
        p = s.get(Project, s.get(Submission, doc.submission_id).project_id)
        return templates.TemplateResponse(request, '_sheet_panel.html', {
            'request': request, 'project': p, 'doc': doc,
            'sheet': sheet_service.context(s, doc, page, focus=remark.id)})


@app.post('/api/remarks/{remark_id}', response_class=HTMLResponse)
def remark_edit(request: Request, remark_id: int, text: str = Form(None),
                status: str = Form(None)):
    """Правка формулировки или статуса замечания."""
    if status is not None and status not in (models.OPEN, models.DISMISSED,
                                             models.SENT):
        raise HTTPException(400, 'неизвестный статус')
    with db() as s:
        remark = s.get(Remark, remark_id)
        if remark is None:
            raise HTTPException(404, 'замечание не найдено')
        remark_service.edit(s, remark, text, status, _uid(request))
        users = {u.id: (u.name or u.email) for u in s.scalars(select(User)).all()}
        doc = s.get(Document, remark.document_id)
        project_id, _ = plan_service.project_of(s, doc)
        return templates.TemplateResponse(request, '_remark.html', {
            'request': request, 'r': remark, 'users': users,
            'project': s.get(Project, project_id)})


def _row_response(request, s, project, item):
    """Только перерисованная строка.

    Счётчик над таблицей обновляет себя сам, услышав событие planchanged:
    класть <div> в один ответ с <tr> нельзя — htmx разбирает такой фрагмент
    как содержимое таблицы, посторонний элемент выбрасывается парсером,
    и обмен строки застревает на htmx-swapping.
    """
    resp = templates.TemplateResponse(request, '_checkplan_row.html', {
        'request': request, 'project': project, 'i': item,
        'frozen': s.get(CheckPlan, item.plan_id).status == models.FROZEN})
    resp.headers['HX-Trigger'] = 'planchanged'
    return resp


@app.get('/projects/{project_id}/checkplan/stats', response_class=HTMLResponse)
def checkplan_stats(request: Request, project_id: int, doc: int = 0):
    """Счётчик отбора отдельным запросом — см. `_row_response`."""
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        if picked is None:
            raise HTTPException(404, 'том не разобран')
        plan = plan_service.current_plan(s, picked.id)
        rows = plan_service.items(s, plan.id) if plan else []
        return templates.TemplateResponse(request, '_checkplan_counter.html', {
            'request': request, 'project': p, 'doc_id': picked.id,
            'stats': plan_service.stats(rows)})


@app.post('/api/check-items/{item_id}/decision', response_class=HTMLResponse)
def item_decision(request: Request, item_id: int, value: str = Form(...)):
    with db() as s:
        item = s.get(CheckItem, item_id)
        if item is None:
            raise HTTPException(404, 'позиция не найдена')
        plan = s.get(CheckPlan, item.plan_id)
        if plan.status == models.FROZEN:
            raise HTTPException(409, 'план зафиксирован: создайте новую версию')
        doc = s.get(Document, plan.document_id)
        project_id, submission_id = plan_service.project_of(s, doc)
        item.decided_by = _uid(request)
        plan_service.set_decision(s, item, value, project_id, submission_id)
        return _row_response(request, s, s.get(Project, project_id), item)


@app.post('/api/check-items/{item_id}/comment', response_class=HTMLResponse)
def item_comment(request: Request, item_id: int, comment: str = Form('')):
    with db() as s:
        item = s.get(CheckItem, item_id)
        if item is None:
            raise HTTPException(404, 'позиция не найдена')
        plan = s.get(CheckPlan, item.plan_id)
        if plan.status == models.FROZEN:
            raise HTTPException(409, 'план зафиксирован')
        item.comment = comment.strip()[:2000]
        item.decided_by = _uid(request)
        doc = s.get(Document, plan.document_id)
        project_id, submission_id = plan_service.project_of(s, doc)
        plan_service.set_decision(s, item, item.decision, project_id, submission_id)
        return _row_response(request, s, s.get(Project, project_id), item)


@app.post('/api/check-plans/{plan_id}/bulk', response_class=HTMLResponse)
def plan_bulk(request: Request, plan_id: int, value: str = Form(...),
              scope: str = Form(''), q: str = Form(''), flt: str = Form(''),
              overwrite: str = Form('')):
    with db() as s:
        plan = s.get(CheckPlan, plan_id)
        if plan is None:
            raise HTTPException(404, 'план не найден')
        if plan.status == models.FROZEN:
            raise HTTPException(409, 'план зафиксирован: создайте новую версию')
        doc = s.get(Document, plan.document_id)
        project_id, submission_id = plan_service.project_of(s, doc)
        rows = plan_service.items(s, plan.id)
        target = plan_service.filtered(rows, q, flt) if scope == 'filtered' else [
            r for r in rows if r.cls == scope] if scope in ('A', 'B', 'C') else rows
        changed, kept = plan_service.bulk(s, target, value, bool(overwrite),
                                          project_id, submission_id, _uid(request))
        log.info('план %s: массовое действие %s, изменено %s, оставлено %s',
                 plan.id, value, len(changed), kept)
        ctx = _plan_ctx(s, doc, q, flt)
        ctx['flash'] = {
            'text': f'Изменено позиций: {len(changed)}' + (
                f', оставлено с решением эксперта: {kept}' if kept else ''),
            'undo': ','.join(str(i) for i in changed),
        }
        return templates.TemplateResponse(request, '_checkplan_table.html', {
            'request': request, 'project': s.get(Project, project_id), 'plan': ctx})


@app.post('/api/check-plans/{plan_id}/undo', response_class=HTMLResponse)
def plan_undo(request: Request, plan_id: int, ids: str = Form(''),
              q: str = Form(''), flt: str = Form('')):
    """Отмена массового действия: перечисленные строки возвращаются в «как
    решила машина». Без этого «снять все C» необратимо стирало ручной отбор."""
    wanted = {int(v) for v in ids.split(',') if v.strip().isdigit()}
    with db() as s:
        plan = s.get(CheckPlan, plan_id)
        if plan is None:
            raise HTTPException(404, 'план не найден')
        if plan.status == models.FROZEN:
            raise HTTPException(409, 'план зафиксирован: создайте новую версию')
        doc = s.get(Document, plan.document_id)
        project_id, submission_id = plan_service.project_of(s, doc)
        rows = [r for r in plan_service.items(s, plan.id) if r.id in wanted]
        back, _ = plan_service.bulk(s, rows, models.AUTO, True,
                                    project_id, submission_id, _uid(request))
        ctx = _plan_ctx(s, doc, q, flt)
        ctx['flash'] = {'text': f'Возвращено позиций: {len(back)}', 'undo': ''}
        return templates.TemplateResponse(request, '_checkplan_table.html', {
            'request': request, 'project': s.get(Project, project_id), 'plan': ctx})


@app.post('/api/check-plans/{plan_id}/freeze')
def plan_freeze(plan_id: int):
    with db() as s:
        plan = s.get(CheckPlan, plan_id)
        if plan is None:
            raise HTTPException(404, 'план не найден')
        doc = s.get(Document, plan.document_id)
        project_id, _ = plan_service.project_of(s, doc)
        plan.frozen_by = _uid(request)
        plan_service.freeze(s, plan, plan_service.items(s, plan.id))
        return RedirectResponse(
            f'/projects/{project_id}?tab=checkplan&doc={doc.id}', status_code=303)


# ------------------------------------------------------------- лист чертежа

def _png(data: bytes, immutable=True):
    cache = 'public, max-age=86400' + (', immutable' if immutable else '')
    return Response(data, media_type='image/png',
                    headers={'Cache-Control': cache})


@app.get('/api/pages/{document_id}/{page}.png')
def page_png(document_id: int, page: int, kind: str = 'overview'):
    """Обзор листа или миниатюра. Если воркер ещё не дошёл — рисуем сами."""
    with db() as s:
        doc = s.get(Document, document_id)
        if doc is None or not doc.file_key:
            raise HTTPException(404, 'том не найден')
        if page < 1 or (doc.pages_total and page > doc.pages_total):
            raise HTTPException(404, 'листа нет в томе')
        data = sheet_service.image(doc, page,
                                   'thumb' if kind == 'thumb' else 'overview')
    return _png(data)


@app.get('/api/pages/{document_id}/{page}/crop.png')
def page_crop_png(document_id: int, page: int, box: str = '0,0,1,1',
                  w: int = 1600):
    """Кроп видимой области под зум. box — четыре доли через запятую."""
    try:
        parts = tuple(float(v) for v in box.split(','))
        if len(parts) != 4:
            raise ValueError
    except ValueError:
        raise HTTPException(400, 'неверная рамка')
    with db() as s:
        doc = s.get(Document, document_id)
        if doc is None or not doc.file_key:
            raise HTTPException(404, 'том не найден')
        data = sheet_service.crop(doc, page, parts, max(200, min(w, 3000)))
    return _png(data)


@app.get('/projects/{project_id}/sheet', response_class=HTMLResponse)
def sheet_panel(request: Request, project_id: int, doc: int = 0, page: int = 0,
                item: int = 0, only: int = 0, rem: int = 0,
                from_tab: str = Query('', alias='from')):
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        if picked is None:
            raise HTTPException(404, 'том не разобран')
        return templates.TemplateResponse(request, '_sheet_panel.html', {
            'request': request, 'project': p, 'doc': picked,
            'sheet': sheet_service.context(s, picked, page, item or None,
                                           only_marked=bool(only),
                                           focus=rem or None)})


@app.post('/api/documents/{document_id}/sheet-remark', response_class=HTMLResponse)
def sheet_remark(request: Request, document_id: int, page: int = Form(...),
                 x: float = Form(...), y: float = Form(...),
                 text: str = Form(''), level: str = Form('red'),
                 mark: str = Form('')):
    """Замечание, заведённое прямо с листа: метка плюс формулировка."""
    with db() as s:
        doc = s.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, 'том не найден')
        remark = remark_service.from_sheet(s, doc, page, x, y, text=text,
                                           level=level, mark=mark,
                                           user_id=_uid(request))
        ctx = sheet_service.context(s, doc, page)
        p = s.get(Project, s.get(Submission, doc.submission_id).project_id)
        response = templates.TemplateResponse(request, '_sheet_panel.html', {
            'request': request, 'project': p, 'doc': doc, 'sheet': ctx,
            'created': remark.id})
    return response


@app.get('/api/symbols/{symbol_id}.png')
def symbol_image(symbol_id: int):
    """Картинка условного обозначения. Веб файлы не разбирает, но отдать
    восемь килобайт из хранилища дешевле, чем городить подписанные ссылки."""
    with db() as s:
        sym = s.get(Symbol, symbol_id)
        if sym is None or not sym.image_key:
            raise HTTPException(404, 'изображение не найдено')
        try:
            data = get_storage().read_bytes(sym.image_key)
        except Exception:
            raise HTTPException(404, 'изображение недоступно')
    return Response(data, media_type='image/png',
                    headers={'Cache-Control': 'public, max-age=86400'})


@app.get('/projects/{project_id}/composition', response_class=HTMLResponse)
def project_composition(request: Request, project_id: int):
    with db() as s:
        p, sub = _load(s, project_id)
        return templates.TemplateResponse(request, '_composition.html', {
            'request': request, 'composition': _composition(s, p, sub),
            'project': p})


def _nom_ctx(s, submission, q='', section='', flagged=0):
    rows, totals = nomenclature.collect(s, submission.id)
    # «—» в разделе значит «раздел не распознан»: как кнопка фильтра это
    # пустой чип, который ничего не отбирает. Переключатель показываем,
    # только когда разделов и правда несколько.
    sections = sorted({sec for r in rows for sec in r.sections if sec and sec != '—'})
    shown = nomenclature.filtered(rows, q, section, bool(flagged))
    return {'rows': shown[:2000], 'totals': totals, 'sections': sections,
            'show_sections': len(sections) > 1,
            'show_section_col': len({tuple(r.sections) for r in rows}) > 1,
            'shown': len(shown), 'truncated': len(shown) > 2000,
            'q': q, 'section': section, 'flagged': bool(flagged)}


@app.get('/projects/{project_id}/nomenclature', response_class=HTMLResponse)
def project_nomenclature(request: Request, project_id: int, q: str = '',
                         section: str = '', flagged: int = 0):
    with db() as s:
        p, sub = _load(s, project_id)
        return templates.TemplateResponse(request, '_nomenclature_rows.html', {
            'request': request, 'nom': _nom_ctx(s, sub, q, section, flagged),
            'project': p})


@app.get('/projects/{project_id}/nomenclature.xlsx')
def project_xlsx(project_id: int):
    with db() as s:
        p, sub = _load(s, project_id)
        rows, totals = nomenclature.collect(s, sub.id)
        data = workbook_bytes(p, sub, sub.documents, rows, totals)
    return _xlsx(data, f'{p.code or p.name}_приёмка.xlsx')


@app.get('/projects/{project_id}/passport.xlsx')
def project_passport_xlsx(project_id: int, doc: int = 0):
    """Паспорт тома и план проверки одной книгой — то же, что на экране.

    Экспорт делает веб, а не воркер: всё нужное уже лежит в БД, PDF при этом
    не открывается, и книга собирается за доли секунды.
    """
    with db() as s:
        p, sub = _load(s, project_id)
        picked, _ = _pick_doc(sub, doc)
        if picked is None:
            raise HTTPException(404, 'том не разобран')
        psp = passport_service.context(s, picked)
        plan = _plan_ctx(s, picked)
        users = {u.id: (u.name or u.email) for u in s.scalars(select(User)).all()}
        data = passport_workbook_bytes(p, sub, picked, psp, plan, users,
                                       match_service.items(s, picked.id),
                                       remark_service.items(s, picked.id))
        stem = picked.cipher or picked.filename.rsplit('.', 1)[0]
    return _xlsx(data, f'{stem}_паспорт.xlsx')


def _xlsx(data: bytes, name: str):
    name = re.sub(r'[^\w\-. ]', '_', name)
    return Response(data, media_type=(
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        headers={'Content-Disposition':
                 "attachment; filename*=UTF-8''" + quote(name)})


# ------------------------------------------------------------------ загрузка

@app.post('/api/uploads/init')
async def upload_init(request: Request):
    body = await request.json()
    project_id = int(body['project_id'])
    filename = (body.get('filename') or '').strip()
    size = int(body.get('size') or 0)
    if not filename.lower().endswith('.pdf'):
        raise HTTPException(400, 'принимаются только файлы PDF')
    if size > config.MAX_UPLOAD_MB * 1048576:
        raise HTTPException(400, f'файл больше {config.MAX_UPLOAD_MB} МБ')
    with db() as s:
        p, sub = _load(s, project_id)
        cipher, section, label, revision = parse_filename(filename)
        d = Document(org_id=p.org_id, submission_id=sub.id, filename=filename,
                     cipher=cipher, section=section, section_label=label,
                     revision=revision, size_bytes=size, status=models.NEW)
        s.add(d)
        s.flush()
        d.file_key = object_key(d.id, filename)
        s.commit()
        target = get_storage().upload_target(d.file_key)
        return JSONResponse({'document_id': d.id, 'upload': target})


@app.put('/api/uploads/local/{key:path}')
async def upload_local(key: str, request: Request):
    """Приём файла для локального бэкенда. На Render файлы идут в R2 мимо веба."""
    if config.STORAGE_BACKEND != 'local':
        raise HTTPException(404, 'локальная загрузка выключена')
    import tempfile
    with tempfile.TemporaryFile() as tmp:
        async for chunk in request.stream():
            tmp.write(chunk)
        tmp.seek(0)
        size = get_storage().put_stream(key, tmp)
    return JSONResponse({'ok': True, 'size': size})


@app.post('/api/documents/{document_id}/ready')
def document_ready(document_id: int):
    with db() as s:
        d = s.get(Document, document_id)
        if d is None:
            raise HTTPException(404, 'том не найден')
        d.status = models.QUEUED
        s.commit()
    enqueue_intake(document_id)
    return JSONResponse({'ok': True})


@app.post('/api/documents/{document_id}/retry', response_class=HTMLResponse)
def document_retry(request: Request, document_id: int):
    with db() as s:
        d = s.get(Document, document_id)
        if d is None:
            raise HTTPException(404, 'том не найден')
        d.status = models.QUEUED
        d.error = ''
        s.commit()
        sub = s.get(Submission, d.submission_id)
        p = s.get(Project, sub.project_id)
        enqueue_intake(document_id)
        return templates.TemplateResponse(request, '_composition.html', {
            'request': request, 'composition': _composition(s, p, sub),
            'project': p})


@app.get('/healthz')
def healthz():
    with db() as s:
        s.execute(select(func.now() if s.bind.dialect.name == 'postgresql'
                         else func.current_timestamp()))
    return {'ok': True, 'time': datetime.utcnow().isoformat()}
