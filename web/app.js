// ==== CONFIG ====
const API_BASE = "https://YOUR_DOMAIN_OR_IP:8000"; // <-- поменяй на свой backend URL (или http://IP:8000)
const DEFAULT_CENTER = [41.3111, 69.2797]; // Ташкент

// ==== Telegram WebApp helpers ====
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.expand();
}

// ==== Map init ====
const map = L.map("map", { zoomControl: true }).setView(DEFAULT_CENTER, 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap",
}).addTo(map);

let cursorMarker = null;
let myPosMarker = null;

const statusEl = document.getElementById("status");
const noteEl = document.getElementById("note");
const btnMark = document.getElementById("btnMark");
const btnMyLoc = document.getElementById("btnMyLoc");

function setStatus(msg) {
  statusEl.textContent = msg;
}

function setCursor(lat, lon) {
  if (cursorMarker) map.removeLayer(cursorMarker);
  cursorMarker = L.marker([lat, lon]).addTo(map).bindPopup("Выбранная точка").openPopup();
}

map.on("click", (e) => {
  setCursor(e.latlng.lat, e.latlng.lng);
  setStatus(`Выбрано: ${e.latlng.lat.toFixed(6)}, ${e.latlng.lng.toFixed(6)}`);
});

async function loadApprovedPoints() {
  try {
    const r = await fetch(`${API_BASE}/trashpoints?status=approved`);
    const data = await r.json();
    data.items.forEach((p) => {
      const m = L.circleMarker([p.lat, p.lon], { radius: 6 });
      const note = p.note ? `<br><b>Комментарий:</b> ${escapeHtml(p.note)}` : "";
      m.bindPopup(`<b>Точка</b>${note}`).addTo(map);
    });
    setStatus("Загружены точки.");
  } catch (e) {
    setStatus("Не удалось загрузить точки.");
  }
}

function escapeHtml(str) {
  return (str || "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[m]);
}

async function sendPoint(lat, lon, note) {
  // Telegram user id (если открыто из бота)
  const tgUserId = tg?.initDataUnsafe?.user?.id || null;

  const payload = {
    user_tid: tgUserId, // может быть null если открыть не из Telegram
    lat: lat,
    lon: lon,
    note: note || "",
    // initData полезно для проверки подписи (можно включить позже)
    init_data: tg ? tg.initData : null
  };

  const r = await fetch(`${API_BASE}/trashpoints`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || "Request failed");
  }
  return await r.json();
}

btnMyLoc.addEventListener("click", () => {
  if (!navigator.geolocation) {
    setStatus("Геолокация не поддерживается.");
    return;
  }
  setStatus("Определяю местоположение…");
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;

      if (myPosMarker) map.removeLayer(myPosMarker);
      myPosMarker = L.marker([lat, lon]).addTo(map).bindPopup("Вы здесь").openPopup();

      map.setView([lat, lon], 16);
      setCursor(lat, lon);
      setStatus(`Моё местоположение: ${lat.toFixed(6)}, ${lon.toFixed(6)}`);
    },
    () => setStatus("Не удалось получить геолокацию."),
    { enableHighAccuracy: true, timeout: 10000 }
  );
});

btnMark.addEventListener("click", async () => {
  if (!cursorMarker) {
    setStatus("Нажми на карту, чтобы выбрать точку.");
    return;
  }
  const ll = cursorMarker.getLatLng();
  const note = noteEl.value.trim();

  try {
    setStatus("Отправляю…");
    await sendPoint(ll.lat, ll.lng, note);
    setStatus("✅ Отправлено на проверку администратору.");
    noteEl.value = "";

    // Закрыть WebApp (если внутри Telegram)
    if (tg) tg.close();
  } catch (e) {
    setStatus("❌ Ошибка отправки.");
  }
});

// start
loadApprovedPoints();