const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

const user = tg.initDataUnsafe?.user;
const userBadge = document.getElementById("user-badge");
if (user) {
  userBadge.innerText = `В сети: ${user.first_name || "Админ"} (ID: ${user.id})`;
} else {
  userBadge.innerText = "Режим управления";
}

let currentSlot = null;

const staffSlots = [
  { slot: 1, role: "Положенец [9]" },
  { slot: 2, role: "Положенец [9]" },
  { slot: 3, role: "Положенец [9]" },
  { slot: 4, role: "Смотрящий [8]" },
  { slot: 5, role: "Смотрящий [8]" },
  { slot: 6, role: "Смотрящий [8]" },
  { slot: 7, role: "Смотрящий [8]" }
];

function switchTab(tabId, btn) {
  document.querySelectorAll(".tab-content").forEach(el => el.classList.add("hidden"));
  document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));
  document.getElementById(tabId).classList.remove("hidden");
  btn.classList.add("active");
}

function renderStaff() {
  const nContainer = document.getElementById("staff-container");
  const pContainer = document.getElementById("punish-container");
  nContainer.innerHTML = "";
  pContainer.innerHTML = "";

  staffSlots.forEach(s => {
    // Карточка для нормы
    const nCard = document.createElement("div");
    nCard.className = "staff-card";
    nCard.innerHTML = `
      <div class="staff-info">
        <div class="role">${s.role}</div>
        <div class="slot">Слот #${s.slot}</div>
      </div>
      <button class="card-btn btn-blue" onclick="openNormaModal(${s.slot})">Отметка</button>
    `;
    nContainer.appendChild(nCard);

    // Карточка для наказаний
    const pCard = document.createElement("div");
    pCard.className = "staff-card";
    pCard.innerHTML = `
      <div class="staff-info">
        <div class="role">${s.role}</div>
        <div class="slot">Слот #${s.slot}</div>
      </div>
      <div class="staff-actions">
        <button class="card-btn" style="background:#e74c3c" onclick="sendAction({action:'punish', type:'warn', slot:${s.slot}})">+Выг</button>
        <button class="card-btn btn-dark" onclick="sendAction({action:'punish', type:'unwarn', slot:${s.slot}})">-Выг</button>
        <button class="card-btn" style="background:#f39c12" onclick="sendAction({action:'punish', type:'pred', slot:${s.slot}})">+Пред</button>
        <button class="card-btn btn-dark" onclick="sendAction({action:'punish', type:'unpred', slot:${s.slot}})">-Пред</button>
      </div>
    `;
    pContainer.appendChild(pCard);
  });
}

function openNormaModal(slot) {
  currentSlot = slot;
  document.getElementById("modal-slot-title").innerText = `Отметка для Слота #${slot}`;
  document.getElementById("norma-modal").style.display = "flex";
}

function closeNormaModal() {
  document.getElementById("norma-modal").style.display = "none";
}

function submitNorma(statusKey) {
  if (!currentSlot) return;
  sendAction({ action: "norma", slot: currentSlot, status: statusKey });
  closeNormaModal();
}

function submitNick() {
  const slot = parseInt(document.getElementById("setnick-slot").value);
  const nick = document.getElementById("setnick-name").value.trim();
  if (!nick) return alert("Введите ник!");
  sendAction({ action: "setnick", slot, nick });
}

function submitNeaktiv() {
  const slot = parseInt(document.getElementById("neaktiv-slot").value);
  const until = document.getElementById("neaktiv-until").value.trim();
  if (!until) return alert("Укажите дату!");
  sendAction({ action: "neaktiv", slot, until });
}

function submitAnnounce() {
  const text = document.getElementById("announce-text").value.trim();
  if (!text) return alert("Введите текст!");
  sendAction({ action: "announce", text });
}

function sendAction(payload) {
  try {
    tg.sendData(JSON.stringify(payload));
    tg.close();
  } catch (err) {
    alert("Ошибка отправки данных боту: " + err);
  }
}

renderStaff();
