const state = {
  selectedFiles: [],
  archiveStudies: [],
  collapsedGroups: new Set(),
  expandedGroups: new Set(),
  contour: null,
  currentStudy: null,
  view: { zoom: 1, panX: 0, panY: 0 },
  draggingPoint: null,
  isPanning: false,
  lastPointer: null,
};

const violationsByRegion = {
  "Поясничный отдел позвоночника": [
    "Некорректная укладка",
    "Не выравнена ось позвоночника",
    "Присутствуют посторонние предметы",
  ],
  "Проксимальный отдел бедра": [
    "Некорректная укладка",
    "Некорректная область интереса",
  ],
};

const qs = (selector, root = document) => root.querySelector(selector);
const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes) {
  if (!bytes) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function statusLabel(study) {
  return study.processing_status || study.status || "-";
}

function typeLabel(study) {
  if (study.source_type === "zip") return "ZIP-архив";
  return study.group_name && study.group_name !== "Отдельные файлы" ? "файл в папке" : "отдельный файл";
}

function studyLaterality(study) {
  return study.metadata?.laterality || "";
}

function setActiveNav() {
  const path = window.location.pathname;
  qsa(".nav-links a").forEach((link) => {
    const href = link.getAttribute("href");
    link.classList.toggle("active", href === path || (href !== "/" && path.startsWith(href)));
  });
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const error = new Error(
      typeof detail === "string" ? detail : detail?.message || "Ошибка запроса",
    );
    error.payload = body;
    throw error;
  }
  return body;
}

function applyAppearance() {
  document.documentElement.dataset.theme = "light";
  document.documentElement.dataset.density = "comfortable";
}

function imageFilter() {
  const brightness = localStorage.getItem("imageBrightness") || "100";
  const contrast = localStorage.getItem("imageContrast") || "100";
  return { brightness: Number(brightness) / 100, contrast: Number(contrast) / 100 };
}

function windowPixel(value, filter) {
  const contrasted = (value - 128) * filter.contrast + 128;
  return Math.max(0, Math.min(255, contrasted * filter.brightness));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function validateInputFile(file) {
  const name = (file.webkitRelativePath || file.name || "").toLowerCase();
  const extension = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  const allowed = [".dcm", ".dicom", ".zip", ""];
  if (allowed.includes(extension)) return null;
  return {
    file: file.webkitRelativePath || file.name || "Файл",
    reason: "Неправильный формат файла. Загрузите DICOM-файл, папку с DICOM или ZIP-архив.",
  };
}

function addFilesToQueue(files) {
  const incoming = Array.from(files || []);
  const errors = [];
  const accepted = [];
  incoming.forEach((file) => {
    const error = validateInputFile(file);
    if (error) errors.push(error);
    else accepted.push(file);
  });
  if (accepted.length) {
    state.selectedFiles = [...state.selectedFiles, ...accepted];
    renderFileList();
  }
  if (errors.length) {
    showUploadErrorModal({
      message: "Файлы не добавлены в очередь",
      payload: {
        detail: {
          message: "Часть файлов не подходит для исследования",
          errors,
        },
      },
    });
  }
}

function renderFileList() {
  const list = qs("#fileList");
  const counter = qs("#fileCounter");
  const submit = qs("#submitFiles");
  if (!list) return;
  counter.textContent = `${state.selectedFiles.length} файлов`;
  submit.disabled = state.selectedFiles.length === 0;
  if (!state.selectedFiles.length) {
    list.className = "file-list empty-state";
    list.textContent = "Файлы пока не выбраны";
    return;
  }
  list.className = "file-list";
  list.innerHTML = state.selectedFiles
    .map((file) => {
      const name = file.webkitRelativePath || file.name;
      const type = file.name.toLowerCase().endsWith(".zip") ? "ZIP" : "DICOM";
      return `
        <div class="file-item">
          <strong>${escapeHtml(name)}</strong>
          <div class="file-meta"><span>${type}</span><span>${formatBytes(file.size)}</span></div>
        </div>`;
    })
    .join("");
}

function setupHome() {
  const fileInput = qs("#fileInput");
  const folderInput = qs("#folderInput");
  const dropZone = qs("#dropZone");
  const submit = qs("#submitFiles");
  if (!fileInput) return;

  qs("#chooseFiles").addEventListener("click", () => fileInput.click());
  qs("#chooseFolder").addEventListener("click", () => folderInput.click());

  fileInput.addEventListener("change", () => {
    addFilesToQueue(fileInput.files);
    fileInput.value = "";
  });
  folderInput.addEventListener("change", () => {
    addFilesToQueue(folderInput.files);
    folderInput.value = "";
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragover");
    });
  });
  dropZone.addEventListener("drop", (event) => {
    addFilesToQueue(event.dataTransfer.files);
  });

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    submit.textContent = "Загрузка...";
    const form = new FormData();
    state.selectedFiles.forEach((file) => {
      form.append("files", file, file.webkitRelativePath || file.name);
    });
    try {
      const data = await getJson("/api/uploads", { method: "POST", body: form });
      window.location.href = data.redirect_url;
    } catch (error) {
      submit.disabled = false;
      submit.textContent = "Отправить на исследование";
      showUploadErrorModal(error);
    }
  });

  loadHomeArchive();
}

async function loadHomeArchive() {
  const target = qs("#homeArchive");
  if (!target) return;
  const data = await getJson("/api/archive");
  const studies = Object.values(data.groups).flat().slice(0, 8);
  state.archiveStudies = studies;
  target.innerHTML = studies.length
    ? studies.map(renderArchiveItem).join("")
    : `<div class="empty-state">Архив пока пуст</div>`;
  bindErrorButtons(target);
}

function renderArchiveItem(study) {
  const date = new Date(study.created_at).toLocaleString("ru-RU");
  const type = typeLabel(study);
  const status = statusLabel(study);
  const region = study.anatomical_region || "область не определена";
  const laterality = studyLaterality(study);
  const hasError = status === "Failure" || study.status === "failed" || study.metadata?.error_message;
  return `
    <div class="archive-item ${hasError ? "has-error" : ""}">
      <a class="archive-link" href="/study/${study.id}">
        <strong>${escapeHtml(study.display_name)}</strong>
        <div class="file-meta">
          <span>${date}</span>
          <span>${type}</span>
          <span>${escapeHtml(status)}</span>
          <span>${escapeHtml(region)}</span>
          ${laterality ? `<span>${escapeHtml(laterality)}</span>` : ""}
        </div>
      </a>
      ${hasError ? `<button class="btn ghost small-btn error-open" type="button" data-study-id="${study.id}">Что случилось</button>` : ""}
    </div>`;
}

function bindErrorButtons(root = document) {
  qsa(".error-open", root).forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const study = state.archiveStudies.find((item) => item.id === button.dataset.studyId);
      if (study) showErrorModal(study);
    });
  });
}

function buildErrorDetails(study) {
  const metadata = study.metadata || {};
  const title = metadata.error_title || "Ошибка обработки";
  const reason =
    metadata.error_message ||
    metadata.validation_message ||
    study.violation_type ||
    "Файл не обработан. В отчете для этой строки будет указан processing_status = Failure.";
  const recommendations = {
    not_dicom: "Проверьте, что загружен исходный DICOM-файл, а не изображение, PDF или служебный файл архива.",
    unsupported_dicom: "Проверьте, что исследование относится к позвоночнику или проксимальному отделу бедра.",
    low_quality_preview: "Проверьте качество снимка и при необходимости повторите экспорт или сканирование.",
  };
  return {
    title,
    reason,
    recommendation: recommendations[metadata.error_type] || "Проверьте файл и при необходимости загрузите его повторно.",
  };
}

function showErrorModal(study) {
  const modal = qs("#errorModal");
  const titleTarget = qs("#errorTitle");
  const bodyTarget = qs("#errorBody");
  if (!modal || !titleTarget || !bodyTarget) return;
  const details = buildErrorDetails(study);
  titleTarget.textContent = details.title;
  bodyTarget.innerHTML = `
    <div class="error-summary">
      <strong>${escapeHtml(study.display_name)}</strong>
      <span>${escapeHtml(statusLabel(study))}</span>
    </div>
    <dl class="params-list">
      <div><dt>Причина</dt><dd>${escapeHtml(details.reason)}</dd></div>
      <div><dt>Запись в отчете</dt><dd>processing_status = Failure</dd></div>
      <div><dt>Что проверить</dt><dd>${escapeHtml(details.recommendation)}</dd></div>
    </dl>`;
  modal.hidden = false;
}

function closeErrorModal() {
  const modal = qs("#errorModal");
  if (modal) modal.hidden = true;
}

function showUploadErrorModal(error) {
  const modal = qs("#errorModal");
  const titleTarget = qs("#errorTitle");
  const bodyTarget = qs("#errorBody");
  if (!modal || !titleTarget || !bodyTarget) {
    alert(error.message);
    return;
  }
  const detail = error.payload?.detail;
  const errors = Array.isArray(detail?.errors) ? detail.errors : [];
  titleTarget.textContent = "Файл не принят";
  bodyTarget.innerHTML = `
    <div class="error-summary">
      <strong>${escapeHtml(detail?.message || error.message)}</strong>
      <span>загрузка отменена</span>
    </div>
    <div class="error-list">
      ${
        errors.length
          ? errors
              .map((item) => `
                <div class="error-list-item">
                  <strong>${escapeHtml(item.file || "Файл")}</strong>
                  <span>${escapeHtml(item.reason || "Файл не открывается или не разбирается как DICOM")}</span>
                </div>`)
              .join("")
          : `<div class="error-list-item"><span>${escapeHtml(error.message)}</span></div>`
      }
    </div>
    <p class="muted small">Такие файлы не сохраняются в архиве и удаляются из временного хранилища.</p>`;
  modal.hidden = false;
}

function updateStages(progress) {
  const items = qsa("#stageList li");
  if (!items.length) return;
  const activeIndex = Math.min(items.length - 1, Math.floor((progress / 100) * items.length));
  items.forEach((item, index) => {
    item.classList.toggle("done", progress === 100 || index < activeIndex);
    item.classList.toggle("active", progress < 100 && index === activeIndex);
  });
}

function renderProcessingFiles(studies) {
  const target = qs("#processingFiles");
  if (!target) return;
  state.archiveStudies = studies;
  target.innerHTML = studies
    .map((study) => {
      const laterality = studyLaterality(study);
      const hasError = statusLabel(study) === "Failure" || study.status === "failed" || study.metadata?.error_message;
      return `
      <div class="file-item ${hasError ? "has-error" : ""}">
        <div class="archive-link">
          <strong>${escapeHtml(study.display_name)}</strong>
          <div class="file-meta">
            <span>${escapeHtml(study.group_name)}</span>
            <span>${escapeHtml(statusLabel(study))}</span>
            <span>${escapeHtml(study.anatomical_region || "область определяется")}</span>
            ${laterality ? `<span>${escapeHtml(laterality)}</span>` : ""}
          </div>
        </div>
        ${hasError ? `<button class="btn ghost small-btn error-open" type="button" data-study-id="${study.id}">Что случилось</button>` : ""}
      </div>`;
    })
    .join("");
  bindErrorButtons(target);
}

async function pollProcessing() {
  const root = qs("[data-page='processing']");
  if (!root) return;
  const jobId = root.dataset.jobId;
  const data = await getJson(`/api/jobs/${jobId}`);
  const job = data.job;
  qs("#totalFiles").textContent = job.total_files;
  qs("#processedFiles").textContent = job.processed_files;
  qs("#currentFile").textContent = job.current_file || "-";
  qs("#jobStatus").textContent = job.status;
  qs("#jobMessage").textContent = job.message || "-";
  qs("#progressValue").textContent = job.progress;
  qs("#progressBar").style.width = `${job.progress}%`;
  updateStages(job.progress);
  renderProcessingFiles(data.studies);
  if (job.status === "done") {
    setTimeout(() => {
      window.location.href = `/results/${jobId}`;
    }, 900);
  } else {
    setTimeout(pollProcessing, 800);
  }
}

function resultBadge(value) {
  if (value === 1) return `<span class="badge bad">нарушение</span>`;
  if (value === 0) return `<span class="badge ok">качество</span>`;
  return `<span class="badge warn">ошибка</span>`;
}

async function loadResults() {
  const root = qs("[data-page='results']");
  if (!root) return;
  const data = await getJson(`/api/jobs/${root.dataset.jobId}`);
  const studies = data.studies;
  state.archiveStudies = studies;
  const bad = studies.filter((study) => study.quality_class === 1).length;
  const success = studies.filter((study) => study.processing_status === "Success").length;
  const times = studies.map((study) => Number(study.time_of_processing || 0)).filter(Boolean);
  const avgTime = times.length ? times.reduce((sum, value) => sum + value, 0) / times.length : 0;
  qs("#summaryTotal").textContent = studies.length;
  qs("#summaryBad").textContent = bad;
  qs("#summarySuccess").textContent = success;
  qs("#summaryTime").textContent = `${avgTime.toFixed(1)} c`;
  qs("#resultsTable").innerHTML = studies
    .map((study) => `
      <tr>
        <td>${escapeHtml(study.display_name)}</td>
        <td>${escapeHtml(study.anatomical_region || "-")}</td>
        <td>${escapeHtml(studyLaterality(study) || "-")}</td>
        <td>${resultBadge(study.quality_class)}</td>
        <td>${study.quality_prob ?? "-"}</td>
        <td>${escapeHtml(study.violation_type || "-")}</td>
        <td>${study.time_of_processing ? `${study.time_of_processing} c` : "-"}</td>
        <td>
          <div class="toolbar compact">
            ${statusLabel(study) === "Failure" || study.status === "failed" || study.metadata?.error_message ? `<button class="btn ghost small-btn error-open" type="button" data-study-id="${study.id}">Ошибка</button>` : ""}
            <a class="btn ghost" href="/editor/${study.id}">Контур</a>
          </div>
        </td>
      </tr>`)
    .join("");
  bindErrorButtons(qs("#resultsTable"));
}

async function loadArchive() {
  const root = qs("[data-page='archive']");
  if (!root) return;
  const data = await getJson("/api/archive");
  state.archiveStudies = Object.values(data.groups).flat().filter(isArchiveReady);
  const filters = readArchiveFilters();
  const filteredGroups = Object.fromEntries(
    Object.entries(data.groups)
      .map(([group, studies]) => [group, studies.filter((study) => isArchiveReady(study) && archiveMatches(study, filters))])
      .filter(([, studies]) => studies.length),
  );
  const total = Object.values(filteredGroups).flat().length;
  const allTotal = state.archiveStudies.length;
  qs("#archiveCounter").textContent = `${total} из ${allTotal} записей`;
  qs("#archiveGroups").innerHTML = Object.entries(filteredGroups)
    .map(([group, studies]) => renderArchiveGroup(group, studies))
    .join("") || `<div class="empty-state">Архив пока пуст</div>`;
  bindArchiveGroupToggles();
  bindErrorButtons(qs("#archiveGroups"));
}

function readArchiveFilters() {
  return {
    query: (qs("#archiveSearch")?.value || "").trim().toLowerCase(),
    dateFrom: qs("#archiveDateFrom")?.value || "",
    dateTo: qs("#archiveDateTo")?.value || "",
    region: qs("#archiveRegion")?.value || "all",
  };
}

function isArchiveReady(study) {
  return ["Success", "ManualReview"].includes(study.processing_status);
}

function archiveMatches(study, filters) {
  const created = new Date(study.created_at);
  if (filters.dateFrom) {
    const from = new Date(`${filters.dateFrom}T00:00:00`);
    if (created < from) return false;
  }
  if (filters.dateTo) {
    const to = new Date(`${filters.dateTo}T23:59:59`);
    if (created > to) return false;
  }
  if (filters.region !== "all" && study.anatomical_region !== filters.region) return false;
  if (filters.query) {
    const haystack = [
      study.display_name,
      study.group_name,
      study.metadata?.original_name,
      study.metadata?.container,
    ].filter(Boolean).join(" ").toLowerCase();
    if (!haystack.includes(filters.query)) return false;
  }
  return true;
}

function archiveGroupTitle(group) {
  return escapeHtml(group);
}

function archiveGroupKey(group) {
  return encodeURIComponent(group);
}

function renderArchiveGroup(group, studies) {
  const isLoose = group === "Отдельные файлы";
  const key = archiveGroupKey(group);
  const collapsed = !state.expandedGroups.has(key);
  if (isLoose) {
    return `
      <section class="archive-group loose-files">
        <h2>${archiveGroupTitle(group)}</h2>
        <div class="archive-children">
          ${studies.map(renderArchiveItem).join("")}
        </div>
      </section>`;
  }
  return `
    <section class="archive-group nested-files ${collapsed ? "collapsed" : ""}" data-group-key="${key}">
      <button class="archive-folder-toggle" type="button" data-group-key="${key}" aria-expanded="${collapsed ? "false" : "true"}">
        <span class="folder-caret">${collapsed ? "+" : "-"}</span>
        <strong>${archiveGroupTitle(group)}</strong>
        <span>${studies.length} файлов</span>
      </button>
      <div class="archive-children" ${collapsed ? "hidden" : ""}>
        ${studies.map(renderArchiveItem).join("")}
      </div>
    </section>`;
}

function bindArchiveGroupToggles() {
  qsa(".archive-folder-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.groupKey;
      if (!key) return;
      const group = button.closest(".archive-group");
      const children = group?.querySelector(".archive-children");
      const nextCollapsed = !group?.classList.contains("collapsed");
      if (nextCollapsed) state.expandedGroups.delete(key);
      else state.expandedGroups.add(key);
      group?.classList.toggle("collapsed", nextCollapsed);
      if (children) children.hidden = nextCollapsed;
      button.setAttribute("aria-expanded", nextCollapsed ? "false" : "true");
      const caret = button.querySelector(".folder-caret");
      if (caret) caret.textContent = nextCollapsed ? "+" : "-";
    });
  });
}

function setupArchiveFilters() {
  const root = qs("[data-page='archive']");
  if (!root) return;
  ["#archiveDateFrom", "#archiveDateTo", "#archiveRegion"].forEach((selector) => {
    qs(selector)?.addEventListener("change", loadArchive);
  });
  qs("#archiveSearch")?.addEventListener("input", loadArchive);
  qs("#archiveFilterReset")?.addEventListener("click", () => {
    ["#archiveSearch", "#archiveDateFrom", "#archiveDateTo"].forEach((selector) => {
      const input = qs(selector);
      if (input) input.value = "";
    });
    const region = qs("#archiveRegion");
    if (region) region.value = "all";
    loadArchive();
  });
}

function setupErrorModal() {
  qs("#closeErrorModal")?.addEventListener("click", closeErrorModal);
  qs("#ackErrorModal")?.addEventListener("click", closeErrorModal);
  qs("#errorModal")?.addEventListener("click", (event) => {
    if (event.target === qs("#errorModal")) closeErrorModal();
  });
}

function drawDxaScene(canvas, contour, study, view = { zoom: 1, panX: 0, panY: 0 }, showNodes = false) {
  if (!canvas || !contour) return;
  const ctx = canvas.getContext("2d");
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);

  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, width, height);
  ctx.clip();
  ctx.translate(width / 2 + view.panX, height / 2 + view.panY);
  ctx.scale(view.zoom, view.zoom);
  ctx.translate(-width / 2, -height / 2);

  const filter = imageFilter();
  ctx.fillStyle = "black";
  ctx.fillRect(0, 0, width, height);

  const region = contour.region || study?.anatomical_region || "";
  ctx.save();
  ctx.translate(width / 2, height / 2);
  ctx.filter = "blur(10px)";
  if (region.includes("Пояснич")) {
    for (let i = -3; i <= 3; i += 1) {
      const shade = windowPixel(178 + Math.abs(i) * 8, filter);
      ctx.fillStyle = `rgb(${shade}, ${shade}, ${shade})`;
      ctx.beginPath();
      ctx.roundRect(-74, i * 58 - 23, 148, 46, 10);
      ctx.fill();
    }
    ctx.filter = "blur(14px)";
    ctx.fillStyle = `rgb(${windowPixel(135, filter)}, ${windowPixel(135, filter)}, ${windowPixel(135, filter)})`;
    ctx.beginPath();
    ctx.ellipse(-122, 214, 90, 42, -0.2, 0, Math.PI * 2);
    ctx.ellipse(122, 214, 90, 42, 0.2, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.filter = "blur(13px)";
    ctx.fillStyle = `rgb(${windowPixel(174, filter)}, ${windowPixel(174, filter)}, ${windowPixel(174, filter)})`;
    ctx.beginPath();
    ctx.ellipse(-190, -210, 118, 95, 0.12, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = `rgb(${windowPixel(205, filter)}, ${windowPixel(205, filter)}, ${windowPixel(205, filter)})`;
    ctx.beginPath();
    ctx.ellipse(-78, -242, 90, 72, 0.1, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = `rgb(${windowPixel(150, filter)}, ${windowPixel(150, filter)}, ${windowPixel(150, filter)})`;
    ctx.beginPath();
    ctx.roundRect(-208, -160, 142, 420, 70);
    ctx.fill();
    ctx.fillStyle = `rgb(${windowPixel(110, filter)}, ${windowPixel(110, filter)}, ${windowPixel(110, filter)})`;
    ctx.beginPath();
    ctx.ellipse(-30, -34, 210, 88, -0.42, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
  ctx.filter = "none";

  ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
  for (let y = 0; y < height; y += 5) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  const points = contour.objects[0].points;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = point.x * width;
    const y = point.y * height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#fff200";
  ctx.lineWidth = 5;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.shadowColor = "rgba(0, 0, 0, 0.7)";
  ctx.shadowBlur = 5;
  ctx.stroke();
  ctx.shadowBlur = 0;

  if (showNodes) {
    points.forEach((point) => {
      ctx.beginPath();
      ctx.arc(point.x * width, point.y * height, 5, 0, Math.PI * 2);
      ctx.fillStyle = "#fff200";
      ctx.fill();
      ctx.strokeStyle = "#061823";
      ctx.lineWidth = 2;
      ctx.stroke();
    });
  }

  (contour.markers || []).forEach((marker) => drawMarker(ctx, marker, width, height));
  ctx.restore();
}

function drawContour() {
  const canvas = qs("#contourCanvas");
  if (!canvas || !state.contour) return;
  drawDxaScene(canvas, state.contour, state.currentStudy, state.view, true);

  const points = state.contour.objects[0].points;
  const pointCounter = qs("#roiPointCount");
  if (pointCounter) pointCounter.textContent = `${points.length} узлов`;
  updateZoomReadout();
}

function drawMarker(ctx, marker, width, height) {
  const x = marker.x * width;
  const y = marker.y * height;
  const color = marker.color || "#0ec76d";
  const dir = marker.direction || "left";
  const length = 70;
  const sign = dir === "right" ? 1 : -1;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 12;
  ctx.lineCap = "butt";
  ctx.beginPath();
  ctx.moveTo(x - sign * length, y);
  ctx.lineTo(x, y);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - sign * 22, y - 18);
  ctx.lineTo(x - sign * 22, y + 18);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function canvasPoint(event) {
  const canvas = qs("#contourCanvas");
  const rect = canvas.getBoundingClientRect();
  const screenX = ((event.clientX - rect.left) / rect.width) * canvas.width;
  const screenY = ((event.clientY - rect.top) / rect.height) * canvas.height;
  return screenToNormalized(screenX, screenY, canvas);
}

function pointerCanvasPosition(event) {
  const canvas = qs("#contourCanvas");
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) / rect.width) * canvas.width,
    y: ((event.clientY - rect.top) / rect.height) * canvas.height,
  };
}

function normalizedToScreen(point, canvas) {
  return {
    x: (point.x * canvas.width - canvas.width / 2) * state.view.zoom + canvas.width / 2 + state.view.panX,
    y: (point.y * canvas.height - canvas.height / 2) * state.view.zoom + canvas.height / 2 + state.view.panY,
  };
}

function screenToNormalized(x, y, canvas) {
  return {
    x: ((x - canvas.width / 2 - state.view.panX) / state.view.zoom + canvas.width / 2) / canvas.width,
    y: ((y - canvas.height / 2 - state.view.panY) / state.view.zoom + canvas.height / 2) / canvas.height,
  };
}

function updateZoomReadout() {
  const target = qs("#zoomValue");
  if (target) target.textContent = `${Math.round(state.view.zoom * 100)}%`;
}

function setZoom(nextZoom, center = null) {
  const canvas = qs("#contourCanvas");
  if (!canvas) return;
  const oldZoom = state.view.zoom;
  const newZoom = clamp(nextZoom, 1, 6);
  const pivot = center || { x: canvas.width / 2, y: canvas.height / 2 };
  const before = screenToNormalized(pivot.x, pivot.y, canvas);
  state.view.zoom = newZoom;
  state.view.panX = pivot.x - canvas.width / 2 - (before.x * canvas.width - canvas.width / 2) * newZoom;
  state.view.panY = pivot.y - canvas.height / 2 - (before.y * canvas.height - canvas.height / 2) * newZoom;
  if (newZoom === 1 && oldZoom !== 1) {
    state.view.panX = 0;
    state.view.panY = 0;
  }
  drawContour();
}

function defaultContourForStudy(study) {
  const isSpine = (study.anatomical_region || "").includes("Пояснич");
  return {
    schema: "dxa-quality-contour-v1",
    region: study.anatomical_region || "Проксимальный отдел бедра",
    coordinate_space: "normalized_preview",
    objects: [{
      id: "roi-main",
      label: "ROI",
      type: "polyline",
      points: isSpine
        ? [
            { x: 0.50, y: 0.18 },
            { x: 0.49, y: 0.30 },
            { x: 0.50, y: 0.42 },
            { x: 0.51, y: 0.55 },
            { x: 0.50, y: 0.69 },
            { x: 0.50, y: 0.82 },
          ]
        : [
            { x: 0.73, y: 0.22 },
            { x: 0.68, y: 0.25 },
            { x: 0.63, y: 0.31 },
            { x: 0.59, y: 0.40 },
            { x: 0.56, y: 0.52 },
            { x: 0.54, y: 0.66 },
            { x: 0.53, y: 0.82 },
          ],
    }],
    markers: [{ id: "marker-1", type: "arrow", color: "#0ec76d", x: 0.36, y: 0.48, direction: "left" }],
  };
}

function qualitySummary(study) {
  if (study.quality_class === 0) return "Качественное исследование";
  if (study.quality_class === 1) return "Есть нарушение качества";
  return "Не определено";
}

async function setupStudyPage() {
  const root = qs("[data-page='study']");
  const canvas = qs("#studyCanvas");
  if (!root || !canvas) return;
  const data = await getJson(`/api/studies/${root.dataset.studyId}`);
  const study = data.study;
  const contour = data.contours[0]?.payload || defaultContourForStudy(study);
  qs("#studyTitle").textContent = study.display_name;
  qs("#studyFileName").textContent = study.display_name;
  qs("#studyContainer").textContent = study.group_name === "Отдельные файлы" ? "отдельный файл" : study.group_name;
  qs("#studyProcessingStatus").textContent = statusLabel(study);
  qs("#studyQualityClass").textContent = qualitySummary(study);
  qs("#studyQualityCard").classList.toggle("bad", study.quality_class === 1);
  qs("#studyQualityCard").classList.toggle("ok", study.quality_class === 0);
  qs("#studyRegion").textContent = study.anatomical_region || "-";
  qs("#studyLaterality").textContent = studyLaterality(study) || "-";
  qs("#studyViolations").textContent = study.violation_type || "Не выявлены";
  qs("#studyProbability").textContent = study.quality_prob ?? "-";
  qs("#studyTime").textContent = study.time_of_processing ? `${study.time_of_processing} c` : "-";
  qs("#studyComment").value = study.metadata?.manual_comment || "";
  drawDxaScene(canvas, contour, study, { zoom: 1, panX: 0, panY: 0 }, false);
}

async function setupEditor() {
  const root = qs("[data-page='editor']");
  const canvas = qs("#contourCanvas");
  if (!root || !canvas) return;
  const data = await getJson(`/api/studies/${root.dataset.studyId}`);
  const study = data.study;
  state.currentStudy = study;
  const latest = data.contours[0]?.payload || {
    schema: "dxa-quality-contour-v1",
    region: study.anatomical_region || "Проксимальный отдел бедра",
    coordinate_space: "normalized_preview",
    objects: [{ id: "roi-main", label: "ROI", type: "polygon", points: [
      { x: 0.73, y: 0.22 },
      { x: 0.68, y: 0.25 },
      { x: 0.63, y: 0.31 },
      { x: 0.59, y: 0.40 },
      { x: 0.56, y: 0.52 },
      { x: 0.54, y: 0.66 },
      { x: 0.53, y: 0.82 },
    ] }],
    markers: [{ id: "marker-1", type: "arrow", color: "#0ec76d", x: 0.36, y: 0.48, direction: "left" }],
  };
  latest.objects[0].type = "polyline";
  state.contour = latest;
  qs("#editorFileName").textContent = study.display_name;
  qs("#editorStatus").textContent = study.processing_status || study.status;
  qs("#reviewRegion").value = study.anatomical_region || "Проксимальный отдел бедра";
  qs("#reviewLaterality").value = study.metadata?.laterality || "";
  qs("#reviewClass").value = String(study.quality_class ?? 0);
  qs("#reviewProb").value = study.quality_prob ?? "";
  qs("#reviewStatus").value = study.processing_status || "Success";
  qs("#reviewComment").value = study.metadata?.manual_comment || "";
  qs("#dicomSummary").textContent = [
    study.metadata?.modality,
    study.metadata?.study_description,
  ].filter(Boolean).join(" / ") || "демо";
  renderViolationChecklist(study.violation_type || "");
  updateLateralityField();
  qs("#contourVersions").innerHTML = data.contours.length
    ? data.contours.map((contour) => `
      <div class="archive-item">
        <strong>Версия ${contour.version}</strong>
        <div class="file-meta"><span>${contour.source}</span><span>${new Date(contour.created_at).toLocaleString("ru-RU")}</span></div>
      </div>`).join("")
    : `<div class="empty-state">Версий пока нет</div>`;
  drawContour();
  setupViewerSettings();

  qs("#reviewRegion").addEventListener("change", () => {
    state.contour.region = qs("#reviewRegion").value;
    updateLateralityField();
    renderViolationChecklist(collectViolations().join("; "));
    drawContour();
  });

  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    const screen = pointerCanvasPosition(event);
    const points = state.contour.objects[0].points;
    state.draggingPoint = points.findIndex((candidate) => {
      const candidateScreen = normalizedToScreen(candidate, canvas);
      return Math.hypot(candidateScreen.x - screen.x, candidateScreen.y - screen.y) < 14;
    });
    state.isPanning = state.draggingPoint < 0;
    state.lastPointer = { clientX: event.clientX, clientY: event.clientY };
  });
  canvas.addEventListener("pointermove", (event) => {
    if (state.draggingPoint !== null && state.draggingPoint >= 0) {
      const point = canvasPoint(event);
      state.contour.objects[0].points[state.draggingPoint] = {
        x: clamp(point.x, 0.02, 0.98),
        y: clamp(point.y, 0.02, 0.98),
      };
      drawContour();
      return;
    }
    if (state.isPanning && state.lastPointer) {
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      state.view.panX += (event.clientX - state.lastPointer.clientX) * scaleX;
      state.view.panY += (event.clientY - state.lastPointer.clientY) * scaleY;
      state.lastPointer = { clientX: event.clientX, clientY: event.clientY };
      drawContour();
    }
  });
  canvas.addEventListener("pointerup", (event) => {
    canvas.releasePointerCapture(event.pointerId);
    state.draggingPoint = null;
    state.isPanning = false;
    state.lastPointer = null;
  });
  canvas.addEventListener("pointercancel", () => {
    state.draggingPoint = null;
    state.isPanning = false;
    state.lastPointer = null;
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const screen = pointerCanvasPosition(event);
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    setZoom(state.view.zoom * factor, screen);
  }, { passive: false });
  qs("#zoomIn").addEventListener("click", () => {
    setZoom(state.view.zoom * 1.2);
  });
  qs("#zoomOut").addEventListener("click", () => {
    setZoom(state.view.zoom / 1.2);
  });
  qs("#zoomReset").addEventListener("click", () => {
    state.view = { zoom: 1, panX: 0, panY: 0 };
    drawContour();
  });
  qs("#addPoint").addEventListener("click", () => {
    const points = state.contour.objects[0].points;
    const last = points[points.length - 1] || { x: 0.55, y: 0.70 };
    points.push({ x: Math.min(0.96, last.x + 0.01), y: Math.min(0.94, last.y + 0.06) });
    drawContour();
  });
  qs("#removePoint").addEventListener("click", () => {
    const points = state.contour.objects[0].points;
    if (points.length > 3) points.pop();
    drawContour();
  });
  qs("#addMarker").addEventListener("click", () => {
    state.contour.markers = state.contour.markers || [];
    state.contour.markers.push({
      id: `marker-${Date.now()}`,
      type: "arrow",
      color: state.contour.markers.length % 2 ? "#ff1f1f" : "#0ec76d",
      x: 0.42,
      y: 0.50,
      direction: "left",
    });
    drawContour();
  });
  const saveHandler = async () => {
    const qualityClass = Number(qs("#reviewClass").value);
    const probabilityRaw = qs("#reviewProb").value;
    const violations = collectViolations();
    state.contour.region = qs("#reviewRegion").value;
    state.contour.objects[0].type = "polyline";
    const saveStatus = qs("#saveStatus");
    if (saveStatus) saveStatus.textContent = "Сохранение...";
    await getJson(`/api/studies/${root.dataset.studyId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anatomical_region: qs("#reviewRegion").value,
        quality_class: qualityClass,
        quality_prob: probabilityRaw === "" ? null : Number(probabilityRaw),
        violation_type: violations.join("; "),
        processing_status: qs("#reviewStatus").value,
        metadata: {
          ...(state.currentStudy.metadata || {}),
          laterality: qs("#reviewRegion").value === "Проксимальный отдел бедра" ? qs("#reviewLaterality").value : "",
          manual_comment: qs("#reviewComment").value,
          manual_review_saved_at: new Date().toISOString(),
        },
      }),
    });
    await getJson(`/api/studies/${root.dataset.studyId}/contours`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.contour),
    });
    if (saveStatus) saveStatus.textContent = "Изменения сохранены";
    window.location.reload();
  };
  qs("#saveReview").addEventListener("click", saveHandler);
  qs("#saveReviewTop").addEventListener("click", saveHandler);
}

function updateLateralityField() {
  const field = qs("#lateralityField");
  const select = qs("#reviewLaterality");
  if (!field || !select) return;
  const isHip = qs("#reviewRegion")?.value === "Проксимальный отдел бедра";
  field.classList.toggle("is-muted", !isHip);
  select.disabled = !isHip;
  if (!isHip) select.value = "";
}

function renderViolationChecklist(selectedText) {
  const target = qs("#violationChecklist");
  const region = qs("#reviewRegion")?.value || "Проксимальный отдел бедра";
  if (!target) return;
  const selected = new Set(
    selectedText
      .split(";")
      .map((item) => item.trim())
      .filter(Boolean),
  );
  target.innerHTML = (violationsByRegion[region] || [])
    .map((violation) => `
      <label class="check-row">
        <input type="checkbox" value="${violation}" ${selected.has(violation) ? "checked" : ""}>
        <span>${violation}</span>
      </label>`)
    .join("");
}

function collectViolations() {
  return qsa("#violationChecklist input:checked").map((input) => input.value);
}

function setupViewerSettings() {
  const imageBrightness = qs("#imageBrightnessRange");
  const imageContrast = qs("#imageContrastRange");
  const units = qs("#unitSelect");
  if (!imageBrightness || !imageContrast || !units) return;
  const savedImageBrightness = localStorage.getItem("imageBrightness");
  const savedImageContrast = localStorage.getItem("imageContrast");
  const savedUnits = localStorage.getItem("units");
  if (savedImageBrightness) imageBrightness.value = savedImageBrightness;
  if (savedImageContrast) imageContrast.value = savedImageContrast;
  if (savedUnits) units.value = savedUnits;
  imageBrightness.addEventListener("input", () => {
    localStorage.setItem("imageBrightness", imageBrightness.value);
    drawContour();
  });
  imageContrast.addEventListener("input", () => {
    localStorage.setItem("imageContrast", imageContrast.value);
    drawContour();
  });
  units.addEventListener("change", () => {
    localStorage.setItem("units", units.value);
  });
}

applyAppearance();
setActiveNav();
setupHome();
pollProcessing();
loadResults();
setupErrorModal();
setupArchiveFilters();
loadArchive();
setupStudyPage();
setupEditor();
