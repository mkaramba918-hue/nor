const tg = window.Telegram?.WebApp;
try {
  tg?.expand();
  tg?.ready();
} catch (e) {
  console.log("TG init error", e);
}

// Актуальный домен Railway
const RAILWAY_FALLBACK = "https://nor-production-674b.up.railway.app";

const RAW_BACKEND_URL = window.location.origin.includes("github.io") 
  ? RAILWAY_FALLBACK 
  : window.location.origin;

// Защита от двойных слешей в URL
const BACKEND_URL = RAW_BACKEND_URL.replace(/\/+$/, "");

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
  statusBadge.innerText = "⚙️ Компиляция 60 000+ строк (Wine)...";
  compilerLog.innerText = "Отправка архива на сервер...\n";

  const formData = new FormData();
  formData.append("file", selectedFile);
  formData.append("db_host", document.getElementById("db-host") ? document.getElementById("db-host").value.trim() : "127.0.0.1");
  formData.append("db_user", document.getElementById("db-user") ? document.getElementById("db-user").value.trim() : "user909028");
  formData.append("db_name", document.getElementById("db-name") ? document.getElementById("db-name").value.trim() : "user909028");
  formData.append("db_pass", document.getElementById("db-pass") ? document.getElementById("db-pass").value.trim() : "FpUjJoAu2gVD");

  // Передача Telegram ID и имени пользователя для синхронизации с командами /log и /logs
  if (tg?.initDataUnsafe?.user?.id) {
    formData.append("user_id", tg.initDataUnsafe.user.id);
    formData.append("user_name", tg.initDataUnsafe.user.username || tg.initDataUnsafe.user.first_name || "Пользователь");
  }

  try {
    const response = await fetch(`${BACKEND_URL}/api/compile`, {
      method: "POST",
      body: formData
    });

    const rawText = await response.text();
    let res;

    try {
      res = JSON.parse(rawText);
    } catch (parseErr) {
      throw new Error(`Ошибка сервера (${response.status}):\n` + rawText.replace(/<[^>]*>?/gm, '').slice(0, 300));
    }

    compileBtn.disabled = false;
    compileBtn.innerText = "🚀 Скомпилировать мод";

    if (res.success) {
      statusBadge.className = "status-badge st-success";
      statusBadge.innerText = `✅ Успешно за ${res.elapsed} сек (${res.amx_size} МБ)`;
      currentDownloadUrl = `${BACKEND_URL}${res.download_url}`;
      downloadBox.classList.remove("hidden");

      let fixesText = "";
      if (res.fixes && res.fixes.length > 0) {
        fixesText = "=== АВТОИСПРАВЛЕНИЯ ===\n" + res.fixes.map(f => "✔ " + f).join("\n") + "\n\n";
      }
      compilerLog.innerText = fixesText + (res.log || "Компиляция завершена успешно.");

      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } else {
      statusBadge.className = "status-badge st-error";
      statusBadge.innerText = `❌ Ошибка сборки (код: ${res.returncode})`;
      compilerLog.innerText = (res.fixes && res.fixes.length > 0 ? res.fixes.join("\n") + "\n\n" : "") + (res.log || "Процесс завершился с ошибкой.");

      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
    }
  } catch (err) {
    compileBtn.disabled = false;
    compileBtn.innerText = "🚀 Скомпилировать мод";
    statusBadge.className = "status-badge st-error";
    statusBadge.innerText = "❌ Ошибка соединения";
    compilerLog.innerText = String(err.message || err);
  }
}

function downloadResult() {
  if (!currentDownloadUrl) return;
  if (tg?.openLink) {
    tg.openLink(currentDownloadUrl);
  } else {
    window.location.href = currentDownloadUrl;
  }
}
