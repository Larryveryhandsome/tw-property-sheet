/**
 * 物調表產生器 - 前端邏輯
 */

// ─── 下拉選單選項（從 API 載入）────────────────────────────
let OPTIONS = {};
let currentStep = 1;
let autoData = {}; // 自動查詢結果暫存
let deedData = {}; // 謄本解析結果（土地＋建物合併）
let parcelMap = null;

// ─── 初始化 ──────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadSettings();
  await loadOptions();
  setupCalcListeners();
});

async function loadSettings() {
  try {
    const s = await (await fetch('/api/settings')).json();
    document.getElementById('brand-name').textContent = s.brand;
    document.title = `物調表產生器 | ${s.brand}`;
    window.MOI = s;
    const b = document.getElementById('moi-btn');
    if (b) b.title = s.moi ? `內政部地政平台官方土地標示部，每筆約 NT$1（今天已查 ${s.moi_used_today}/${s.moi_daily_limit}）` : '需要自備地政平台金鑰，見 docs/金鑰申請.md';
  } catch (e) { /* 用預設名稱 */ }
}

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

async function loadOptions() {
  try {
    const res = await fetch('/api/dropdown-options');
    OPTIONS = await res.json();
    fillAllDropdowns();
  } catch (e) {
    console.error('載入選單失敗', e);
  }
}

function fillAllDropdowns() {
  const map = {
    'case_type': 'case_type',
    'status': 'status',
    'exposure': 'exposure',
    'escrow': 'escrow',
    'handover': 'handover',
    'visit_method': 'visit_method',
    'sell_reason': 'sell_reason',
    'loan': 'loan',
    'appearance': 'appearance',
    'structure': 'structure',
    'addon_type': 'addon_type',
    'parking': 'parking',
    'parking_type': 'parking_type',
    'parking_lane': 'parking_lane',
    'parking_reg': 'parking_reg',
    'parking_vehicle': 'parking_vehicle',
    'management': 'management',
    'mgmt_period': 'mgmt_period',
    'utilities': 'utilities',
  };
  for (const [optKey, elId] of Object.entries(map)) {
    fillSelect(elId, OPTIONS[optKey] || []);
  }
  // 可自由輸入的欄位：選項放進 datalist
  ['agent', 'nearby_park', 'nearby_station', 'nearby_market'].forEach(k => {
    const dl = document.getElementById(`${k}-list`);
    if (dl) (OPTIONS[k] || []).forEach(o => dl.appendChild(new Option(o, o)));
  });
  // 方位選單
  const dirs = OPTIONS['direction'] || [];
  ['orientation_build', 'orientation_window', 'orientation_door'].forEach(id => {
    fillSelect(id, dirs, true);
  });
}

function fillSelect(id, options, keepEmpty = true) {
  const el = document.getElementById(id);
  if (!el) return;
  const firstOpt = el.options[0];
  el.innerHTML = '';
  if (keepEmpty || firstOpt) el.appendChild(firstOpt || new Option('請選擇', ''));
  options.forEach(o => {
    el.appendChild(new Option(o, o));
  });
}

// ─── 步驟切換 ─────────────────────────────────────────────────
function goStep(n) {
  document.querySelectorAll('.form-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active', 'done'));
  document.getElementById(`step${n}`)?.classList.add('active');
  document.querySelectorAll('.step').forEach(s => {
    const sn = parseInt(s.dataset.step);
    if (sn < n) s.classList.add('done');
    if (sn === n) s.classList.add('active');
  });
  currentStep = n;
  if (n === 5) buildPreview();
  window.scrollTo(0, 0);
}

// ─── 地址自動查詢 ─────────────────────────────────────────────
async function lookupAddress() {
  const address = val('address');
  if (!address) { alert('請先輸入地址'); return; }

  setStatus('address-status', '🔍 查詢中...（周邊公園、車站在公開站台忙碌時最久可能要 30 秒，查不到會留空，可自己填）', 'loading');
  try {
    const fd = new FormData();
    fd.append('address', address);
    const res = await fetch('/api/lookup-address', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    autoData = await res.json();

    // 填入自動查詢結果
    setIfEmpty('land_section', autoData.land_section);
    setIfEmpty('land_county', autoData.county);
    setIfEmpty('land_number', autoData.land_number);
    setIfEmpty('village', autoData.village);
    setIfEmpty('nearby_park', autoData.nearby_park, true);
    setIfEmpty('nearby_market', autoData.nearby_market, true);
    setIfEmpty('nearby_station', autoData.nearby_station, true);

    // 判斷是否為土地物件
    const isLand = /段.{1,8}地號/.test(address);
    if (isLand) {
      document.getElementById('land_section').value = address.match(/([^\s]+段)/)?.[1] || '';
      document.getElementById('land_number').value = address.match(/(\d+)地號/)?.[1] || '';
    }

    setStatus('address-status',
      `✅ 查詢完成：${autoData.county || ''}${autoData.town || ''}${autoData.village || ''}（公開資料僅供參考，請再確認）`, 'success');
  } catch (e) {
    setStatus('address-status', `❌ 查詢失敗：${e.message}`, 'error');
  }
}

// ─── 謄本解析 ─────────────────────────────────────────────────
async function parseTenement() {
  const files = [...document.getElementById('tenement-file').files];
  if (!files.length) return;
  setStatus('tenement-status', `⚙️ 解析 ${files.length} 份謄本中...`, 'loading');
  const done = [];
  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetch('/api/parse-tenement', { method: 'POST', body: fd });
      if (!res.ok) throw new Error((await res.json()).detail || res.status);
      const data = await res.json();
      if (!data.kind) { done.push(`⚠️ ${file.name}：看不出是土地或建物謄本（可能是掃描圖檔）`); continue; }
      // 建物謄本的地段地號是「坐落地號」；已經有土地謄本時以土地謄本為準
      if (data.kind === 'building' && deedData._landKind) { delete data.land_section; delete data.land_number; }
      if (data.kind === 'land') deedData._landKind = true;
      mergeDeed(deedData, data);
      done.push(`✅ ${file.name}（${data.kind === 'land' ? '土地' : '建物'}謄本）`);
    } catch (e) {
      done.push(`❌ ${file.name}：${e.message}`);
    }
  }
  fillFromDeed(deedData);
  setStatus('tenement-status', done.join('　'), done.some(x => x.startsWith('✅')) ? 'success' : 'error');
  showDeedSummary(deedData);
  runRisk();
}

// 合併土地＋建物謄本：取得日取較近的（判斷短期交易）、謄本日取較舊的（判斷鮮度）、
// 他項權利種類合併，金額取最大（同一筆抵押常同時設定在土地和建物，不重複相加）
function mergeDeed(base, add) {
  const keep = { acquire_date: base.acquire_date, acquire_reason: base.acquire_reason, holding_years: base.holding_years,
    short_term: base.short_term, deed_date: base.deed_date, kinds: base.encumbrance_kinds || [], amount: base.encumbrance_amount };
  Object.assign(base, add);
  if (keep.acquire_date && (!add.acquire_date || keep.acquire_date > add.acquire_date)) {
    base.acquire_date = keep.acquire_date; base.acquire_reason = keep.acquire_reason;
    base.holding_years = keep.holding_years; base.short_term = keep.short_term;
  }
  if (keep.deed_date && (!add.deed_date || keep.deed_date < add.deed_date)) base.deed_date = keep.deed_date;
  const kinds = [...new Set([...keep.kinds, ...(add.encumbrance_kinds || [])])];
  if (kinds.length) base.encumbrance_kinds = kinds;
  const amt = Math.max(keep.amount || 0, add.encumbrance_amount || 0);
  if (amt) base.encumbrance_amount = amt;
  return base;
}

function setSelectValue(id, v) {
  const el = document.getElementById(id);
  if (!el || v == null || v === '') return;
  if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === String(v))) el.appendChild(new Option(v, v));
  el.value = v;
}

function fillFromDeed(d) {
  const plain = ['land_section', 'land_number', 'build_number', 'build_area_main', 'build_area_sub', 'build_area_pub',
    'floor_above', 'floor_current', 'finish_year', 'finish_month', 'finish_day', 'land_area_all', 'land_area_share',
    'land_share', 'land_value_per_sqm', 'land_price_per_sqm'];
  plain.forEach(k => { if (d[k] != null) setVal(k, d[k]); });
  setSelectValue('structure', d.structure);
  setSelectValue('land_ownership', d.land_ownership);
  if (d.land_zone_type) { setSelectValue('land_zone_type', d.land_zone_type); onZoneTypeChange(); }
  setSelectValue('land_zone_name', d.land_zone_name);
  setSelectValue('land_use_type', d.land_use_type);
  calcTotal();
  calcAge();
}

function showDeedSummary(d) {
  const rows = [
    ['建號', d.build_number],
    ['地段地號', [d.land_section, d.land_number].filter(Boolean).join(' ')],
    ['主建／附屬／公設（坪）', (d.build_area_main != null) ? [d.build_area_main, d.build_area_sub, d.build_area_pub].map(v => v ?? '─').join(' ／ ') : ''],
    ['各層面積', (d.layers || []).map(l => `${l.floor} ${l.ping} 坪`).join('、')],
    ['樓層', d.floor_current ? `${d.floor_current} 樓 / 共 ${d.floor_above ?? '─'} 層` : ''],
    ['結構', d.structure],
    ['完工', d.finish_year ? `民國 ${d.finish_year} 年 ${d.finish_month ?? '─'} 月（屋齡 ${d.age_years ?? '─'} 年）` : ''],
    ['土地面積', d.land_area_all ? `${d.land_area_all} 坪（持分 ${d.land_area_share ?? '─'} 坪，${d.land_share || ''}）` : ''],
    ['使用分區', [d.land_zone_type, d.land_zone_name, d.land_use_type].filter(Boolean).join('，')],
    ['公告現值', d.land_value_per_sqm ? `${Number(d.land_value_per_sqm).toLocaleString()} 元/㎡` : ''],
    ['取得日', d.acquire_date ? `${d.acquire_date} ${d.acquire_reason || ''}（持有約 ${d.holding_years} 年）` : ''],
    ['他項權利', (d.encumbrance_kinds || []).join('、') + (d.encumbrance_amount ? `（擔保 ${Number(d.encumbrance_amount).toLocaleString()} 元）` : '')],
    ['謄本查詢日', d.deed_date],
  ].filter(([, v]) => v && String(v).trim());
  const box = document.getElementById('tenement-result');
  box.style.display = 'block';
  box.innerHTML = '<h4>✅ 謄本解析結果</h4><table class="result-table">' +
    rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') + '</table>';
}

async function runRisk() {
  const total = (numVal('build_area_main') || 0) + (numVal('build_area_sub') || 0) + (numVal('build_area_pub') || 0);
  const deed = Object.assign({}, deedData, { build_area_total: total ? Math.round(total * 100) / 100 : null });
  const fd = new FormData();
  fd.append('deed_json', JSON.stringify(deed));
  fd.append('form_json', JSON.stringify({ claimed_build_area_total: numVal('claimed_build_area_total'),
    claimed_land_area_share: numVal('claimed_land_area_share') }));
  try {
    const r = await (await fetch('/api/risk', { method: 'POST', body: fd })).json();
    const icon = { red: '🔴', yellow: '🟡', green: '🟢' };
    document.getElementById('risk-box').innerHTML = `<b>${esc(r.summary)}</b><ul class="risk-list">` +
      r.items.map(i => `<li class="risk-${i.level}">${icon[i.level]} <b>${esc(i.item)}</b>：${esc(i.msg)}</li>`).join('') + '</ul>';
    setVal('risk_notes', r.text);
  } catch (e) {
    document.getElementById('risk-box').textContent = '風險檢查失敗：' + e.message;
  }
}

async function lookupParcel() {
  const county = val('land_county'), section = val('land_section'), number = val('land_number');
  if (!county || !section || !number) {
    setStatus('parcel-status', '請先填縣市、地段、地號（可以先按地址「自動查詢」或上傳謄本）', 'error');
    return;
  }
  setStatus('parcel-status', '🔍 查詢國土測繪地籍圖中...', 'loading');
  const fd = new FormData();
  fd.append('county', county);
  fd.append('section', section);
  fd.append('number', number);
  try {
    const res = await fetch('/api/parcel', { method: 'POST', body: fd });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || res.status);
    document.getElementById('parcel-map').style.display = 'block';
    if (!parcelMap) {
      parcelMap = L.map('parcel-map');
      L.tileLayer('https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}', {
        maxZoom: 19, attribution: '© 內政部國土測繪中心' }).addTo(parcelMap);
    }
    parcelMap.eachLayer(l => { if (l instanceof L.ImageOverlay) parcelMap.removeLayer(l); });
    d.parts.forEach(p => L.imageOverlay('data:image/png;base64,' + p.image, [[p.ly, p.lx], [p.ry, p.rx]], { opacity: 0.85 }).addTo(parcelMap));
    parcelMap.fitBounds([[d.ly, d.lx], [d.ry, d.rx]], { padding: [30, 30], maxZoom: 19 });
    setTimeout(() => parcelMap.invalidateSize(), 50);
    setIfEmpty('width', d.width_m);
    setIfEmpty('depth', d.depth_m);
    if (!val('land_area_all') && d.area_ping) setVal('land_area_all', d.area_ping);
    setStatus('parcel-status', `✅ ${d.town} ${d.section} ${number} 地號：約 ${d.area_ping ?? '─'} 坪（圖面估算，正式以謄本為準），外框約 ${d.width_m} × ${d.depth_m} 公尺` +
      (d.parts.length > 1 ? `，由 ${d.parts.length} 塊組成` : ''), 'success');
  } catch (e) {
    setStatus('parcel-status', `❌ ${e.message}`, 'error');
  }
}

async function officialLand(confirmed) {
  if (!window.MOI || !window.MOI.moi) {
    const el = document.getElementById('moi-status');
    el.className = 'status-msg error';
    el.innerHTML = '這個功能要自備「內政部地政整合資訊服務共享協作平台」金鑰，每筆約 NT$1。' +
      '<a href="https://github.com/Larryveryhandsome/tw-property-sheet/blob/main/docs/%E9%87%91%E9%91%B0%E7%94%B3%E8%AB%8B.md" target="_blank" rel="noopener">看申請與設定教學</a>';
    return;
  }
  const county = val('land_county'), section = val('land_section'), number = val('land_number');
  if (!county || !section || !number) { setStatus('moi-status', '請先填縣市、地段、地號', 'error'); return; }
  const fd = new FormData();
  fd.append('county', county); fd.append('section', section); fd.append('number', number);
  fd.append('compare_ping', val('land_area_all'));
  if (confirmed) fd.append('confirm', '1');
  setStatus('moi-status', '🏛️ 查詢地政平台中...', 'loading');
  try {
    const res = await fetch('/api/official-land', { method: 'POST', body: fd });
    const d = await res.json();
    if (res.status === 409) {
      const el = document.getElementById('moi-status');
      el.className = 'status-msg loading';
      el.innerHTML = `這筆（${esc(section)} ${esc(number)} 地號）要向地政平台查詢，<b>會收費（約 NT$1）</b>。今天已查 ${window.MOI.moi_used_today} 筆。 <button class="btn-small" id="moi-confirm">確認查詢</button>`;
      document.getElementById('moi-confirm').onclick = () => officialLand(true);
      return;
    }
    if (!res.ok) throw new Error(d.detail || res.status);
    window.MOI.moi_used_today = d.used_today;
    const cmp = d.compare && d.compare.other_ping ? `；表單上的土地坪數 ${d.compare.other_ping} 坪，相差 ${d.compare.diff_pct}%${d.compare.ok ? '' : ' ⚠️ 超過 3%，請確認'}` : '';
    setStatus('moi-status', `✅ 官方面積 ${d.area_sqm.toLocaleString()} ㎡（${d.area_ping} 坪）${d.cached ? '（之前查過，這次沒收費）' : ''}${cmp}`, d.compare && d.compare.ok === false ? 'error' : 'success');
    if (!val('land_area_all')) setVal('land_area_all', d.area_ping);
  } catch (e) {
    setStatus('moi-status', `❌ ${e.message}`, 'error');
  }
}

function previewPhotos() {
  const box = document.getElementById('photo-preview');
  box.innerHTML = '';
  [...document.getElementById('site-photos').files].slice(0, 6).forEach(f => {
    const img = document.createElement('img');
    img.src = URL.createObjectURL(f);
    img.style.cssText = 'height:80px;border-radius:6px';
    img.alt = f.name;
    box.appendChild(img);
  });
}

// ─── 動態選單邏輯 ─────────────────────────────────────────────
function onCaseTypeChange() {
  const ct = val('case_type');
  const needDimensions = ['透天', '別墅', '農舍', '小農舍'].includes(ct);
  const el = document.getElementById('land-dimensions');
  if (el) el.style.display = needDimensions ? 'block' : 'none';
}

function onVisitMethodChange() {
  const m = val('visit_method');
  const contact = document.getElementById('visit_contact');
  const keyNo = document.getElementById('visit_key_no');
  const extra = document.getElementById('visit-extra');
  contact.style.display = 'none';
  keyNo.style.display = 'none';
  if (extra) extra.style.display = 'none';
  if (m === '聯絡') {
    contact.style.display = 'block';
    if (extra) extra.style.display = 'flex';
  } else if (m === '鑰匙') {
    keyNo.style.display = 'block';
    if (extra) extra.style.display = 'flex';
  }
}

function onZoneTypeChange() {
  const zt = val('land_zone_type');
  const nameEl = document.getElementById('land_zone_name');
  const useEl = document.getElementById('land_use_type_group');
  nameEl.innerHTML = '<option value="">請選擇</option>';

  if (zt === '都計內') {
    (OPTIONS['land_zone_name_urban'] || []).forEach(o => nameEl.appendChild(new Option(o, o)));
    if (useEl) useEl.style.display = 'none';
  } else if (zt === '都計外') {
    (OPTIONS['land_zone_name_rural'] || []).forEach(o => nameEl.appendChild(new Option(o, o)));
    if (useEl) {
      useEl.style.display = 'block';
      const sel = document.getElementById('land_use_type');
      sel.innerHTML = '<option value="">請選擇</option>';
      (OPTIONS['land_use_type'] || []).forEach(o => sel.appendChild(new Option(o, o)));
    }
  }
}

function onParkingChange() {
  const p = val('parking');
  const typeGrp = document.getElementById('parking-type-group');
  const laneGrp = document.getElementById('parking-lane-group');
  const detail = document.getElementById('parking-detail');
  const show = p === '有';
  if (typeGrp) typeGrp.style.display = show ? 'block' : 'none';
  if (laneGrp) laneGrp.style.display = show ? 'block' : 'none';
  if (detail) detail.style.display = show ? 'flex' : 'none';
}

function onManagementChange() {
  const m = val('management');
  const feeGrp = document.getElementById('mgmt-fee-group');
  const periodGrp = document.getElementById('mgmt-period-group');
  const show = m && m !== '無管理';
  if (feeGrp) feeGrp.style.display = show ? 'block' : 'none';
  if (periodGrp) periodGrp.style.display = show ? 'block' : 'none';
}

// ─── 計算 ─────────────────────────────────────────────────────
function calcTotal() {
  const main = parseFloat(val('build_area_main')) || 0;
  const sub = parseFloat(val('build_area_sub')) || 0;
  const pub = parseFloat(val('build_area_pub')) || 0;
  const total = (main + sub + pub).toFixed(2);
  const el = document.getElementById('total-area-display');
  if (el) el.textContent = `總建坪：${total} 坪`;
}

function calcAge() {
  const yr = parseInt(val('finish_year'));
  const mo = parseInt(val('finish_month')) || 1;
  const dy = parseInt(val('finish_day')) || 1;
  const el = document.getElementById('age-display');
  if (!el || !yr) return;
  const adYear = yr + 1911;
  const built = new Date(adYear, mo - 1, dy);
  const now = new Date();
  const years = ((now - built) / (365.25 * 24 * 3600 * 1000)).toFixed(1);
  el.textContent = `屋齡：${years} 年`;
}

function setupCalcListeners() {
  ['build_area_main', 'build_area_sub', 'build_area_pub'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', calcTotal);
  });
  ['finish_year', 'finish_month', 'finish_day'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', calcAge);
  });
}

// ─── 外部工具 ─────────────────────────────────────────────────
function openNLSC() {
  const section = val('land_section') || '';
  const number = val('land_number') || '';
  // 清理地號：0123-0000 → 123
  const cleanNum = number.replace(/^0+/, '').split('-')[0];
  window.open(`https://maps.nlsc.gov.tw/T09/mapshow.action#`, '_blank');
  if (section && cleanNum) {
    setTimeout(() => alert(`請在國土測繪查詢：\n地段：${section}\n地號：${cleanNum}\n\n查完後回來選擇分區`), 500);
  }
}

function openStreetView() {
  const addr = val('address');
  if (!addr) { alert('請先輸入地址'); return; }
  const q = encodeURIComponent(addr);
  window.open(`https://maps.google.com/maps?q=${q}&layer=c`, '_blank');
}

// ─── 預覽 ─────────────────────────────────────────────────────
function buildPreview() {
  const box = document.getElementById('preview-box');
  if (!box) return;
  const rows = [
    ['物件編號', val('case_number')],
    ['地址', val('address')],
    ['總價', val('total_price') ? `${val('total_price')} 萬` : ''],
    ['案件型態', val('case_type')],
    ['現況', val('status')],
    ['樓層', val('floor_current') ? `${val('floor_current')}樓 / 共${val('floor_above')}樓` : ''],
    ['主建坪', val('build_area_main')],
    ['結構', val('structure')],
    ['外觀', val('appearance')],
    ['格局', (val('room_count') || val('hall_count') || val('bath_count')) ? `${val('room_count') || 0}房${val('hall_count') || 0}廳${val('bath_count') || 0}衛` : ''],
    ['完工', val('finish_year') ? `民國${val('finish_year')}年${val('finish_month')}月` : ''],
    ['土地屬性', `${val('land_zone_type')} ${val('land_zone_name')}`],
    ['車位', val('parking')],
    ['管理', val('management')],
    ['學區', [val('school_elem') && `國小：${val('school_elem')}`, val('school_mid') && `國中：${val('school_mid')}`].filter(Boolean).join(' / ')],
    ['營業員', val('agent')],
  ];
  box.innerHTML = `<h3>📋 資料摘要</h3><table class="result-table">` +
    rows.filter(([, v]) => v && v.trim()).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') +
    '</table>';

  // 自動觸發案名生成
  if (!val('case_name') && val('address')) regenName();
}

// ─── AI 生成 ──────────────────────────────────────────────────
async function regenName() {
  if (!val('address')) return;
  const fd = new FormData();
  fd.append('form_json', JSON.stringify(collectForm()));
  try {
    const res = await fetch('/api/generate-name', { method: 'POST', body: fd });
    setVal('case_name', (await res.json()).case_name);
  } catch (e) {
    console.error('案名生成失敗', e);
  }
}

async function regenSellPoints() {
  const old = val('sell_points');
  setVal('sell_points', '⏳ 生成中...');
  const fd = new FormData();
  fd.append('form_json', JSON.stringify(collectForm()));
  try {
    const res = await fetch('/api/generate-sell-points', { method: 'POST', body: fd });
    setVal('sell_points', (await res.json()).sell_points || old);
  } catch (e) {
    setVal('sell_points', old);
    alert('銷售重點生成失敗');
  }
}

// ─── 收集表單資料 ─────────────────────────────────────────────
function collectForm() {
  return {
    case_number: val('case_number'),
    case_name: val('case_name'),
    address: val('address'),
    total_price: numVal('total_price'),
    case_type: val('case_type'),
    status: val('status'),
    exposure: val('exposure'),
    escrow: val('escrow'),
    handover: val('handover'),
    visit_method: val('visit_method'),
    visit_contact: val('visit_contact'),
    visit_key_no: val('visit_key_no'),
    sell_reason: val('sell_reason'),
    agent: val('agent'),
    loan: val('loan'),
    land_section: val('land_section'),
    land_number: val('land_number'),
    build_number: val('build_number'),
    land_ownership: val('land_ownership'),
    land_area_all: numVal('land_area_all'),
    land_area_share: numVal('land_area_share'),
    land_zone_type: val('land_zone_type'),
    land_zone_name: val('land_zone_name'),
    land_use_type: val('land_use_type'),
    vacant_ratio: val('vacant_ratio'),
    road_frontage: numVal('road_frontage'),
    width: numVal('width'),
    depth: numVal('depth'),
    appearance: val('appearance'),
    structure: val('structure'),
    addon_type: val('addon_type'),
    is_corner: val('is_corner'),
    has_courtyard: val('has_courtyard'),
    build_area_main: numVal('build_area_main'),
    build_area_sub: numVal('build_area_sub'),
    build_area_pub: numVal('build_area_pub'),
    floor_current: numVal('floor_current'),
    floor_above: numVal('floor_above'),
    floor_below: numVal('floor_below'),
    finish_year: numVal('finish_year'),
    finish_month: numVal('finish_month'),
    finish_day: numVal('finish_day'),
    room_count: numVal('room_count'),
    hall_count: numVal('hall_count'),
    bath_count: numVal('bath_count'),
    orientation_build: val('orientation_build'),
    orientation_window: val('orientation_window'),
    orientation_door: val('orientation_door'),
    school_elem: val('school_elem'),
    school_mid: val('school_mid'),
    village: val('village'),
    nearby_park: val('nearby_park'),
    nearby_station: val('nearby_station'),
    nearby_market: val('nearby_market'),
    parking: val('parking'),
    parking_type: val('parking_type'),
    parking_no: val('parking_no'),
    parking_lane: val('parking_lane'),
    parking_reg: val('parking_reg'),
    parking_vehicle: val('parking_vehicle'),
    management: val('management'),
    mgmt_fee: numVal('mgmt_fee'),
    mgmt_period: val('mgmt_period'),
    utilities: val('utilities'),
    sell_points: val('sell_points'),
    notes: val('notes'),
    others: val('others'),
    depth: numVal('depth'),
    land_share: val('land_share'),
    land_value_per_sqm: numVal('land_value_per_sqm'),
    land_price_per_sqm: numVal('land_price_per_sqm'),
    claimed_build_area_total: numVal('claimed_build_area_total'),
    claimed_land_area_share: numVal('claimed_land_area_share'),
    risk_notes: val('risk_notes'),
    main_use: deedData.main_use || null,
    build_share: deedData.build_share || null,
    acquire_date: deedData.acquire_date || null,
    acquire_reason: deedData.acquire_reason || null,
    holding_years: deedData.holding_years ?? null,
    encumbrance_kinds: deedData.encumbrance_kinds || [],
    encumbrance_amount: deedData.encumbrance_amount ?? null,
    deed_date: deedData.deed_date || null,
  };
}

// ─── 產生 Excel ───────────────────────────────────────────────
async function generateExcel() {
  const btn = document.getElementById('download-btn');
  btn.disabled = true;
  btn.textContent = '⏳ 生成中...';
  setStatus('generate-status', '⏳ 正在生成 Excel，請稍候...', 'loading');

  try {
    const fd = new FormData();
    fd.append('form_json', JSON.stringify(collectForm()));
    [...document.getElementById('site-photos').files].slice(0, 6).forEach(f => fd.append('photos', f));
    const res = await fetch('/api/generate', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());

    const missing = (res.headers.get('X-Missing-Fields') || '').split(',').filter(Boolean);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const addr = val('address') || '物調表';
    a.href = url;
    a.download = `物調表_${addr.substring(0, 15)}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);

    setStatus('generate-status', missing.length ? `✅ 下載完成！還有 ${missing.length} 個必填欄位沒填（Excel 裡標黃）` : '✅ 下載完成！', 'success');
  } catch (e) {
    setStatus('generate-status', `❌ 生成失敗：${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '📥 產生並下載物調表 Excel';
  }
}

// ─── 工具函數 ─────────────────────────────────────────────────
function val(id) {
  const el = document.getElementById(id);
  return el ? el.value.trim() : '';
}
function numVal(id) {
  const v = val(id);
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}
function setVal(id, value) {
  const el = document.getElementById(id);
  if (el && value != null) el.value = value;
}
function setIfEmpty(id, value, isSelect = false) {
  const el = document.getElementById(id);
  if (!el || !value) return;
  if (!el.value || el.value === '') el.value = value;
}
function setStatus(id, msg, type) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.className = `status-msg ${type}`;
}
