(() => {
  'use strict';

  const TZ = 'Europe/Kyiv';
  const WEEKDAYS = ['нд', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  const STATUS = {
    green: { label: 'Сприятливо', cls: 'g' },
    yellow: { label: 'З обережністю', cls: 'y' },
    red: { label: 'Неможливо', cls: 'r' },
    unknown: { label: 'Немає даних', cls: 'u' },
  };
  const CODE_TEXT = {
    0: 'ясно', 1: 'переважно ясно', 2: 'мінлива хмарність', 3: 'похмуро',
    45: 'туман', 48: 'паморозний туман',
    51: 'легка мряка', 53: 'мряка', 55: 'сильна мряка', 56: 'мряка, що замерзає', 57: 'сильна мряка, що замерзає',
    61: 'невеликий дощ', 63: 'дощ', 65: 'сильний дощ', 66: 'дощ, що замерзає', 67: 'сильний дощ, що замерзає',
    71: 'невеликий сніг', 73: 'сніг', 75: 'сильний сніг', 77: 'снігова крупа',
    80: 'злива', 81: 'сильна злива', 82: 'дуже сильна злива', 85: 'снігова злива', 86: 'сильна снігова злива',
    95: 'гроза', 96: 'гроза з градом', 99: 'сильна гроза з градом',
  };

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d = 1) => (v === null || v === undefined ? '—' : (Math.round(v * 10 ** d) / 10 ** d).toString().replace('.', ','));

  let A = null;            // data/assessment.json
  let F = null;            // data/forecast.json
  let NOW = '';            // поточна година за Києвом, 'YYYY-MM-DDTHH:00'
  let rawIdx = {};         // {locId: {t: година прогнозу}}
  const state = { aircraft: null, loc: null, tab: 'today', day: null, hour: null };

  /* ---------- час ---------- */
  const parseT = (t) => Date.UTC(+t.slice(0, 4), +t.slice(5, 7) - 1, +t.slice(8, 10), +t.slice(11, 13));
  const fmtT = (ms) => new Date(ms).toISOString().slice(0, 16);
  const addHours = (t, n) => fmtT(parseT(t) + n * 3600000);
  const addDays = (d, n) => addHours(d + 'T00:00', 24 * n).slice(0, 10);
  const shortDate = (d) => d.slice(8, 10) + '.' + d.slice(5, 7);
  const dayName = (d) => WEEKDAYS[new Date(Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10))).getUTCDay()];
  const todayDate = () => NOW.slice(0, 10);

  function nowKey() {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date());
    const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
    return `${p.year}-${p.month}-${p.day}T${p.hour}:00`;
  }

  function dayTitle(day) {
    const t = todayDate();
    const base = `${dayName(day)} ${shortDate(day)}`;
    if (day === t) return `Сьогодні, ${base}`;
    if (day === addDays(t, 1)) return `Завтра, ${base}`;
    return base;
  }

  /* ---------- дані ---------- */
  const res = () => A.results[state.loc][state.aircraft];
  const aircraftInfo = () => A.aircraft.find((a) => a.id === state.aircraft);

  function buildIndex() {
    rawIdx = {};
    for (const loc of F.locations) {
      rawIdx[loc.id] = Object.fromEntries(loc.hours.map((h) => [h.t, h]));
    }
  }

  const agg = (vals, fn) => {
    const v = vals.filter((x) => x !== null && x !== undefined);
    return v.length ? fn(...v) : null;
  };

  function metricsFor(locId, t) {
    if (locId === 'area') {
      const list = F.locations.map((l) => metricsFor(l.id, t)).filter(Boolean);
      if (!list.length) return null;
      const windMax = agg(list.map((m) => m.windMax), Math.max);
      const top = list.find((m) => m.windMax === windMax);
      return {
        windMax, windH: top ? top.windH : null,
        gust: agg(list.map((m) => m.gust), Math.max),
        cloud: agg(list.map((m) => m.cloud), Math.min),
        precip: agg(list.map((m) => m.precip), Math.max),
        snow: agg(list.map((m) => m.snow), Math.max),
        vis: agg(list.map((m) => m.vis), Math.min),
        icing: agg(list.map((m) => m.icing), Math.min),
        code: null,
      };
    }
    const h = rawIdx[locId] && rawIdx[locId][t];
    if (!h) return null;
    let windMax = null;
    let windH = null;
    for (const k of Object.keys(h.wind_ms)) {
      const v = h.wind_ms[k];
      if (v !== null && (windMax === null || v > windMax)) { windMax = v; windH = k; }
    }
    return {
      windMax, windH, gust: h.gust_ground_ms, cloud: h.cloud_base_m, precip: h.precip_mm,
      snow: h.snowfall_cm, vis: h.visibility_m, code: h.weather_code, icing: h.icing_from_m,
    };
  }

  /* ---------- вікна (рахуються в браузері, щоб відкидати години, що минули) ---------- */
  function computeWindows(hours) {
    const runs = [];
    let cur = [];
    let prev = null;
    for (const h of hours) {
      const ok = h.t >= NOW && (h.status === 'green' || h.status === 'yellow');
      const contiguous = prev === null || parseT(h.t) - parseT(prev) === 3600000;
      if (!contiguous && cur.length) { runs.push(cur); cur = []; }
      if (ok) cur.push(h);
      else if (cur.length) { runs.push(cur); cur = []; }
      prev = h.t;
    }
    if (cur.length) runs.push(cur);
    return runs.filter((r) => r.length >= A.window_min_hours).map((r) => ({
      start: r[0].t,
      end: addHours(r[r.length - 1].t, 1),
      hours: r.length,
      green: r.filter((x) => x.status === 'green').length,
      yellow: r.filter((x) => x.status === 'yellow').length,
      low: r.some((x) => x.low_confidence),
    }));
  }

  function windowRange(w, day) {
    const sd = w.start.slice(0, 10);
    let ed = w.end.slice(0, 10);
    let et = w.end.slice(11, 16);
    if (et === '00:00' && ed === addDays(sd, 1)) { et = '24:00'; ed = sd; }
    const st = w.start.slice(11, 16);
    const left = sd === day ? st : `${shortDate(sd)} ${st}`;
    const right = ed === sd || ed === day ? et : `${shortDate(ed)} ${et}`;
    return `${left}–${right}`;
  }

  function windowDetail(w) {
    const parts = [];
    if (w.green) parts.push(`${w.green} сприятливих`);
    if (w.yellow) parts.push(`${w.yellow} з обережністю`);
    return `${w.hours} год (${parts.join(', ')})`;
  }

  /* ---------- підготовка змісту ---------- */
  function topReason(h) {
    const reasons = h.reasons || [];
    const red = reasons.filter((r) => r.level === 'red');
    const list = red.length ? red : reasons;
    if (list.length) {
      const extra = reasons.length - 1;
      return esc(list[0].text) + (extra > 0 ? ` <b>+${extra}</b>` : '');
    }
    if (h.warnings && h.warnings.length) return esc(h.warnings[0]);
    if (h.missing && h.missing.length) return 'Немає даних: ' + esc(h.missing.join(', '));
    return '';
  }

  function factCell(label, value, cls) {
    return `<div class="fact ${cls || ''}"><small>${esc(label)}</small>${value}</div>`;
  }

  function heightsTable(raw, lim) {
    const rows = F.heights_m_agl.map((ht) => {
      const k = String(ht);
      const w = raw.wind_ms[k];
      const d = raw.wind_dir[k];
      const t = raw.temp_c[k];
      const rh = raw.rh_pct[k];
      const wc = w === null ? '' : w > lim.wind_ms.max ? 'r' : w > lim.wind_ms.optimal ? 'y' : '';
      const tc = t === null ? '' : (t < lim.temp_c.min || t > lim.temp_c.max) ? 'r' : '';
      const rc = rh === null ? '' : rh > lim.humidity_pct.max ? 'r' : '';
      const arrow = d === null ? '' : `<span class="arr" style="transform:rotate(${(d + 180) % 360}deg)" aria-hidden="true">↑</span>`;
      return `<tr><td>${ht} м</td><td class="num ${wc}">${num(w)}</td><td class="num">${d === null ? '—' : d + '°'} ${arrow}</td>`
        + `<td class="num ${tc}">${num(t)}</td><td class="num ${rc}">${rh === null ? '—' : rh}</td></tr>`;
    }).join('');
    return `<div class="tablewrap"><table><thead><tr><th>Висота</th><th class="num">Вітер м/с</th>`
      + `<th class="num">Азимут</th><th class="num">T, °C</th><th class="num">Вол., %</th></tr></thead>`
      + `<tbody>${rows}</tbody></table></div>`;
  }

  function factsFor(m, lim) {
    if (!m) return '';
    const gustCls = m.gust !== null && m.gust > lim.gust_ground_ms.max ? 'r' : '';
    const cloudCls = m.cloud !== null && m.cloud < lim.cloud_base_m.min ? 'r' : '';
    const visCls = m.vis !== null && m.vis < 2000 ? 'r' : '';
    const cells = [
      factCell('Пориви на землі', `${num(m.gust)} м/с`, gustCls),
      factCell('Підошва хмар', m.cloud === null ? 'немає' : `${m.cloud} м`, cloudCls),
      factCell('Обмерзання з висоти', m.icing === null ? 'немає' : `${m.icing} м`),
      factCell('Опади', `${num(m.precip)} мм/год`),
    ];
    if (m.snow) cells.push(factCell('Сніг', `${num(m.snow)} см/год`));
    cells.push(factCell('Видимість', m.vis === null ? '—' : `${num(m.vis / 1000)} км`, visCls));
    if (m.code !== null && m.code !== undefined) cells.push(factCell('Погода', esc(CODE_TEXT[m.code] || `код ${m.code}`)));
    return `<div class="facts">${cells.join('')}</div>`;
  }

  function detailHtml(day, hours) {
    const h = hours.find((x) => x.t === state.hour);
    if (!h) return '';
    const st = STATUS[h.status];
    const lim = aircraftInfo().limits;
    const items = [];
    for (const r of h.reasons || []) items.push(`<li class="${r.level}">${esc(r.text)}</li>`);
    for (const w of h.warnings || []) items.push(`<li class="warn">${esc(w)}</li>`);
    if (h.missing && h.missing.length) items.push(`<li>Немає даних: ${esc(h.missing.join(', '))}</li>`);
    const reasons = items.length ? `<ul class="reasons">${items.join('')}</ul>` : '<p class="none">Обмежень немає.</p>';

    let body;
    if (state.loc === 'area') {
      const rows = F.locations.map((loc) => {
        const m = metricsFor(loc.id, h.t);
        const own = A.results[loc.id][state.aircraft].hours.find((x) => x.t === h.t);
        const s = own ? STATUS[own.status] : STATUS.unknown;
        if (!m) return '';
        return `<tr><td>${esc(loc.name)}</td><td><span class="chip ${s.cls}">${s.label}</span></td>`
          + `<td class="num">${num(m.windMax)} м/с (${m.windH} м)</td><td class="num">${num(m.gust)}</td>`
          + `<td class="num">${m.cloud === null ? 'немає' : m.cloud + ' м'}</td><td class="num">${num(m.precip)}</td></tr>`;
      }).join('');
      body = `<div class="tablewrap"><table><thead><tr><th>Точка</th><th>Статус</th><th class="num">Макс. вітер</th>`
        + `<th class="num">Пориви, м/с</th><th class="num">Підошва хмар</th><th class="num">Опади, мм</th></tr></thead>`
        + `<tbody>${rows}</tbody></table></div><p class="none">Оберіть точку, щоб побачити профіль по висотах.</p>`;
    } else {
      const raw = rawIdx[state.loc] && rawIdx[state.loc][h.t];
      body = raw ? factsFor(metricsFor(state.loc, h.t), lim) + heightsTable(raw, lim) : '<p class="none">Немає даних прогнозу.</p>';
    }
    return `<section class="card" id="detail"><div class="detail-head"><h3>${shortDate(day)}, ${h.t.slice(11, 16)}</h3>`
      + `<span class="chip ${st.cls}">${st.label}</span>${h.low_confidence ? '<span class="tag">менша достовірність</span>' : ''}</div>`
      + reasons + body + '</section>';
  }

  function ensureHour(day, hours) {
    if (state.hour && hours.some((h) => h.t === state.hour)) return;
    const pref = day === todayDate() ? NOW : `${day}T12:00`;
    const pick = hours.find((h) => h.t === pref) || hours.find((h) => h.t >= NOW) || hours[0];
    state.hour = pick ? pick.t : null;
  }

  /* ---------- екрани ---------- */
  function dayHtml(day) {
    const hours = res().hours.filter((h) => h.t.slice(0, 10) === day);
    const back = state.tab === 'day' ? '<button class="back" data-tab="week">← До тижня</button>' : '';
    if (!hours.length) return `${back}<p class="empty">Для ${shortDate(day)} немає даних прогнозу.</p>`;
    ensureHour(day, hours);

    const wins = computeWindows(res().hours).filter(
      (w) => w.start.slice(0, 10) <= day && addHours(w.end, -1).slice(0, 10) >= day);
    const winHtml = wins.length
      ? `<ul class="windows">${wins.map((w) => `<li class="${w.yellow ? 'has-yellow' : ''}"><b>${windowRange(w, day)}</b> · ${windowDetail(w)}`
        + `${w.low ? '<span class="tag">менша достовірність</span>' : ''}</li>`).join('')}</ul>`
      : `<p class="none">Вікон тривалістю від ${A.window_min_hours} годин ${day === todayDate() ? 'на залишок доби ' : ''}немає.</p>`;

    const cells = hours.map((h) => {
      const st = STATUS[h.status];
      const cls = `cell ${st.cls}${h.t < NOW ? ' past' : ''}${h.t === state.hour ? ' sel' : ''}`;
      return `<button class="${cls}" data-hour="${h.t}" title="${h.t.slice(11, 16)} — ${st.label}" `
        + `aria-label="${h.t.slice(11, 16)}, ${st.label}">${h.t.slice(11, 13)}</button>`;
    }).join('');

    const rows = hours.map((h) => {
      const st = STATUS[h.status];
      const m = metricsFor(state.loc, h.t);
      const cls = `${h.t === state.hour ? 'sel' : ''}${h.t < NOW ? ' past' : ''}`;
      return `<tr class="${cls}" data-hour="${h.t}"><td>${h.t.slice(11, 16)}</td>`
        + `<td><span class="chip ${st.cls}">${st.label}</span></td>`
        + `<td class="num">${m ? `${num(m.windMax)} (${m.windH} м)` : '—'}</td>`
        + `<td class="num">${m ? num(m.gust) : '—'}</td>`
        + `<td class="num">${m ? (m.cloud === null ? 'немає' : m.cloud + ' м') : '—'}</td>`
        + `<td class="num">${m ? num(m.precip) : '—'}</td>`
        + `<td class="why">${topReason(h)}</td></tr>`;
    }).join('');

    const lowTag = hours[0].low_confidence ? '<span class="tag">менша достовірність</span>' : '';
    return `${back}<section class="card"><h2>${dayTitle(day)}${lowTag}</h2>${winHtml}</section>`
      + `<section class="card"><h3>Години (оберіть, щоб побачити деталі)</h3><div class="strip">${cells}</div></section>`
      + detailHtml(day, hours)
      + `<section class="card"><h3>По годинах</h3><div class="tablewrap"><table class="hours"><thead><tr><th>Час</th><th>Статус</th>`
      + `<th class="num">Макс. вітер, м/с</th><th class="num">Пориви, м/с</th><th class="num">Підошва хмар</th>`
      + `<th class="num">Опади, мм</th><th>Причина / попередження</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function weekHtml() {
    const all = res().hours;
    const days = [...new Set(all.map((h) => h.t.slice(0, 10)))].filter((d) => d >= todayDate());
    const wins = computeWindows(all);
    if (!days.length) return '<p class="empty">Немає даних прогнозу на найближчі дні.</p>';
    return days.map((day) => {
      const hours = all.filter((h) => h.t.slice(0, 10) === day);
      const mini = hours.map((h) => `<i class="${STATUS[h.status].cls}${h.t < NOW ? ' past' : ''}" title="${h.t.slice(11, 16)} — ${STATUS[h.status].label}"></i>`).join('');
      const dayWins = wins.filter((w) => w.start.slice(0, 10) === day);
      const low = hours[0].low_confidence ? '<span class="tag">менша достовірність</span>' : '';
      const txt = dayWins.length
        ? dayWins.map((w) => `<b>${windowRange(w, day)}</b> · ${w.hours} год`).join(' &nbsp;|&nbsp; ')
        : `немає вікон від ${A.window_min_hours} год`;
      return `<button class="weekrow" data-day="${day}"><span class="wd">${dayName(day)} ${shortDate(day)}`
        + `<small>${day === todayDate() ? 'сьогодні' : day === addDays(todayDate(), 1) ? 'завтра' : ''}</small></span>`
        + `<span class="mini">${mini}</span><span class="wtxt">${txt}${low}</span></button>`;
    }).join('');
  }

  function renderControls() {
    $('#aircraft').innerHTML = A.aircraft.map((a) =>
      `<button data-aircraft="${esc(a.id)}" aria-pressed="${a.id === state.aircraft}">${esc(a.name)}</button>`).join('');
    $('#locs').innerHTML = A.locations.map((l) =>
      `<button class="${l.id === 'area' ? 'area' : ''}" data-loc="${esc(l.id)}" aria-pressed="${l.id === state.loc}">${esc(l.name)}</button>`).join('');
    const tabs = [['today', 'Сьогодні'], ['tomorrow', 'Завтра'], ['week', 'Тиждень']];
    $('#tabs').innerHTML = tabs.map(([id, name]) =>
      `<button data-tab="${id}" aria-pressed="${state.tab === id}">${name}</button>`).join('');
  }

  function render() {
    renderControls();
    const y = window.scrollY;
    let html;
    if (state.tab === 'week') html = weekHtml();
    else if (state.tab === 'tomorrow') html = dayHtml(addDays(todayDate(), 1));
    else if (state.tab === 'day') html = dayHtml(state.day);
    else html = dayHtml(todayDate());
    $('#view').innerHTML = html;
    window.scrollTo(0, y);
    writeHash();
  }

  /* ---------- стан у посиланні ---------- */
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const a = p.get('a');
    const l = p.get('l');
    const t = p.get('t');
    if (a && A.aircraft.some((x) => x.id === a)) state.aircraft = a;
    if (l && A.locations.some((x) => x.id === l)) state.loc = l;
    if (['today', 'tomorrow', 'week'].includes(t)) state.tab = t;
  }

  function writeHash() {
    const p = new URLSearchParams({ a: state.aircraft, l: state.loc, t: state.tab === 'day' ? 'week' : state.tab });
    try { history.replaceState(null, '', '#' + p.toString()); } catch (e) { /* ігноруємо */ }
  }

  /* ---------- події ---------- */
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-aircraft],[data-loc],[data-tab],[data-day],[data-hour]');
    if (!el || !A) return;
    if (el.dataset.aircraft) state.aircraft = el.dataset.aircraft;
    else if (el.dataset.loc) state.loc = el.dataset.loc;
    else if (el.dataset.tab) { state.tab = el.dataset.tab; state.hour = null; }
    else if (el.dataset.day) { state.tab = 'day'; state.day = el.dataset.day; state.hour = null; }
    else if (el.dataset.hour) state.hour = el.dataset.hour;
    render();
  });

  /* ---------- запуск ---------- */
  function showUpdated() {
    const gen = Date.parse(A.forecast_generated_at);
    const when = new Date(gen).toLocaleString('uk-UA', {
      timeZone: TZ, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    });
    $('#updated').textContent = `Прогноз оновлено: ${when} (Київ)`;
    const ageH = (Date.now() - gen) / 3600000;
    const stale = $('#stale');
    if (ageH > 12) {
      stale.hidden = false;
      stale.textContent = `Дані оновлювалися ${Math.floor(ageH)} год тому і можуть бути застарілими. Перевірте, чи працює автоматичне оновлення.`;
    } else {
      stale.hidden = true;
    }
  }

  function init() {
    NOW = nowKey();
    buildIndex();
    state.aircraft = A.aircraft[0].id;
    state.loc = A.default_location && A.locations.some((l) => l.id === A.default_location) ? A.default_location : A.locations[0].id;
    readHash();
    showUpdated();
    render();
    setInterval(() => {
      const n = nowKey();
      if (n !== NOW) { NOW = n; showUpdated(); render(); }
    }, 60000);
  }

  async function load() {
    try {
      const [a, f] = await Promise.all(['data/assessment.json', 'data/forecast.json'].map((u) =>
        fetch(u, { cache: 'no-cache' }).then((r) => {
          if (!r.ok) throw new Error(`${u}: ${r.status}`);
          return r.json();
        })));
      A = a;
      F = f;
      init();
    } catch (err) {
      $('#view').innerHTML = '<div class="card error"><h3>Дані ще не завантажені</h3>'
        + '<p>Файли прогнозу не знайдені або не відкриваються. Якщо сайт щойно створений, запустіть '
        + 'автоматичне оновлення (вкладка Actions у репозиторії) і оновіть сторінку. '
        + 'Якщо ви відкрили index.html прямо з диска, відкрийте його через GitHub Pages '
        + 'або локальний сервер.</p><p class="none">' + esc(String(err.message || err)) + '</p></div>';
    }
  }

  load();
})();
