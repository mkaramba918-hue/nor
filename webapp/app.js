const tg = window.Telegram?.WebApp;
try {
  tg?.expand();
  tg?.ready();
} catch (e) {
  console.log("TG init error", e);
}

let selectedFile = null;
let currentDownloadUrl = null;

const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const compileBtn = document.getElementById("compile-btn");
const resultCard = document.getElementById("result-card");
const statusBadge = document.getElementById("status-badge");
const compilerLog = document.getElementById("compiler-log");
const downloadBox = document.getElementById("download-box");

fileInput.addEventListener("change", (e) => {
  if (e.target.files && e.target.files[0]) {
    handleFile(e.target.files[0]);
  }
});

function handleFile(file) {
  selectedFile = file;
  const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
  
  document.getElementById("drop-text").classList.add("hidden");
  const nameEl = document.getElementById("file-name");
  const sizeEl = document.getElementById("file-size");
  
  nameEl.innerText = file.name;
  nameEl.classList.remove("hidden");
  
  sizeEl.innerText = `${sizeMb} МБ`;
  sizeEl.classList.remove("hidden");

  document.getElementById("file-icon").innerText = file.name.endsWith(".zip") ? "📦" : "📄";
  dropzone.classList.add("active");
  compileBtn.disabled = false;
  
  if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
}

async function startCompilation() {
  if (!selectedFile) return;

  compileBtn.disabled = true;
  compileBtn.innerText = "⏳ Идет сборка...";
  resultCard.classList.remove("hidden");
  downloadBox.classList.add("hidden");
  
  statusBadge.className = "status-badge st-loading";
  statusBadge.innerText = "⚙️ Компиляция 60 000+ строк...";
  compilerLog.innerText = "Загрузка файла на сервер и подготовка инклудов...\n";

  const formData = new FormData();
  formData.append("file", selectedFile);
  formData.append("db_host", document.getElementById("db-host").value.trim());
  formData.append("db_user", document.getElementById("db-user").value.trim());
  formData.append("db_name", document.getElementById("db-name").value.trim());
  formData.append("db_pass", document.getElementById("db-pass").value.trim());

  try {
    const response = await fetch("/api/compile", {
      method: "POST",
      body: formData
    });

    const res = await response.json();

    compileBtn.disabled = false;
    compileBtn.innerText = "🚀 Скомпилировать мод";

    if (res.success) {
      statusBadge.className = "status-badge st-success";
      statusBadge.innerText = `✅ Успешно за ${res.elapsed} сек (${res.amx_size} МБ)`;
      currentDownloadUrl = res.download_url;
      downloadBox.classList.remove("hidden");

      let fixesText = "";
      if (res.fixes && res.fixes.length > 0) {
        fixesText = "=== АВТОИСПРАВЛЕНИЯ ===\n" + res.fixes.map(f => "✔ " + f).join("\n") + "\n\n";
      }
      compilerLog.innerText = fixesText + (res.log || "Компиляция завершена без замечаний.");

      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } else {
      statusBadge.className = "status-badge st-error";
      statusBadge.innerText = `❌ Ошибка сборки (код: ${res.returncode})`;
      compilerLog.innerText = (res.fixes ? res.fixes.join("\n") + "\n\n" : "") + (res.log || "Процесс завершился с ошибкой.");

      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
    }
  } catch (err) {
    compileBtn.disabled = false;
    compileBtn.innerText = "🚀 Скомпилировать мод";
    statusBadge.className = "status-badge st-error";
    statusBadge.innerText = "❌ Ошибка соединения с сервером";
    compilerLog.innerText = String(err);
  }
}

function downloadResult() {
  if (!currentDownloadUrl) return;
  // Открытие скачивания через встроенный метод Telegram или браузер
  if (tg?.openLink) {
    tg.openLink(window.location.origin + currentDownloadUrl);
  } else {
    window.location.href = currentDownloadUrl;
  }
}
