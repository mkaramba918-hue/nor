const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

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

// Проверка сохранённого логина в localStorage
window.addEventListener("DOMContentLoaded", () => {
  const savedUser = localStorage.getItem("aopg_auth_user");
  if (savedUser) {
    showMainScreen(savedUser);
  }
});

function performLogin() {
  const loginVal = document.getElementById("login-input").value.trim();
  const pwdVal = document.getElementById("password-input").value.trim();

  if (!loginVal || !pwdVal) {
    alert("Заполните логин и пароль!");
    return;
  }

  // Сохраняем локально и передаем событие боту
  localStorage.setItem("aopg_auth_user", loginVal);
  localStorage.setItem("aopg_auth_pwd", pwdVal);

  const payload = {
    action: "login",
    login: loginVal,
    password: pwdVal
  };
  tg.sendData(JSON.stringify(payload));
  tg.close();
}

function showMainScreen(user) {
  document.getElementById("auth-screen").classList.add("hidden");
  document.getElementById("main-screen").classList.remove("hidden");
  document.getElementById("user-role-label").innerText = user;

  renderStaffNormaList();
  renderStaffPunishList();
}

function switchTab(tabId) {
  document.querySelectorAll(".tab-content").forEach(el => el.classList.add("hidden"));
  document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));

  document.getElementById(tabId).classList.remove("hidden");
  event.target.classList.add("active");
}

function renderStaffNormaList() {
  const container = document.getElementById("staff-container");
  container.innerHTML = "";

  staffSlots.forEach(s => {
    const card = document.createElement("div");
    card.className = "staff-card";
    card.innerHTML = `
      <div class="staff-info">
        <div class="role">${s.role}</div>
        <div class="slot">Слот #${s.slot}</div>
      </div>
      <div class="staff-actions">
        <button class="card-btn btn-blue" onclick="openNormaModal(${s.slot})">Отметка</button>
      </div>
    `;
    container.appendChild(card);
  });
}

function renderStaffPunishList() {
  const container = document.getElementById("punish-container");
  container.innerHTML = "";

  staffSlots.forEach(s => {
    const card = document.createElement("div");
    card.className = "staff-card";
    card.innerHTML = `
      <div class="staff-info">
        <div class="role">${s.role}</div>
        <div class="slot">Слот #${s.slot}</div>
      </div>
      <div class="staff-actions">
        <button class="card-btn" style="background:#e74c3c" onclick="sendPunish('warn', ${s.slot})">+Выг</button>
        <button class="card-btn btn-dark" onclick="sendPunish('unwarn', ${s.slot})">-Выг</button>
        <button class="card-btn" style="background:#f39c12" onclick="sendPunish('pred', ${s.slot})">+Пред</button>
        <button class="card-btn btn-dark" onclick="sendPunish('unpred', ${s.slot})">-Пред</button>
      </div>
    `;
    container.appendChild(card);
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

function sendNormaStatus(statusKey) {
  if (!currentSlot) return;
  const payload = {
    action: "norma",
    slot: currentSlot,
    status: statusKey
  };
  tg.sendData(JSON.stringify(payload));
  tg.close();
}

function sendFastAllNorma() {
  tg.sendData(JSON.stringify({ action: "all_norma" }));
  tg.close();
}

function requestSummary() {
  tg.sendData(JSON.stringify({ action: "summary" }));
  tg.close();
}

function sendPunish(type, slot) {
  const payload = {
    action: "punish",
    type: type,
    slot: slot
  };
  tg.sendData(JSON.stringify(payload));
  tg.close();
}

function sendSetNick() {
  const slot = document.getElementById("setnick-slot").value;
  const nick = document.getElementById("setnick-name").value.trim();
  if (!nick) {
    alert("Введите никнейм!");
    return;
  }
  const payload = {
    action: "setnick",
    slot: parseInt(slot),
    nick: nick
  };
  tg.sendData(JSON.stringify(payload));
  tg.close();
}

function sendNeaktiv() {
  const slot = document.getElementById("neaktiv-slot").value;
  const until = document.getElementById("neaktiv-until").value.trim();
  if (!until) {
    alert("Укажите дату!");
    return;
  }
  const payload = {
    action: "neaktiv",
    slot: parseInt(slot),
    until: until
  };
  tg.sendData(JSON.stringify(payload));
  tg.close();
}

function sendAnnounce() {
  const text = document.getElementById("announce-text").value.trim();
  if (!text) {
    alert("Введите текст анонса!");
    return;
  }
  const payload = {
    action: "announce",
    text: text
  };
  tg.sendData(JSON.stringify(payload));
  tg.close();
}
