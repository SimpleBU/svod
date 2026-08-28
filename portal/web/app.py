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

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from agent.api import KIND_LABELS, KIND_ORDER

from .. import config, models, nomenclature
from ..db import SessionLocal, upgrade_schema
from ..exporting import workbook_bytes
from ..flags import readiness, document_flags
from ..models import Document, Org, Project, Run, Submission
from ..naming import parse_filename
from ..queue import enqueue_intake
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
templates = Jinja2Templates(directory=str(BASE / 'templates'))


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


templates.env.filters['num'] = _num
templates.env.filters['mb'] = _mb
templates.env.filters['plural'] = _plural
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


def db():
    return SessionLocal()


def current_org(s):
    org = s.scalar(select(Org).order_by(Org.id).limit(1))
    if org is None:
        org = Org(name=config.ORG_NAME)
        s.add(org)
        s.commit()
    return org


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
    if not docs:
        return {}
    ids = [d.id for d in docs]
    out = {}
    for r in s.scalars(select(Run).where(Run.document_id.in_(ids))
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
    return {'volumes': items, 'busy': busy, 'parsed': parsed, 'ready': ready,
            'project': project, 'submission': submission}


@app.get('/projects/{project_id}', response_class=HTMLResponse)
def project_page(request: Request, project_id: int, tab: str = 'composition',
                 q: str = '', section: str = '', flagged: int = 0):
    with db() as s:
        p, sub = _load(s, project_id)
        ctx = {'request': request, 'project': p, 'submission': sub, 'tab': tab,
               'q': q, 'section': section, 'flagged': flagged}
        ctx['composition'] = _composition(s, p, sub)
        if tab == 'nomenclature':
            ctx['nom'] = _nom_ctx(s, sub, q, section, flagged)
        return templates.TemplateResponse(request, 'project.html', ctx)


@app.get('/projects/{project_id}/composition', response_class=HTMLResponse)
def project_composition(request: Request, project_id: int):
    with db() as s:
        p, sub = _load(s, project_id)
        return templates.TemplateResponse(request, '_composition.html', {
            'request': request, 'composition': _composition(s, p, sub),
            'project': p})


def _nom_ctx(s, submission, q='', section='', flagged=0):
    rows, totals = nomenclature.collect(s, submission.id)
    sections = sorted({sec for r in rows for sec in r.sections})
    shown = nomenclature.filtered(rows, q, section, bool(flagged))
    return {'rows': shown[:2000], 'totals': totals, 'sections': sections,
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
    name = re.sub(r'[^\w\-. ]', '_', f'{p.code or p.name}_приёмка.xlsx')
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
