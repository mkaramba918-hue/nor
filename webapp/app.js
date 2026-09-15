const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

let selectedSlot = null;

const members = [
  { slot: 1, role: "Положенец [9]" },
  { slot: 2, role: "Положенец [9]" },
  { slot: 3, role: "Положенец [9]" },
  { slot: 4, role: "Смотрящий [8]" },
  { slot: 5, role: "Смотрящий [8]" },
  { slot: 6, role: "Смотрящий [8]" },
  { slot: 7, role: "Смотрящий [8]" }
];

function renderList() {
  const container = document.getElementById("members-list");
  container.innerHTML = "";

  members.forEach((m) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="info">
        <div class="role">${m.role}</div>
        <div class="nick">Слот #${m.slot}</div>
      </div>
      <button class="status-btn" onclick="openModal(${m.slot})">Отметка</button>
    `;
    container.appendChild(card);
  });
}

function openModal(slot) {
  selectedSlot = slot;
  document.getElementById("modal").style.display = "flex";
}

function closeModal() {
  document.getElementById("modal").style.display = "none";
}

function sendAction(statusKey) {
  if (!selectedSlot) return;

  const payload = JSON.stringify({
    slot: selectedSlot,
    status: statusKey
  });

  // Отправляет данные обратно боту в чат
  tg.sendData(payload);
  tg.close();
}

renderList();
