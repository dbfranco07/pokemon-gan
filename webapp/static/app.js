"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  seed: $("seed"), dice: $("dice"), steps: $("steps"), stepsVal: $("stepsVal"),
  generate: $("generate"), save: $("save"), status: $("status"),
  sprite: $("sprite"), placeholder: $("placeholder"), spinner: $("spinner"),
  stage: $("stage"), meta: $("meta"),
  zoomIn: $("zoomIn"), zoomOut: $("zoomOut"), fit: $("fit"), actual: $("actual"),
  tabCurrent: $("tabCurrent"), tabSaved: $("tabSaved"),
  viewerPane: $("viewerPane"), savedPane: $("savedPane"),
  gallery: $("gallery"), galleryEmpty: $("galleryEmpty"),
};

// Current sprite state (image bytes + the params that made it).
let current = null;   // { image, seed, steps }
let nativeSize = 64;  // updated from /api/info

// ---------- pan / zoom viewer ----------
const view = { scale: 8, x: 0, y: 0 };

function applyTransform() {
  els.sprite.style.transform =
    `translate(-50%, -50%) translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
}
function setScale(s) {
  view.scale = Math.max(0.5, Math.min(40, s));
  applyTransform();
}
function fitView() {
  const pad = 40;
  const r = els.stage.getBoundingClientRect();
  const s = Math.max(1, Math.floor(Math.min(r.width, r.height) - pad) / nativeSize);
  view.x = 0; view.y = 0; setScale(s);
}

els.zoomIn.onclick = () => setScale(view.scale * 1.25);
els.zoomOut.onclick = () => setScale(view.scale / 1.25);
els.actual.onclick = () => { view.x = 0; view.y = 0; setScale(1); };
els.fit.onclick = fitView;

els.stage.addEventListener("wheel", (e) => {
  e.preventDefault();
  setScale(view.scale * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
}, { passive: false });

let dragging = false, lastX = 0, lastY = 0;
els.stage.addEventListener("pointerdown", (e) => {
  dragging = true; lastX = e.clientX; lastY = e.clientY;
  els.stage.setPointerCapture(e.pointerId);
});
els.stage.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  view.x += e.clientX - lastX; view.y += e.clientY - lastY;
  lastX = e.clientX; lastY = e.clientY; applyTransform();
});
els.stage.addEventListener("pointerup", () => { dragging = false; });

// ---------- helpers ----------
function showSpinner(on) {
  els.spinner.classList.toggle("hidden", !on);
  els.generate.disabled = on;
}
function showSprite(dataUrl, seed, steps) {
  els.sprite.src = dataUrl;
  els.sprite.classList.remove("hidden");
  els.placeholder.classList.add("hidden");
  els.meta.textContent = `seed ${seed} · ${steps} steps · ${nativeSize}px`;
}

// ---------- API actions ----------
async function generate() {
  showSpinner(true);
  try {
    const seedRaw = els.seed.value.trim();
    const body = {
      steps: parseInt(els.steps.value, 10),
      seed: seedRaw === "" ? null : parseInt(seedRaw, 10),
    };
    const res = await fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    current = data;
    els.seed.value = data.seed;          // echo the seed actually used
    showSprite(data.image, data.seed, data.steps);
    els.save.disabled = false;
    if (els.sprite.classList.contains("hidden") === false && view.scale === 8) fitView();
  } catch (err) {
    els.status.innerHTML = `<b>Generate failed:</b> ${String(err).slice(0, 200)}`;
  } finally {
    showSpinner(false);
  }
}

async function save() {
  if (!current) return;
  els.save.disabled = true;
  try {
    const res = await fetch("/api/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(current),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    els.save.textContent = "Saved ✓";
    setTimeout(() => { els.save.textContent = "Save sprite"; els.save.disabled = false; }, 1200);
  } catch (err) {
    els.status.innerHTML = `<b>Save failed:</b> ${String(err).slice(0, 200)}`;
    els.save.disabled = false;
  }
}

async function loadGallery() {
  const res = await fetch("/api/saved");
  const items = await res.json();
  els.gallery.innerHTML = "";
  els.galleryEmpty.classList.toggle("hidden", items.length > 0);
  for (const it of items) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      `<img class="thumb" src="${it.url}" alt="${it.filename}" />` +
      `<div class="cap">seed ${it.seed ?? "?"} · ${it.steps ?? "?"} steps</div>`;
    card.onclick = () => {
      current = { image: it.url, seed: it.seed, steps: it.steps };
      showSprite(it.url, it.seed ?? "?", it.steps ?? "?");
      els.save.disabled = true;        // already saved
      switchTab("current");
      fitView();
    };
    els.gallery.appendChild(card);
  }
}

// ---------- tabs ----------
function switchTab(which) {
  const cur = which === "current";
  els.tabCurrent.classList.toggle("active", cur);
  els.tabSaved.classList.toggle("active", !cur);
  els.viewerPane.classList.toggle("hidden", !cur);
  els.savedPane.classList.toggle("hidden", cur);
  if (!cur) loadGallery();
}
els.tabCurrent.onclick = () => switchTab("current");
els.tabSaved.onclick = () => switchTab("saved");

// ---------- wiring ----------
els.steps.oninput = () => { els.stepsVal.textContent = els.steps.value; };
els.dice.onclick = () => { els.seed.value = ""; els.seed.focus(); };
els.generate.onclick = generate;
els.save.onclick = save;
els.seed.addEventListener("keydown", (e) => { if (e.key === "Enter") generate(); });

async function init() {
  try {
    const info = await (await fetch("/api/info")).json();
    nativeSize = info.image_size || 64;
    els.status.innerHTML =
      `Device <b>${info.device}</b><br>${info.checkpoint} · epoch ${info.epoch}`;
  } catch {
    els.status.innerHTML = "<b>Backend unreachable.</b>";
  }
  applyTransform();
}
init();
