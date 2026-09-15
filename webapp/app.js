const tg = window.Telegram.WebApp;
try {
  tg.expand();
  tg.ready();
} catch (e) {
  console.log("TG init error", e);
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

window.addEventListener("DOMContentLoaded", () => {
  const savedUser = localStorage.getItem("aopg_auth_user");
  if (savedUser) {
    showMainScreen(savedUser);
  } else {
    showAuthScreen();
  }
});

function showAuthScreen() {
  const auth = document.getElementById("auth-screen");
  const main = document.getElementById("main-screen");
  if (auth) auth.classList.remove("hidden");
  if (main) main.classList.add("hidden");
}

function showMainScreen(user) {
  const auth = document.getElementById("auth-screen");
  const main = document.getElementById("main-screen");
  if (auth) auth.classList.add("hidden");
  if (main) main.classList.remove("hidden");
  
  const badge = document.getElementById("user-badge");
  if (badge) badge.innerText = `Авторизован: ${user}`;
  
  renderStaff();
}

function performLogin() {
  const loginInput = document.getElementById("login-input");
  const pwdInput = document.getElementById("password-input");
  
  const login = loginInput ? loginInput.value.trim() : "";
  const pwd = pwdInput ? pwdInput.value.trim() : "";

  if (!login || !pwd) {
    alert("Заполните логин и пароль!");
    return;
  }

  // Сразу сохраняем локально и открываем панель
  localStorage.setItem("aopg_auth_user", login);
  localStorage.setItem("aopg_auth_pwd", pwd);
  
  showMainScreen(login);

  // Пробуем передать боту сессию
  sendActionSilently({ action: "login", login: login, password: pwd });
}

function logout() {
  localStorage.removeItem("aopg_auth_user");
  localStorage.removeItem("aopg_auth_pwd");
  showAuthScreen();
}

function switchTab(tabId, btn) {
  document.querySelectorAll(".tab-content").forEach(el => el.classList.add("hidden"));
  document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));
  const target = document.getElementById(tabId);
  if (target) target.classList.remove("hidden");
  if (btn) btn.classList.add("active");
}

function renderStaff() {
  const nContainer = document.getElementById("staff-container");
  const pContainer = document.getElementById("punish-container");
  if (!nContainer || !pContainer) return;

  nContainer.innerHTML = "";
  pContainer.innerHTML = "";

  staffSlots.forEach(s => {
    // Норма
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

    // Наказания
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
  const title = document.getElementById("modal-slot-title");
  if (title) title.innerText = `Отметка для Слота #${slot}`;
  const modal = document.getElementById("norma-modal");
  if (modal) modal.style.display = "flex";
}

function closeNormaModal() {
  const modal = document.getElementById("norma-modal");
  if (modal) modal.style.display = "none";
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
    if (tg && tg.sendData) {
      tg.sendData(JSON.stringify(payload));
      tg.close();
    } else {
      alert("Telegram WebApp API недоступен. Откройте через кнопку в сообщении бота.");
    }
  } catch (err) {
    alert("Ошибка отправки: " + err.message);
  }
}

function sendActionSilently(payload) {
  try {
    if (tg && tg.sendData) {
      tg.sendData(JSON.stringify(payload));
    }
  } catch (err) {
    console.log("Silent send error:", err);
  }
}
