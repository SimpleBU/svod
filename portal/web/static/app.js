/* Загрузка томов: браузер льёт файл прямо в хранилище по подписанной
   ссылке, минуя веб-сервис. Веб узнаёт о файле дважды: когда выдаёт
   ссылку и когда файл долит. Сборки нет — это обычный скрипт. */
const svod = {
  toggleUploader() {
    const u = document.getElementById('uploader');
    if (!u) return;
    u.style.display = (u.style.display === 'none') ? '' : 'none';
    if (u.style.display === '') u.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  }
};

(function () {
  const zone = document.getElementById('dropzone');
  const input = document.getElementById('files');
  const list = document.getElementById('uploads');
  const wrap = document.getElementById('uploader');
  if (!zone || !input || !wrap) return;
  const projectId = wrap.dataset.project;
  let active = 0;

  const mb = b => (b / 1048576).toFixed(1) + ' МБ';

  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { send([...input.files]); input.value = ''; });
  ['dragenter', 'dragover'].forEach(e => zone.addEventListener(e, ev => {
    ev.preventDefault(); zone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(e => zone.addEventListener(e, ev => {
    ev.preventDefault(); zone.classList.remove('over');
  }));
  zone.addEventListener('drop', ev => send([...ev.dataTransfer.files]));

  function row(file) {
    const el = document.createElement('div');
    el.className = 'up';
    el.innerHTML = '<div class="name ell"></div><div class="size"></div>' +
      '<div class="progress" style="flex:1"><div style="width:0%"></div></div>' +
      '<div class="st">ожидание</div>';
    el.querySelector('.name').textContent = file.name;
    el.querySelector('.size').textContent = mb(file.size);
    list.hidden = false;
    list.appendChild(el);
    return {
      bar: el.querySelector('.progress > div'),
      st: el.querySelector('.st'),
      set(pct, text, color) {
        this.bar.style.width = pct + '%';
        this.st.textContent = text;
        if (color) this.st.style.color = color;
      }
    };
  }

  async function send(files) {
    files = files.filter(f => /\.pdf$/i.test(f.name));
    if (!files.length) return;
    for (const f of files) upload(f, row(f));
  }

  async function upload(file, ui) {
    active++;
    try {
      ui.set(0, 'подготовка');
      const r = await fetch('/api/uploads/init', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({project_id: projectId, filename: file.name, size: file.size})
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'отказ сервера');
      const {document_id, upload: target} = await r.json();

      await new Promise((ok, fail) => {
        const x = new XMLHttpRequest();
        x.open(target.method, target.url, true);
        for (const [k, v] of Object.entries(target.headers || {})) x.setRequestHeader(k, v);
        x.upload.onprogress = e => {
          if (e.lengthComputable) ui.set(Math.round(e.loaded * 100 / e.total), 'загрузка');
        };
        x.onload = () => (x.status >= 200 && x.status < 300) ? ok() :
          fail(new Error('хранилище ответило ' + x.status));
        x.onerror = () => fail(new Error('обрыв связи с хранилищем'));
        x.send(file);
      });

      await fetch('/api/documents/' + document_id + '/ready', {method: 'POST'});
      ui.set(100, 'в очереди на разбор', 'var(--g-fg)');
    } catch (e) {
      ui.set(100, e.message.slice(0, 40), 'var(--r-fg)');
    } finally {
      if (--active === 0) setTimeout(() => location.reload(), 900);
    }
  }
})();

/* Просмотрщик листа. Зум — обычный CSS-трансформ поверх обзорной картинки;
   когда масштаб уходит выше единицы, у сервера запрашивается кроп видимой
   области: он рисуется за сотые доли секунды и отдаёт настоящее разрешение
   вместо растянутых пикселей. Тайлов нет намеренно — лист один, а кроп
   дешевле любой схемы нарезки. */
svod.sheet = function () {
  const panel = document.getElementById('sheetpanel');
  if (!panel || panel.dataset.wired) return;
  panel.dataset.wired = '1';

  const stage = document.getElementById('stage');
  const canvas = document.getElementById('canvas');
  const pic = document.getElementById('sheetpic');
  const form = document.getElementById('newremark');
  const pin = document.getElementById('newpin');
  const docId = panel.dataset.doc, page = panel.dataset.page;
  const overview = '/api/pages/' + docId + '/' + page + '.png';

  let scale = 1, tx = 0, ty = 0, dragging = false, moved = false, sx = 0, sy = 0;
  let cropTimer = null, cropped = false;

  const apply = () => {
    canvas.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
  };

  /* Видимая область в долях листа — то же представление, в котором
     хранятся якоря замечаний. */
  const visibleBox = () => {
    const s = stage.getBoundingClientRect(), c = canvas.getBoundingClientRect();
    const x0 = Math.max(0, (s.left - c.left) / c.width);
    const y0 = Math.max(0, (s.top - c.top) / c.height);
    const x1 = Math.min(1, (s.right - c.left) / c.width);
    const y1 = Math.min(1, (s.bottom - c.top) / c.height);
    return [x0, y0, x1, y1];
  };

  /* Кроп не подменяет обзорную картинку, а ложится поверх неё ровно на ту
     область, из которой вырезан: система координат остаётся системой всего
     листа, и метки замечаний не съезжают. */
  const refine = () => {
    clearTimeout(cropTimer);
    cropTimer = setTimeout(() => {
      const layer = document.getElementById('sheetcrop');
      if (!layer) return;
      if (scale <= 1.6) { layer.hidden = true; cropped = false; return; }
      const b = visibleBox();
      const key = b.map(v => v.toFixed(3)).join(',');
      if (key === layer.dataset.box) return;
      const w = Math.min(3000, Math.round(stage.clientWidth * 2));
      const img = new Image();
      img.onload = () => {
        layer.src = img.src;
        layer.style.left = (b[0] * 100) + '%';
        layer.style.top = (b[1] * 100) + '%';
        layer.style.width = ((b[2] - b[0]) * 100) + '%';
        layer.style.height = ((b[3] - b[1]) * 100) + '%';
        layer.dataset.box = key;
        layer.hidden = false;
        cropped = true;
      };
      img.src = '/api/pages/' + docId + '/' + page + '/crop.png?box='
        + b.map(v => v.toFixed(4)).join(',') + '&w=' + w;
    }, 220);
  };

  const zoom = (factor, cx, cy) => {
    const prev = scale;
    scale = Math.max(1, Math.min(8, scale * factor));
    const r = stage.getBoundingClientRect();
    const px = (cx === undefined ? r.width / 2 : cx - r.left);
    const py = (cy === undefined ? r.height / 2 : cy - r.top);
    tx = px - (px - tx) * (scale / prev);
    ty = py - (py - ty) * (scale / prev);
    if (scale === 1) { tx = 0; ty = 0; }
    apply(); refine();
  };

  stage.addEventListener('wheel', e => {
    e.preventDefault();
    zoom(e.deltaY < 0 ? 1.25 : 1 / 1.25, e.clientX, e.clientY);
  }, { passive: false });

  stage.addEventListener('mousedown', e => {
    dragging = true; moved = false; sx = e.clientX - tx; sy = e.clientY - ty;
    stage.classList.add('grabbing');
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    if (Math.abs(e.clientX - sx - tx) > 3 || Math.abs(e.clientY - sy - ty) > 3) moved = true;
    tx = e.clientX - sx; ty = e.clientY - sy; apply();
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false; stage.classList.remove('grabbing');
    if (moved) refine();
  });

  panel.querySelectorAll('[data-zoom]').forEach(b => b.addEventListener('click', () => {
    const k = b.dataset.zoom;
    if (k === 'reset') { scale = 1; tx = 0; ty = 0; apply(); refine(); }
    else zoom(k === 'in' ? 1.4 : 1 / 1.4);
  }));

  /* Клик по листу ставит метку. Перетаскивание меткой не считается. */
  canvas.addEventListener('click', e => {
    if (moved || !form) return;
    const r = canvas.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const y = (e.clientY - r.top) / r.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    document.getElementById('nx').value = x.toFixed(5);
    document.getElementById('ny').value = y.toFixed(5);
    document.getElementById('nlabel').textContent =
      'л. ' + page + ', точка ' + Math.round(x * 100) + '×' + Math.round(y * 100);
    pin.style.left = (x * 100) + '%'; pin.style.top = (y * 100) + '%';
    pin.hidden = false;
    form.hidden = false;
    form.querySelector('textarea').focus();
  });

  const cancel = document.getElementById('cancelrem');
  if (cancel) cancel.addEventListener('click', () => { form.hidden = true; pin.hidden = true; });

  /* Подсветка: карточка справа и метка на листе — одно и то же. */
  panel.querySelectorAll('.remcard').forEach(card => {
    const id = card.dataset.rem;
    const dot = panel.querySelector('.pin[data-rem="' + id + '"]');
    if (!dot) return;
    const on = v => { card.classList.toggle('hot', v); dot.classList.toggle('hot', v); };
    card.addEventListener('mouseenter', () => on(true));
    card.addEventListener('mouseleave', () => on(false));
    dot.addEventListener('mouseenter', () => on(true));
    dot.addEventListener('mouseleave', () => on(false));
  });

  const active = panel.querySelector('.thumb.on');
  if (active) active.scrollIntoView({ block: 'nearest' });
};

document.addEventListener('DOMContentLoaded', () => svod.sheet && svod.sheet());
document.body && document.body.addEventListener('htmx:afterSwap', () => svod.sheet && svod.sheet());
