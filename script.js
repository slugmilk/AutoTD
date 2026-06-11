/* ════════════════════════════════════════
   AutoTD Web – JavaScript
   백엔드 API (FastAPI) + WebSocket 연동
   ════════════════════════════════════════ */

// ── 백엔드 설정 ──────────────────────────────────────────────────
const API_BASE = 'http://localhost:8000';
const WS_URL   = 'ws://localhost:8000/ws';

// ── 상태 관리 ────────────────────────────────────────────────────
const state = {
  wsConnected: false,
  tdConnected: false,
  generating:  false,
  lastParams:  null,
  lastPayload:  null,
};

// ?€?€ WebSocket ?몄뒪?댁뒪 ?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€?€
let ws = null;

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  珥덇린??
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
document.addEventListener('DOMContentLoaded', () => {
  initNavbar();
  initHamburger();
  initCharCounter();
  initDropdowns();
  initRecipeSelector();
  initParticleCanvas();
  initPreviewCanvas();
  initScrollAnimations();
  initSmoothScroll();
  initToastStyle();

  // 諛깆뿏???곌껐 ?뺤씤 (?쒕쾭媛€ ?ㅽ뻾 以묒씪 ?뚮쭔)
  checkServerStatus();
  connectWebSocket();
});

async function checkServerStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      const data = await res.json();
      updateStatusIndicator({
        server: true,
        gemini: data.gemini === 'ready',
        td: data.touchdesigner === 'connected',
        tdMsg: data.touchdesigner,
      });
    }
  } catch {
  }
}

function updateStatusIndicator({ server, gemini, td, tdMsg }) {
  const badge = document.querySelector('.hero-badge');
  if (!badge) return;

  const indicators = [];
  if (server) indicators.push('<span style="color:#86efac">● Server</span>');
  if (gemini) indicators.push('<span style="color:#86efac">● Gemini</span>');
  else        indicators.push('<span style="color:#fca5a5">● Gemini</span>');
  if (td)     indicators.push('<span style="color:#86efac">● TouchDesigner</span>');
  else        indicators.push(`<span style="color:#fca5a5" title="${tdMsg}">● TouchDesigner</span>`);

  badge.innerHTML = `<span class="badge-dot"></span> ${indicators.join(' · ')}`;
  state.tdConnected = td;
}

function connectWebSocket() {
  try {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      state.wsConnected = true;
      ws.send(JSON.stringify({ type: 'ping' }));
    };

    ws.onmessage = (e) => handleWsMessage(JSON.parse(e.data));

    ws.onclose = () => {
      state.wsConnected = false;
      setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = () => {
      state.wsConnected = false;
    };
  } catch {
  }
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Generate 踰꾪듉 ???듭떖 濡쒖쭅
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function handleWsMessage(data) {
  if (data.type === 'pong') return;

  if (data.type === 'thinking_start') {
    const thoughtBox = document.getElementById('ai-thought-box');
    const thoughtContent = document.getElementById('thought-content');
    if (thoughtBox) thoughtBox.style.display = 'block';
    if (thoughtContent) {
      thoughtContent.textContent = 'Ollama is translating the prompt into a TouchDesigner node plan...\n';
    }
    resetRuntimeInspector();
  }

  if (data.type === 'thinking_delta') {
    const thoughtContent = document.getElementById('thought-content');
    if (thoughtContent && data.delta) {
      thoughtContent.textContent += data.delta;
      const thoughtBox = document.getElementById('ai-thought-box');
      if (thoughtBox) thoughtBox.scrollTop = thoughtBox.scrollHeight;
    }
  }

  if (data.type === 'step') {
    updateGenStep(data.step, data.message);
    appendThoughtLine(data.message);
  }

  if (data.type === 'agent_plan') {
    renderAgentPlan(data);
  }

  if (data.type === 'source_assets') {
    renderSourceAssets(data.assets || [], data.td_applied || {}, data.source_status || null);
  }

  if (data.type === 'complete') {
    state.lastParams = data.parameters;
    renderResultInspector(data);
    finishGeneration(data);
  }
}

const generateBtn = document.getElementById('generate-btn');
const genModal    = document.getElementById('gen-modal');
const previewOverlay = document.getElementById('preview-overlay');
const previewImage = document.getElementById('preview-image');
const sourceStatus = document.getElementById('source-status');
const sourceList = document.getElementById('source-list');
const tdPlanStatus = document.getElementById('td-plan-status');
const tdPlanText = document.getElementById('td-plan-text');
const sourceGenerateImage = document.getElementById('source-generate-image');
const sourceGenerate3d = document.getElementById('source-generate-3d');
generateBtn.addEventListener('click', handleGenerate);

async function handleGenerate() {
  const promptVal = document.getElementById('prompt-input').value.trim();
  if (!promptVal) {
    shakeElement(document.querySelector('.prompt-box'));
    return;
  }
  if (state.generating) return;
  state.generating = true;

  const recipeSelect = document.getElementById('recipe-select');
  const recipeId = recipeSelect ? recipeSelect.value : 'auto';

  const payload = {
    prompt: promptVal,
    recipe_id: recipeId,
    source_options: {
      generate_image: Boolean(sourceGenerateImage?.checked),
      generate_3d: Boolean(sourceGenerate3d?.checked),
    },
  };
  state.lastPayload = payload;

  // 紐⑤떖 ?닿린
  openGenModal();
  resetRuntimeInspector();
  if (state.wsConnected && ws?.readyState === WebSocket.OPEN) {
    updateGenStep(1, 'Analyzing prompt with Ollama...');
    ws.send(JSON.stringify({ type: 'generate', payload }));
  } else {
    await generateViaHttp(payload);
  }
}

async function generateViaHttp(payload) {
  updateGenStep(1, '프롬프트 분석 중...');
  
  // ?앷컖 諛뺤뒪 ?몄텧
  const thoughtBox = document.getElementById('ai-thought-box');
  const thoughtContent = document.getElementById('thought-content');
  if (thoughtBox) thoughtBox.style.display = 'block';
  if (thoughtContent) thoughtContent.textContent = 'AI가 시각 콘셉트를 설계하는 중...';

  let result = null;
  try {
    const res = await fetch(`${API_BASE}/api/generate`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
      signal:  AbortSignal.timeout(360000),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '서버 오류');
    }
    result = await res.json();
  } catch (err) {
    state.generating = false;
    closeGenModal();
    showPreviewMessage('Backend unavailable. TouchDesigner preview was not updated.');
    showToast(`오류: ${err.message}`, 'error');
    return;
  }

  // Gemini ?앷컖怨쇱젙 ??댄븨 ?좊땲硫붿씠??
  if (result.reasoning) {
    if (thoughtContent) thoughtContent.textContent = 'AI가 시각 콘셉트를 설계하는 중...';
    await typeThought(result.reasoning, 20);
  } else {
    if (thoughtContent) thoughtContent.textContent = '데모 모드로 동작하여 간략 분석 정보를 사용합니다.';
    await sleep(800);
  }

  updateGenStep(2, '시각 구조 계획 중...');
  await sleep(600);

  updateGenStep(3, '소스 생성 중...');
  await sleep(600);

  updateGenStep(4, 'TouchDesigner 템플릿 실행 중...');
  await sleep(600);

  updateGenStep(5, '미리보기 준비 중...');
  await sleep(400);
  finishGeneration(result);
}

// ??댄븨 ?④낵 ?ы띁
function typeThought(text, speed = 20) {
  const content = document.getElementById('thought-content');
  const box = document.getElementById('ai-thought-box');
  if (!content) return Promise.resolve();
  content.textContent = '';
  return new Promise((resolve) => {
    let i = 0;
    function type() {
      if (i < text.length) {
        content.textContent += text.charAt(i);
        i++;
        if (box) box.scrollTop = box.scrollHeight;
        setTimeout(type, speed);
      } else {
        resolve();
      }
    }
    type();
  });
}

function makeDemoResult(payload) {
  const colorPresets = {
    Monochrome: { r: 1, g: 1, b: 1 },
    Neon:       { r: 0.2, g: 1, b: 0.8 },
    Warm:       { r: 1, g: 0.4, b: 0.1 },
    Cool:       { r: 0.2, g: 0.5, b: 1 },
    Pastel:     { r: 0.9, g: 0.8, b: 1 },
  };
  const c = colorPresets[payload.color] || colorPresets.Monochrome;
  const densityMap = { Low: 0.25, Medium: 0.5, High: 0.75, Ultra: 1.0 };
  const motionSpeedMap = { Static: 0, Flowing: 0.5, Pulsing: 0.6, Turbulent: 0.9 };

  return {
    success: true,
    message: '[Demo mode] Backend is unavailable.',
    parameters: {
      template: 'particle',
      style: (payload.style || 'Ethereal').toLowerCase(),
      motion: (payload.motion || 'Flowing').toLowerCase(),
      color_mode: (payload.color || 'Monochrome').toLowerCase(),
      color_r: c.r,
      color_g: c.g,
      color_b: c.b,
      speed: motionSpeedMap[payload.motion || 'Flowing'] ?? 0.5,
      density: densityMap[payload.particles || 'High'] ?? 0.7,
      scale: 1.0,
      turbulence: payload.motion === 'Turbulent' ? 0.9 : 0.3,
      brightness: 0.8,
      keywords: payload.prompt.split(' ').slice(0, 5),
      mood: 'ethereal',
      description: payload.prompt.slice(0, 80),
    },
    td_status: 'skipped',
    td_message: 'Demo mode: backend not connected.',
  };
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  ?앹꽦 ?꾨즺 泥섎━
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function showPreviewMessage(message) {
  const msg = document.querySelector('#preview-overlay .preview-idle-msg p');
  if (msg) msg.textContent = message;
  previewOverlay.classList.remove('hidden');
}

async function loadTdPreview(previewUrl) {
  if (!previewImage || !previewUrl) {
    showPreviewMessage('TouchDesigner preview is unavailable.');
    return;
  }

  previewOverlay.classList.remove('hidden');
  showPreviewMessage('Loading TouchDesigner preview...');
  previewImage.hidden = true;
  previewImage.removeAttribute('src');

  const previewSrc = `${previewUrl}${previewUrl.includes('?') ? '&' : '?'}t=${Date.now()}`;

  try {
    const res = await fetch(previewSrc, { cache: 'no-store' });
    if (!res.ok) throw new Error(`Preview HTTP ${res.status}`);

    const blob = await res.blob();
    const imageUrl = URL.createObjectURL(blob);
    previewImage.onload = () => {
      previewOverlay.classList.add('hidden');
      previewImage.hidden = false;
      if (previewImage.dataset.objectUrl) {
        URL.revokeObjectURL(previewImage.dataset.objectUrl);
      }
      previewImage.dataset.objectUrl = imageUrl;
    };
    previewImage.onerror = () => {
      URL.revokeObjectURL(imageUrl);
      showPreviewMessage('TouchDesigner preview could not be decoded.');
    };
    previewImage.src = imageUrl;
  } catch (err) {
    showPreviewMessage('TouchDesigner preview could not be loaded.');
    showToast(`???? ?? ??: ${err.message}`, 'warn');
  }
}

function resetRuntimeInspector() {
  if (sourceStatus) sourceStatus.textContent = 'Procedural TouchDesigner Recipe: waiting';
  if (sourceList) sourceList.innerHTML = '';
  if (tdPlanStatus) tdPlanStatus.textContent = 'Waiting for recipe';
  if (tdPlanText) tdPlanText.textContent = 'Generate to inspect the selected recipe, parameters, and planned TD nodes.';
}

function appendThoughtLine(message) {
  const thoughtContent = document.getElementById('thought-content');
  const thoughtBox = document.getElementById('ai-thought-box');
  if (!thoughtContent || !message) return;
  thoughtContent.textContent += `\n- ${message}`;
  if (thoughtBox) thoughtBox.scrollTop = thoughtBox.scrollHeight;
}

function formatReasoning(payload) {
  if (!payload) return '';
  const text = typeof payload === 'string' ? payload : (payload.reasoning || payload.delta || '');
  if (!text) return '';
  return text.endsWith('\n') ? text : `${text}\n`;
}

function renderAgentPlan(data) {
  const plan = data.td_plan || data.asset_plan?.td_mcp_plan || {};
  const params = data.parameters || data.params || {};
  const recipeId = params.recipe_id || plan.recipe_id || data.recipe_id || 'pending';
  if (tdPlanStatus) {
    const count = Array.isArray(plan.nodes) ? plan.nodes.length : 0;
    tdPlanStatus.textContent = count ? `${recipeId} · ${count} TD nodes planned` : `${recipeId} · recipe selected`;
  }
  if (tdPlanText) {
    tdPlanText.textContent = JSON.stringify({
      selected_recipe: recipeId,
      generated_parameters: {
        color_palette: params.color_palette || params.color_mode,
        noise_strength: params.noise_strength,
        motion_speed: params.motion_speed || params.speed,
        feedback_opacity: params.feedback_opacity,
        glow: params.glow || params.brightness,
        particle_density: params.particle_density || params.density,
      },
      nodes: plan.nodes || [],
      notes: plan.notes || '',
    }, null, 2);
  }
}

function sourceStatusLabel(entry, fallback) {
  if (!entry?.requested) return `${fallback}: not requested`;
  const status = entry.status || 'requested';
  return `${fallback}: ${status}`;
}

function sourceFailureMessage(sourceStatusData) {
  if (!sourceStatusData) return '';
  const explicit = sourceStatusData.source_generation_errors;
  if (Array.isArray(explicit) && explicit.length) return explicit.join(' / ');
  const errors = [];
  for (const entry of [sourceStatusData.image, sourceStatusData.object_3d]) {
    if (entry?.requested && (entry.status === 'failed' || entry.loaded === false)) {
      errors.push(entry.error || `${entry.prompt || 'source'} was requested but not loaded`);
    }
  }
  if (Array.isArray(sourceStatusData.touchdesigner_errors)) {
    errors.push(...sourceStatusData.touchdesigner_errors.map(e => e.message || e.reason || String(e)));
  }
  return errors.filter(Boolean).join(' / ');
}

function sourceStatusFromResult(result) {
  if (!result) return null;
  if (result.source_status) return result.source_status;
  const options = result.source_options || {};
  const imageRequested = Boolean(result.image_asset_requested ?? options.generate_image);
  const meshRequested = Boolean(result.asset_3d_requested ?? options.generate_3d);
  return {
    source_options: {
      generate_image: imageRequested,
      generate_3d: meshRequested,
    },
    source_generation_errors: result.source_generation_errors || [],
    image: {
      requested: imageRequested,
      status: imageRequested ? (result.image_loaded_in_td ? 'loaded' : 'failed') : 'not_requested',
      path: result.image_asset_path || '',
      loaded: Boolean(result.image_loaded_in_td),
      width: result.telemetry?.image_width || 0,
      height: result.telemetry?.image_height || 0,
      error: imageRequested && !result.image_loaded_in_td ? 'Image source requested but not loaded in TouchDesigner.' : '',
    },
    object_3d: {
      requested: meshRequested,
      status: meshRequested ? (result.asset_3d_loaded_in_td ? 'loaded' : 'failed') : 'not_requested',
      path: result.asset_3d_obj_path || result.asset_3d_glb_path || '',
      obj_path: result.asset_3d_obj_path || '',
      glb_path: result.asset_3d_glb_path || '',
      loaded: Boolean(result.asset_3d_loaded_in_td),
      point_count: result.point_count || 0,
      primitive_count: result.primitive_count || 0,
      error: meshRequested && !result.asset_3d_loaded_in_td ? '3D source requested but not loaded in TouchDesigner.' : '',
    },
  };
}

function appendFact(parent, label, value) {
  if (value === undefined || value === null || value === '') return;
  const item = document.createElement('span');
  item.textContent = `${label}: ${value}`;
  parent.appendChild(item);
}

function renderSourceStatusCard(kind, entry) {
  const item = document.createElement('div');
  const status = entry?.status || 'not_requested';
  item.className = `source-item ${status === 'loaded' ? 'loaded' : ''} ${status === 'failed' ? 'failed' : ''}`;

  const meta = document.createElement('div');
  meta.className = 'source-meta';
  const title = document.createElement('strong');
  title.textContent = kind === 'image' ? 'Image Source · OpenAI' : '3D Source · Trellis';
  const path = document.createElement('span');
  path.textContent = entry?.path || entry?.obj_path || entry?.glb_path || entry?.error || 'No path returned';

  const facts = document.createElement('div');
  facts.className = 'source-facts';
  appendFact(facts, 'status', status);
  appendFact(facts, 'loaded', entry?.loaded === true ? 'true' : entry?.loaded === false ? 'false' : '');
  if (kind === 'image') {
    appendFact(facts, 'size', entry?.width && entry?.height ? `${entry.width}x${entry.height}` : '');
    appendFact(facts, 'usage', entry?.usage);
  } else {
    appendFact(facts, 'points', entry?.point_count);
    appendFact(facts, 'prims', entry?.primitive_count);
    appendFact(facts, 'usage', entry?.usage);
  }

  meta.appendChild(title);
  meta.appendChild(path);
  meta.appendChild(facts);
  item.appendChild(meta);
  return item;
}

function renderSourceAssets(assets, tdApplied = {}, sourceStatusData = null) {
  const list = Array.isArray(assets) ? assets : [];
  if (sourceStatusData?.mode === 'procedural_recipe_mvp') {
    if (sourceStatus) {
      sourceStatus.textContent = 'Procedural TouchDesigner Recipe: active';
    }
    if (!sourceList) return;
    sourceList.innerHTML = '';

    const recipe = document.createElement('div');
    recipe.className = 'source-item loaded';
    recipe.innerHTML = `
      <div class="source-meta">
        <strong>Procedural TouchDesigner Recipe</strong>
        <span>active · Ollama selects recipe_id and parameters only</span>
        <div class="source-facts">
          <span>external image: experimental</span>
          <span>external 3D: experimental</span>
        </div>
      </div>
    `;
    sourceList.appendChild(recipe);

    const image = document.createElement('div');
    image.className = 'source-item';
    image.innerHTML = `
      <div class="source-meta">
        <strong>External Image Source · OpenAI</strong>
        <span>experimental · disabled for this MVP demo</span>
      </div>
    `;
    sourceList.appendChild(image);

    const mesh = document.createElement('div');
    mesh.className = 'source-item';
    mesh.innerHTML = `
      <div class="source-meta">
        <strong>External 3D Source · Trellis</strong>
        <span>experimental · disabled for this MVP demo</span>
      </div>
    `;
    sourceList.appendChild(mesh);
    return;
  }

  if (sourceStatusData) {
    const image = sourceStatusData.image || {};
    const mesh = sourceStatusData.object_3d || {};
    const anyRequested = Boolean(image.requested || mesh.requested);
    const failureMessage = sourceFailureMessage(sourceStatusData);
    if (sourceStatus) {
      if (!anyRequested) {
        sourceStatus.textContent = 'No external source asset requested';
      } else if (failureMessage) {
        sourceStatus.textContent = `External source requested but failed: ${failureMessage}`;
      } else {
        sourceStatus.textContent = `${sourceStatusLabel(image, 'Image Source')} / ${sourceStatusLabel(mesh, '3D Source')}`;
      }
    }
    if (!sourceList) return;
    sourceList.innerHTML = '';
    sourceList.appendChild(renderSourceStatusCard('image', image));
    sourceList.appendChild(renderSourceStatusCard('3d', mesh));
    if (Array.isArray(sourceStatusData.touchdesigner_errors) && sourceStatusData.touchdesigner_errors.length) {
      const error = document.createElement('div');
      error.className = 'source-empty';
      error.textContent = `TD load errors: ${sourceStatusData.touchdesigner_errors.map(e => e.message || e).join(' / ')}`;
      sourceList.appendChild(error);
    }
    return;
  }

  if (sourceStatus) {
    const appliedImage = tdApplied.asset_image_path;
    const applied3d = tdApplied.asset_3d_path;
    if (list.length) {
      sourceStatus.textContent = (appliedImage || applied3d)
        ? `${list.length} source asset(s) connected to TD`
        : `${list.length} source asset(s) generated`;
    } else {
      sourceStatus.textContent = 'No external source asset requested';
    }
  }

  if (!sourceList) return;
  sourceList.innerHTML = '';
  if (!list.length) {
    const empty = document.createElement('div');
    empty.className = 'source-empty';
    empty.textContent = 'This run is driven by procedural TouchDesigner nodes only.';
    sourceList.appendChild(empty);
    return;
  }

  list.forEach(asset => {
    const item = document.createElement('div');
    item.className = 'source-item';

    // ── Image thumbnail ───────────────────────────────────────────
    if (asset.kind === 'image' && (asset.url || asset.path)) {
      const imgSrc = asset.url
        ? `${API_BASE}${asset.url.startsWith('/') ? '' : '/'}${asset.url}?t=${Date.now()}`
        : '';
      if (imgSrc) {
        const img = document.createElement('img');
        img.className = 'source-thumb';
        img.src = imgSrc;
        img.alt = 'Generated source image';
        img.onerror = () => { img.style.display = 'none'; };
        item.appendChild(img);
      }
    }

    // ── 3-D asset badge ───────────────────────────────────────────
    if (asset.kind === '3d') {
      const badge = document.createElement('div');
      badge.className = 'source-3d-badge';
      badge.textContent = '🧊 3D Mesh';
      badge.style.cssText = 'padding:4px 10px;background:rgba(99,102,241,0.25);border:1px solid rgba(99,102,241,0.5);border-radius:6px;font-size:12px;color:#a5b4fc;display:inline-block;margin-bottom:6px;';
      item.appendChild(badge);
    }

    const meta = document.createElement('div');
    meta.className = 'source-meta';
    const title = document.createElement('strong');
    title.textContent = `${asset.kind || 'asset'} → ${asset.td_role || 'TouchDesigner input'}`;
    const path = document.createElement('span');
    path.style.cssText = 'display:block;font-size:11px;opacity:0.6;word-break:break-all;margin-top:2px;';
    path.textContent = asset.path || asset.url || 'No path returned';
    meta.appendChild(title);
    meta.appendChild(path);
    item.appendChild(meta);
    sourceList.appendChild(item);
  });
}

function renderResultInspector(result) {
  const plan = result.td_plan || result.orchestrator?.asset_plan?.td_mcp_plan || {};
  renderAgentPlan({
    template: result.parameters?.template || result.orchestrator?.template,
    parameters: result.parameters,
    td_plan: plan,
  });
  renderSourceAssets(result.source_assets || [], result.td_applied || {}, sourceStatusFromResult(result));
}

window.AutoTDDebug = {
  renderAgentPlan,
  renderSourceAssets,
  renderResultInspector,
  resetRuntimeInspector,
};

async function generateViaHttp(payload) {
  updateGenStep(1, 'Analyzing prompt...');

  const thoughtBox = document.getElementById('ai-thought-box');
  const thoughtContent = document.getElementById('thought-content');
  if (thoughtBox) thoughtBox.style.display = 'block';
  if (thoughtContent) thoughtContent.textContent = 'Ollama is selecting a procedural TouchDesigner recipe...';

  let result = null;
  try {
    const res = await fetch(`${API_BASE}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(360000),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Server error');
    }
    result = await res.json();
  } catch (err) {
    state.generating = false;
    closeGenModal();
    showPreviewMessage('Backend unavailable. TouchDesigner preview was not updated.');
    showToast(`Error: ${err.message}`, 'error');
    return;
  }

  if (result.reasoning) {
    if (thoughtContent) thoughtContent.textContent = 'Ollama selected a procedural recipe:';
    await typeThought(result.reasoning, 18);
  } else {
    if (thoughtContent) thoughtContent.textContent = 'Fallback recipe mode used local prompt heuristics.';
    await sleep(500);
  }

  updateGenStep(2, 'Selecting procedural recipe...');
  await sleep(350);
  updateGenStep(3, 'Tuning recipe parameters...');
  await sleep(350);
  updateGenStep(4, 'Running verified TouchDesigner chain...');
  await sleep(350);
  updateGenStep(5, 'Preparing preview...');
  await sleep(250);
  finishGeneration(result);
}

async function generateSourcePreview(payload) {
  showToast('External source generation is experimental in this MVP.', 'warn');
  return null;
}

async function loadTdPreview(previewUrl) {
  if (!previewImage || !previewUrl) {
    showPreviewMessage('TouchDesigner preview is unavailable.');
    return;
  }

  previewOverlay.classList.remove('hidden');
  showPreviewMessage('Loading TouchDesigner preview...');
  previewImage.hidden = true;
  previewImage.removeAttribute('src');

  const previewSrc = `${previewUrl}${previewUrl.includes('?') ? '&' : '?'}t=${Date.now()}`;
  previewImage.onload = () => {
    previewOverlay.classList.add('hidden');
    previewImage.hidden = false;
  };
  previewImage.onerror = () => {
    showPreviewMessage('TouchDesigner preview could not be loaded.');
    showToast('TouchDesigner preview unavailable', 'warn');
  };
  previewImage.src = previewSrc;
}

function finishGeneration(result) {
  state.generating = false;

  setTimeout(() => {
    closeGenModal();

    const params = result.parameters || result.params;
    state.lastParams = params;
    renderResultInspector(result);

    stopPreviewAnimation();

    if (result.td_status === 'sent' && result.preview_url) {
      loadTdPreview(result.preview_url);
      showToast('TouchDesigner preview updated', 'success');
    } else if (result.td_status === 'skipped') {
      showPreviewMessage('TouchDesigner is not connected.');
      showToast('TouchDesigner is not connected', 'warn');
    } else {
      showPreviewMessage('TouchDesigner preview is unavailable.');
      if (result.td_message) {
        showToast(`? ${result.td_message}`, 'warn');
      }
    }

    if (params) updateParamsDisplay(params);
  }, 600);
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  ?앹꽦 紐⑤떖 ?쒖뼱
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
const GEN_STEP_LABELS = [
  'Analyzing prompt...',
  'Selecting procedural recipe...',
  'Tuning recipe parameters...',
  'Running verified TouchDesigner chain...',
  'Preparing preview...',
];

let currentStep = 0;

function openGenModal() {
  currentStep = 0;
  genModal.classList.add('open');
  const steps = genModal.querySelectorAll('.gen-step');
  steps.forEach((s, i) => {
    s.classList.remove('active', 'done');
    s.textContent = GEN_STEP_LABELS[i];
  });
  steps[0].classList.add('active');

  // ?앷컖 諛뺤뒪 珥덇린 ?곹깭 由ъ뀑
  const thoughtBox = document.getElementById('ai-thought-box');
  const thoughtContent = document.getElementById('thought-content');
  if (thoughtBox) thoughtBox.style.display = 'none';
  if (thoughtContent) thoughtContent.textContent = 'AI가 시각 콘셉트를 설계하는 중...';
}

function closeGenModal() {
  genModal.classList.remove('open');
  // ?덉씠釉?由ъ뀑
  const steps = genModal.querySelectorAll('.gen-step');
  steps.forEach((s, i) => {
    s.classList.remove('active', 'done');
    s.textContent = GEN_STEP_LABELS[i];
  });
}

function updateGenStep(stepNum, message) {
  const steps = genModal.querySelectorAll('.gen-step');
  for (let i = 0; i < stepNum - 1; i++) {
    steps[i]?.classList.remove('active');
    steps[i]?.classList.add('done');
    if (steps[i]) steps[i].textContent = 'Done: ' + GEN_STEP_LABELS[i];
  }
  if (steps[stepNum - 1]) {
    steps[stepNum - 1].classList.remove('done');
    steps[stepNum - 1].classList.add('active');
    steps[stepNum - 1].textContent = message || GEN_STEP_LABELS[stepNum - 1];
  }
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  ?뚮씪誘명꽣 ?쒖떆 ?낅뜲?댄듃
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function updateParamsDisplay(params) {
  if (!params) return;
  // ?듭뀡 ?쒕∼?ㅼ슫 媛?諛섏쁺
  const styleEl     = document.getElementById('style-val');
  const motionEl    = document.getElementById('motion-val');
  const colorEl     = document.getElementById('color-val');

  const capitalize  = s => s.charAt(0).toUpperCase() + s.slice(1);

  if (styleEl && params.style)      styleEl.textContent = capitalize(params.style);
  if (motionEl && params.motion)    motionEl.textContent = capitalize(params.motion);
  if (colorEl && params.color_mode) colorEl.textContent = capitalize(params.color_mode);
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Export 踰꾪듉
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
['export-tox', 'export-mp4', 'export-gif'].forEach(id => {
  document.getElementById(id)?.addEventListener('click', () => {
    if (!state.lastParams) {
      showToast('먼저 미디어아트를 생성해주세요.', 'warn');
      return;
    }
    const labels = { 'export-tox': 'TOX', 'export-mp4': 'MP4', 'export-gif': 'GIF' };
    exportFile(labels[id]);
  });
});

async function exportFile(format) {
  if (state.tdConnected) {
    try {
      const res = await fetch(`${API_BASE}/api/td/preview`);
      if (res.ok) {
        showToast(`${format} export request completed`, 'success');
        return;
      }
    } catch {}
  }
  showToast(`${format} export ready (demo)`, 'success');
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Particle Canvas (Hero Background)
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initParticleCanvas() {
  const canvas = document.getElementById('particle-canvas');
  const ctx    = canvas.getContext('2d');
  let particles = [];
  let width, height;
  let mouse = { x: 0, y: 0 };

  function resize() {
    width = canvas.width  = window.innerWidth;
    height = canvas.height = window.innerHeight;
    mouse.x = width / 2;
    mouse.y = height / 2;
  }
  window.addEventListener('resize', () => { resize(); initP(); });
  window.addEventListener('mousemove', e => { mouse.x = e.clientX; mouse.y = e.clientY; });
  resize();

  class Particle {
    constructor() { this.reset(); }
    reset() {
      this.x = Math.random() * width;
      this.y = Math.random() * height;
      this.size   = Math.random() * 1.5 + 0.3;
      this.speedX = (Math.random() - 0.5) * 0.4;
      this.speedY = (Math.random() - 0.5) * 0.4;
      this.opacity  = Math.random() * 0.5 + 0.05;
      this.life     = 0;
      this.maxLife  = Math.random() * 400 + 200;
    }
    update() {
      this.x += this.speedX;
      this.y += this.speedY;
      this.life++;
      if (this.life > this.maxLife || this.x < -10 || this.x > width + 10 ||
          this.y < -10 || this.y > height + 10) this.reset();
      const r = this.life / this.maxLife;
      this.curOpacity = this.opacity * Math.sin(r * Math.PI);
    }
    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255,255,255,${this.curOpacity})`;
      ctx.fill();
    }
  }

  function initP() {
    particles = [];
    const n = Math.min(Math.floor((width * height) / 10000), 120);
    for (let i = 0; i < n; i++) particles.push(new Particle());
  }

  function drawLines() {
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const d  = Math.sqrt(dx * dx + dy * dy);
        if (d < 100) {
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.strokeStyle = `rgba(255,255,255,${(1 - d / 100) * 0.06})`;
          ctx.lineWidth   = 0.5;
          ctx.stroke();
        }
      }
    }
  }

  function animate() {
    ctx.clearRect(0, 0, width, height);
    // 留덉슦??二쇰? 湲濡쒖슦
    const grd = ctx.createRadialGradient(mouse.x, mouse.y, 0, mouse.x, mouse.y, 300);
    grd.addColorStop(0, 'rgba(255,255,255,0.015)');
    grd.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = grd; ctx.fillRect(0, 0, width, height);
    drawLines();
    particles.forEach(p => { p.update(); p.draw(); });
    requestAnimationFrame(animate);
  }

  initP();
  animate();
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Preview Canvas (?앹꽦 寃곌낵 ?쒓컖??
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
let previewAnimId = null;
let previewActive = false;
let pTime = 0;
let previewParticles = [];

function initPreviewCanvas() {
  if (previewImage) {
    previewImage.hidden = true;
    previewImage.removeAttribute('src');
  }
  showPreviewMessage('TouchDesigner preview will appear here');
}

function resizePreview() {}

function startPreviewAnimation(params, previewUrl) {
  return loadTdPreview(previewUrl);
}

function stopPreviewAnimation() {
  previewActive = false;
  if (previewAnimId) cancelAnimationFrame(previewAnimId);
  const c = document.getElementById('preview-canvas');
  if (c) {
    const ctx = c.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, c.width, c.height);
  }
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Navbar
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initNavbar() {
  const navbar = document.getElementById('navbar');
  window.addEventListener('scroll', () => {
    navbar.classList.toggle('scrolled', window.scrollY > 40);
  }, { passive: true });

  // ?쒖꽦 ?뱀뀡 ?섏씠?쇱씠??
  const sections  = document.querySelectorAll('section[id]');
  const navLinks  = document.querySelectorAll('.nav-link');
  window.addEventListener('scroll', () => {
    let cur = '';
    sections.forEach(s => { if (window.scrollY >= s.offsetTop - 100) cur = s.id; });
    navLinks.forEach(l => {
      l.style.color = l.getAttribute('href') === '#' + cur ? 'white' : '';
    });
  }, { passive: true });
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Hamburger Menu
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initHamburger() {
  const btn  = document.getElementById('hamburger');
  const menu = document.getElementById('mobile-menu');
  btn?.addEventListener('click', () => menu.classList.toggle('open'));
  menu?.querySelectorAll('.mobile-link').forEach(l =>
    l.addEventListener('click', () => menu.classList.remove('open'))
  );
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Char Counter
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initCharCounter() {
  const input   = document.getElementById('prompt-input');
  const counter = document.getElementById('char-count');
  input?.addEventListener('input', () => { counter.textContent = input.value.length; });
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Dropdown Options
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initDropdowns() {
  document.querySelectorAll('.option-item').forEach(item => {
    const dd = item.querySelector('.dropdown');
    if (!dd) return;
    item.addEventListener('click', e => {
      document.querySelectorAll('.dropdown.open').forEach(d => { if (d !== dd) d.classList.remove('open'); });
      dd.classList.toggle('open');
      e.stopPropagation();
    });
    dd.querySelectorAll('.dropdown-item').forEach(opt => {
      opt.addEventListener('click', e => {
        const el = document.getElementById(opt.dataset.target);
        if (el) el.textContent = opt.textContent;
        dd.querySelectorAll('.dropdown-item').forEach(o => o.classList.remove('active'));
        opt.classList.add('active');
        dd.classList.remove('open');
        e.stopPropagation();
      });
    });
  });
  document.addEventListener('click', () =>
    document.querySelectorAll('.dropdown.open').forEach(d => d.classList.remove('open'))
  );
}

function initRecipeSelector() {
  const cards = document.querySelectorAll('.recipe-card');
  const hiddenInput = document.getElementById('recipe-select');
  cards.forEach(card => {
    card.addEventListener('click', () => {
      cards.forEach(c => c.classList.remove('active'));
      card.classList.add('active');
      if (hiddenInput) {
        hiddenInput.value = card.dataset.value;
      }
    });
  });
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Scroll Animations
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initScrollAnimations() {
  const targets = document.querySelectorAll(
    '.problem-card, .feature-card, .week-card, .impact-point, .step-card, .tech-layer, .sys-node, .mvp-box'
  );
  targets.forEach(el => el.classList.add('fade-in-up'));
  const obs = new IntersectionObserver(entries =>
    entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('visible'); obs.unobserve(e.target); } }),
    { threshold: 0.1 }
  );
  targets.forEach(el => obs.observe(el));
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Smooth Scroll
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
      const t = document.querySelector(a.getAttribute('href'));
      if (t) { e.preventDefault(); t.scrollIntoView({ behavior: 'smooth' }); }
    });
  });
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  Toast ?뚮┝
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function initToastStyle() {
  const s = document.createElement('style');
  s.textContent = `
    @keyframes slideIn { from { transform:translateX(20px); opacity:0; } to { transform:translateX(0); opacity:1; } }
    .toast { position:fixed; bottom:32px; right:32px; padding:12px 20px; border-radius:10px;
             font-size:13px; font-weight:600; z-index:3000; animation:slideIn .3s ease;
             transition:opacity .3s; max-width:320px; box-shadow:0 8px 32px rgba(0,0,0,.3); }
    .toast-success { background:#fff; color:#0a0a0a; }
    .toast-warn    { background:#1a1a1a; color:#fbbf24; border:1px solid rgba(251,191,36,.3); }
    .toast-info    { background:#1a1a1a; color:rgba(255,255,255,.7); border:1px solid rgba(255,255,255,.1); }
  `;
  document.head.appendChild(s);
}

function showToast(msg, type = 'success') {
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3000);
}

// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
//  ?좏떥
// ?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function shakeElement(el) {
  el.style.transition = 'transform .1s';
  const times = [0, 6, -6, 4, -4, 0];
  times.forEach((x, i) => setTimeout(() => { el.style.transform = `translateX(${x}px)`; }, i * 60));
  setTimeout(() => { el.style.transform = ''; el.style.borderColor = 'rgba(255,100,100,0.4)'; }, 360);
  setTimeout(() => { el.style.borderColor = ''; }, 1800);
}

console.log('AutoTD Web v2.1 initialized. API:', API_BASE, '| Operator family auto-detect active');


// 2D MVP UI overrides: Ollama + optional OpenAI image source + TouchDesigner recipes.
function renderAgentPlan(data) {
  const plan = data.td_plan || data.asset_plan?.td_mcp_plan || {};
  const params = data.parameters || data.params || {};
  const recipeId = params.recipe_id || plan.recipe_id || data.recipe_id || 'pending';
  const count = Array.isArray(plan.nodes) ? plan.nodes.length : 0;
  if (tdPlanStatus) {
    tdPlanStatus.textContent = count ? `${recipeId} - ${count} TD nodes planned` : `${recipeId} - recipe selected`;
  }
  if (tdPlanText) {
    tdPlanText.textContent = JSON.stringify({
      selected_recipe: recipeId,
      concept_summary: params.concept_summary || plan.intent || '',
      visual_mood: params.visual_mood || '',
      openai_image: params.use_comfyui_image ? 'requested' : 'skipped',
      image_usage: params.image_usage || '',
      generated_parameters: {
        speed: params.speed,
        noise_strength: params.noise_strength,
        feedback_opacity: params.feedback_opacity,
        blur_amount: params.blur_amount,
        displace_weight: params.displace_weight,
        glow_intensity: params.glow_intensity || params.glow || params.brightness,
        particle_count: params.particle_count,
        spread: params.spread,
        color_1: params.color_1,
        color_2: params.color_2,
        color_3: params.color_3,
      },
      planned_td_nodes: plan.nodes || [],
      notes: plan.notes || '',
    }, null, 2);
  }
}

function renderSourceStatusCard(kind, entry) {
  const item = document.createElement('div');
  const status = entry?.status || 'not_requested';
  item.className = `source-item ${status === 'loaded' ? 'loaded' : ''} ${status === 'failed' ? 'failed' : ''}`;

  const meta = document.createElement('div');
  meta.className = 'source-meta';
  const title = document.createElement('strong');
  title.textContent = kind === 'image' ? 'OpenAI 2D Source' : 'External 3D Source';
  const path = document.createElement('span');
  path.textContent = entry?.path || entry?.obj_path || entry?.glb_path || entry?.error || (kind === 'image' ? 'skipped' : 'disabled for this MVP');

  const facts = document.createElement('div');
  facts.className = 'source-facts';
  appendFact(facts, 'status', kind === '3d' ? 'disabled' : status);
  appendFact(facts, 'loaded', entry?.loaded === true ? 'true' : entry?.loaded === false ? 'false' : '');
  if (kind === 'image') {
    appendFact(facts, 'size', entry?.width && entry?.height ? `${entry.width}x${entry.height}` : '');
    appendFact(facts, 'usage', entry?.usage);
  } else {
    appendFact(facts, 'points', entry?.point_count);
    appendFact(facts, 'prims', entry?.primitive_count);
  }
  meta.appendChild(title);
  meta.appendChild(path);
  meta.appendChild(facts);
  item.appendChild(meta);
  return item;
}

function renderSourceAssets(assets, tdApplied = {}, sourceStatusData = null) {
  const image = sourceStatusData?.image || {};
  const mesh = sourceStatusData?.object_3d || { requested: false, status: 'disabled', loaded: false };
  const imageRequested = Boolean(image.requested);
  const failureMessage = sourceFailureMessage(sourceStatusData);
  const imageState = imageRequested ? sourceStatusLabel(image, 'OpenAI Image Generation') : 'OpenAI Image Generation: skipped';

  if (sourceStatus) {
    sourceStatus.textContent = failureMessage
      ? `External source requested but failed: ${failureMessage}`
      : `Ollama Orchestration: active / ${imageState} / TouchDesigner MCP Control: active`;
  }
  if (!sourceList) return;
  sourceList.innerHTML = '';

  const runtime = document.createElement('div');
  runtime.className = 'source-item loaded';
  runtime.innerHTML = `
    <div class="source-meta">
      <strong>Ollama Orchestration</strong>
      <span>active - recipe_id and parameters only</span>
      <div class="source-facts">
        <span>recipes: dreamy_particle_field / glitch_feedback_field / soft_3d_orb</span>
        <span>3D/Trellis: experimental</span>
      </div>
    </div>
  `;
  sourceList.appendChild(runtime);
  sourceList.appendChild(renderSourceStatusCard('image', image));
  sourceList.appendChild(renderSourceStatusCard('3d', mesh));

  const td = document.createElement('div');
  td.className = 'source-item loaded';
  const particleEngine = tdApplied.particle_engine ? `particle engine: ${tdApplied.particle_engine}` : 'recipe engine: feedback/POP particle';
  const particleTrail = tdApplied.trail_enabled ? 'POP trails: enabled' : 'POP trails: recipe-dependent';
  const fallbackText = tdApplied.fallback_used ? `fallback: ${tdApplied.fallback_reason || 'used'}` : 'fallback: off';
  td.innerHTML = `
    <div class="source-meta">
      <strong>TouchDesigner MCP Control</strong>
      <span>active - verified recipe / TouchDesigner MCP control</span>
      <div class="source-facts">
        <span>preview: ${tdApplied.final_output_top || tdApplied.out1 || 'out1'}</span>
        <span>${particleEngine}</span>
        <span>${particleTrail}</span>
        <span>${fallbackText}</span>
      </div>
    </div>
  `;
  sourceList.appendChild(td);
}

if (window.AutoTDDebug) {
  window.AutoTDDebug.renderAgentPlan = renderAgentPlan;
  window.AutoTDDebug.renderSourceAssets = renderSourceAssets;
}



