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
