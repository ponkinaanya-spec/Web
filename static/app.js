const state = {
  selectedFiles: [],
  archiveStudies: [],
  archiveFilteredStudies: [],
  archiveSelected: new Set(),
  collapsedGroups: new Set(),
  expandedGroups: new Set(),
  contour: null,
  initialContour: null,
  currentStudy: null,
  editorImage: null,
  undoStack: [],
  redoStack: [],
  maxHistory: 80,
  selectedObjectIndex: 0,
  selectedPointIndex: null,
  insertPointMode: false,
  editTool: "brush",
  brushSize: 2,
  eraserSize: 12,
  brushShape: "circle",
  activeStroke: null,
  stickStroke: null,
  showMachineOverlay: true,
  view: { zoom: 1, panX: 0, panY: 0, fitZoom: 1 },
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
  const displayName = file.relativePath || file.webkitRelativePath || file.name || "";
  const name = displayName.toLowerCase();
  const extension = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  const allowed = [".dcm", ".dicom", ".zip"];
  if (allowed.includes(extension)) return null;
  return {
    file: displayName || "Файл",
    reason: "Неправильный формат файла. Загрузите DICOM-файл, папку с DICOM или ZIP-архив.",
  };
}

function showQueueRejected(errors) {
  showUploadErrorModal({
    message: "Файлы не добавлены в очередь",
    payload: {
      detail: {
        message: "Файлы не добавлены в очередь",
        errors,
      },
    },
  });
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
  if (errors.length) {
    showQueueRejected(errors);
    return;
  }
  if (accepted.length) {
    state.selectedFiles = [...state.selectedFiles, ...accepted];
    renderFileList();
  }
}

function readDirectoryEntries(reader) {
  return new Promise((resolve, reject) => {
    reader.readEntries(resolve, reject);
  });
}

function entryFile(entry) {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject);
  });
}

async function collectDroppedEntryFiles(entry, prefix = "") {
  if (!entry) return [];
  if (entry.isFile) {
    const file = await entryFile(entry);
    Object.defineProperty(file, "relativePath", {
      value: `${prefix}${file.name}`,
      configurable: true,
    });
    return [file];
  }
  if (!entry.isDirectory) return [];
  const reader = entry.createReader();
  const files = [];
  let batch = await readDirectoryEntries(reader);
  while (batch.length) {
    for (const child of batch) {
      files.push(...(await collectDroppedEntryFiles(child, `${prefix}${entry.name}/`)));
    }
    batch = await readDirectoryEntries(reader);
  }
  return files;
}

async function addDroppedItemsToQueue(dataTransfer) {
  const items = Array.from(dataTransfer.items || []);
  if (!items.length) {
    addFilesToQueue(dataTransfer.files);
    return;
  }
  try {
    const files = [];
    for (const item of items) {
      const entry = item.webkitGetAsEntry?.();
      if (entry) files.push(...(await collectDroppedEntryFiles(entry)));
      else if (item.kind === "file") files.push(item.getAsFile());
    }
    const realFiles = files.filter(Boolean);
    if (!realFiles.length) {
      showQueueRejected([{ file: "Папка", reason: "В папке нет файлов, подходящих для обработки." }]);
      return;
    }
    addFilesToQueue(realFiles);
  } catch {
    showQueueRejected([
      {
        file: "Папка",
        reason: "Не удалось прочитать папку. Выберите DICOM-файл, папку с DICOM или ZIP-архив.",
      },
    ]);
  }
}

function removeFileFromQueue(index) {
  state.selectedFiles.splice(index, 1);
  renderFileList();
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
    .map((file, index) => {
      const name = file.relativePath || file.webkitRelativePath || file.name;
      const type = file.name.toLowerCase().endsWith(".zip") ? "ZIP" : "DICOM";
      return `
        <div class="file-item">
          <div class="file-row-main">
            <strong>${escapeHtml(name)}</strong>
            <div class="file-meta"><span>${type}</span><span>${formatBytes(file.size)}</span></div>
          </div>
          <button class="btn ghost icon-btn queue-remove" type="button" data-file-index="${index}" aria-label="Удалить ${escapeHtml(name)}" title="Удалить из очереди">×</button>
        </div>`;
    })
    .join("");
  qsa(".queue-remove", list).forEach((button) => {
    button.addEventListener("click", () => {
      removeFileFromQueue(Number(button.dataset.fileIndex));
    });
  });
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
    addDroppedItemsToQueue(event.dataTransfer);
  });

  submit.addEventListener("click", async () => {
    submit.disabled = true;
    submit.textContent = "Загрузка...";
    const form = new FormData();
    state.selectedFiles.forEach((file) => {
      form.append("files", file, file.relativePath || file.webkitRelativePath || file.name);
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
    ? studies.map((study) => renderArchiveItem(study, { selectable: false })).join("")
    : `<div class="empty-state">Архив пока пуст</div>`;
  bindErrorButtons(target);
}

function renderArchiveItem(study, options = {}) {
  const selectable = options.selectable !== false;
  const status = statusLabel(study);
  const hasError = status === "Failure" || study.status === "failed" || study.metadata?.error_message;
  const fields = [
    ["Область", study.anatomical_region || "-"],
    ["Сторона", studyLaterality(study) || "-"],
    ["Класс", qualitySummary(study)],
    ["Нарушения", study.violation_type || "-"],
    ["Время", study.time_of_processing ? `${study.time_of_processing} c` : "-"],
  ];
  return `
    <div class="archive-item ${selectable ? "selectable" : "plain"} ${hasError ? "has-error" : ""}">
      ${selectable ? `<input class="archive-file-check" type="checkbox" data-study-id="${study.id}" ${state.archiveSelected.has(study.id) ? "checked" : ""} aria-label="Выбрать ${escapeHtml(study.display_name)}">` : ""}
      <a class="archive-link" href="/study/${study.id}">
        <strong>${escapeHtml(study.display_name)}</strong>
        <div class="archive-result-meta">
          ${fields.map(([label, value]) => `<span><b>${label}</b>${escapeHtml(value)}</span>`).join("")}
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
    "Файл не удалось обработать.";
  const recommendations = {
    not_dicom: "Вы прикрепили файл другого формата. Загрузите DICOM-файл, папку с DICOM или ZIP-архив с DICOM-файлами.",
    unsupported_dicom: "Файл открылся, но не похож на DICOM-исследование позвоночника или проксимального отдела бедра.",
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
                  <span>${escapeHtml(item.reason || "Вы прикрепили файл другого формата. Загрузите DICOM-файл.")}</span>
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

function effectiveQualityClass(study) {
  if ((study.violation_type || "").trim()) return 1;
  return study.quality_class;
}

function resultBadge(study) {
  const value = effectiveQualityClass(study);
  if (value === 1) return `<span class="badge bad">некорректно</span>`;
  if (value === 0) return `<span class="badge ok">корректно</span>`;
  return `<span class="badge warn">ошибка</span>`;
}

function isManualContour(contour) {
  return contour?.source === "manual" || contour?.payload?.source === "manual_edit";
}

function manualContours(contours) {
  return (contours || []).filter(isManualContour);
}

function hasManualContour(study) {
  return Boolean(study.has_manual_contour || study.manual_contour_path);
}

function previewCell(study) {
  const manual = hasManualContour(study);
  const overlayUrl = manual ? `/api/studies/${study.id}/variant/edited` : `/api/studies/${study.id}/overlay`;
  const previewUrl = `/api/studies/${study.id}/preview`;
  const overlayLabel = manual ? "Ручная" : "Overlay";
  return `
    <div class="preview-link">
      <a href="${overlayUrl}" target="_blank" rel="noopener" title="Открыть разметку">
        <img class="result-preview" src="${overlayUrl}" alt="DICOM markup: ${escapeHtml(study.display_name)}" loading="lazy">
      </a>
      <span><a href="${overlayUrl}" target="_blank" rel="noopener">${overlayLabel}</a> / <a href="${previewUrl}" target="_blank" rel="noopener">Исходник</a></span>
    </div>`;
}

function resultFileCell(study) {
  return `
    <div class="result-file-cell">
      <strong>${escapeHtml(study.display_name)}</strong>
      <span>${escapeHtml(study.metadata?.study_uid || "Study UID: -")}</span>
      <span>${escapeHtml(study.metadata?.image_uid || "Image UID: -")}</span>
    </div>`;
}

async function loadResults() {
  const root = qs("[data-page='results']");
  if (!root) return;
  const data = await getJson(`/api/jobs/${root.dataset.jobId}`);
  const studies = data.studies;
  state.archiveStudies = studies;
  const bad = studies.filter((study) => effectiveQualityClass(study) === 1).length;
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
        <td>${previewCell(study)}</td>
        <td>${resultFileCell(study)}</td>
        <td>${escapeHtml(study.anatomical_region || "-")}</td>
        <td>${escapeHtml(studyLaterality(study) || "-")}</td>
        <td>${resultBadge(study)}</td>
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
  state.archiveFilteredStudies = state.archiveStudies.filter((study) => archiveMatches(study, filters));
  const visibleIds = new Set(state.archiveFilteredStudies.map((study) => study.id));
  state.archiveSelected = new Set([...state.archiveSelected].filter((id) => visibleIds.has(id)));
  const groupedByDate = groupArchiveByDate(state.archiveFilteredStudies);
  const total = state.archiveFilteredStudies.length;
  const allTotal = state.archiveStudies.length;
  qs("#archiveCounter").textContent = `${total} из ${allTotal} записей`;
  qs("#archiveGroups").innerHTML = Object.entries(groupedByDate)
    .map(([dateKey, studies]) => renderArchiveDateGroup(dateKey, studies))
    .join("") || `<div class="empty-state">Архив пока пуст</div>`;
  bindArchiveGroupToggles();
  bindArchiveSelection();
  bindErrorButtons(qs("#archiveGroups"));
  updateArchiveSelectionUi();
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

function archiveDateKey(study) {
  return new Date(study.created_at).toISOString().slice(0, 10);
}

function archiveDateLabel(dateKey) {
  return new Date(`${dateKey}T00:00:00`).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "long",
    year: "numeric",
  });
}

function groupArchiveByDate(studies) {
  return studies.reduce((acc, study) => {
    const key = archiveDateKey(study);
    acc[key] = acc[key] || [];
    acc[key].push(study);
    return acc;
  }, {});
}

function groupArchiveByFolder(studies) {
  return studies.reduce((acc, study) => {
    const key = study.group_name || "Отдельные файлы";
    acc[key] = acc[key] || [];
    acc[key].push(study);
    return acc;
  }, {});
}

function everySelected(studies) {
  return studies.length > 0 && studies.every((study) => state.archiveSelected.has(study.id));
}

function renderArchiveDateGroup(dateKey, studies) {
  const checked = everySelected(studies) ? "checked" : "";
  const groups = groupArchiveByFolder(studies);
  return `
    <section class="archive-date-group" data-date-key="${dateKey}">
      <label class="archive-date-head">
        <input class="archive-date-check" type="checkbox" data-date-key="${dateKey}" ${checked}>
        <strong>${escapeHtml(archiveDateLabel(dateKey))}</strong>
        <span>${studies.length} файлов</span>
      </label>
      <div class="archive-date-children">
        ${Object.entries(groups).map(([group, groupStudies]) => renderArchiveGroup(dateKey, group, groupStudies)).join("")}
      </div>
    </section>`;
}

function renderArchiveGroup(dateKey, group, studies) {
  const key = `${dateKey}:${archiveGroupKey(group)}`;
  const collapsed = !state.expandedGroups.has(key);
  const checked = everySelected(studies) ? "checked" : "";
  return `
    <section class="archive-group nested-files ${collapsed ? "collapsed" : ""}" data-group-key="${key}">
      <div class="archive-folder-row">
        <label class="archive-folder-select">
          <input class="archive-group-check" type="checkbox" data-group-key="${key}" ${checked}>
          <strong>${archiveGroupTitle(group)}</strong>
        </label>
        <button class="archive-folder-toggle" type="button" data-group-key="${key}" aria-expanded="${collapsed ? "false" : "true"}">
          <span class="folder-caret">${collapsed ? "+" : "-"}</span>
          <span>${studies.length} файлов</span>
        </button>
      </div>
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

function setArchiveSelection(studies, checked) {
  studies.forEach((study) => {
    if (checked) state.archiveSelected.add(study.id);
    else state.archiveSelected.delete(study.id);
  });
  updateArchiveSelectionUi();
  qsa(".archive-file-check").forEach((input) => {
    input.checked = state.archiveSelected.has(input.dataset.studyId);
  });
  qsa(".archive-date-check").forEach((input) => {
    const studiesForDate = state.archiveFilteredStudies.filter((study) => archiveDateKey(study) === input.dataset.dateKey);
    input.checked = everySelected(studiesForDate);
  });
  qsa(".archive-group-check").forEach((input) => {
    const group = input.closest(".archive-group");
    const ids = qsa(".archive-file-check", group).map((item) => item.dataset.studyId);
    input.checked = ids.length > 0 && ids.every((id) => state.archiveSelected.has(id));
  });
}

function bindArchiveSelection() {
  qsa(".archive-file-check").forEach((input) => {
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("change", () => {
      if (input.checked) state.archiveSelected.add(input.dataset.studyId);
      else state.archiveSelected.delete(input.dataset.studyId);
      updateArchiveSelectionUi();
    });
  });
  qsa(".archive-date-check").forEach((input) => {
    input.addEventListener("change", () => {
      const studies = state.archiveFilteredStudies.filter((study) => archiveDateKey(study) === input.dataset.dateKey);
      setArchiveSelection(studies, input.checked);
    });
  });
  qsa(".archive-group-check").forEach((input) => {
    input.addEventListener("change", () => {
      const group = input.closest(".archive-group");
      const ids = qsa(".archive-file-check", group).map((item) => item.dataset.studyId);
      const studies = state.archiveFilteredStudies.filter((study) => ids.includes(study.id));
      setArchiveSelection(studies, input.checked);
    });
  });
}

function updateArchiveSelectionUi() {
  const count = state.archiveSelected.size;
  const counter = qs("#archiveSelectedCounter");
  const download = qs("#archiveDownload");
  const selectAll = qs("#archiveSelectAll");
  if (counter) counter.textContent = `${count} выбрано`;
  if (download) download.disabled = count === 0;
  if (selectAll) {
    const allVisibleSelected = state.archiveFilteredStudies.length > 0 && state.archiveFilteredStudies.every((study) => state.archiveSelected.has(study.id));
    selectAll.textContent = allVisibleSelected ? "Снять выбор" : "Выбрать все";
  }
}

async function downloadArchiveSelection() {
  const format = qs("#archiveExportFormat")?.value || "csv";
  const ids = [...state.archiveSelected];
  if (!ids.length) return;
  const response = await fetch(`/api/archive/export/${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ study_ids: ids }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    alert(body.detail || "Не удалось скачать отчет");
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `archive-selected.${format}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
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
  qs("#archiveSelectAll")?.addEventListener("click", () => {
    const allVisibleSelected = state.archiveFilteredStudies.length > 0 && state.archiveFilteredStudies.every((study) => state.archiveSelected.has(study.id));
    setArchiveSelection(state.archiveFilteredStudies, !allVisibleSelected);
  });
  qs("#archiveDownload")?.addEventListener("click", downloadArchiveSelection);
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

  if (state.editorImage) {
    ctx.filter = `brightness(${filter.brightness}) contrast(${filter.contrast})`;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(state.editorImage, 0, 0, width, height);
    ctx.filter = "none";
  }

  const machineLayer = document.createElement("canvas");
  machineLayer.width = width;
  machineLayer.height = height;
  const machineCtx = machineLayer.getContext("2d");
  if (state.showMachineOverlay) (contour.objects || []).forEach((object) => {
    const points = object.points || [];
    if (!points.length) return;
    if (object.type === "points") {
      const radius = object.radius_ratio ? Number(object.radius_ratio) * height : 2;
      machineCtx.save();
      machineCtx.fillStyle = object.color || "#50ff50";
      points.forEach((point) => {
        machineCtx.beginPath();
        machineCtx.arc(point.x * width, point.y * height, radius, 0, Math.PI * 2);
        machineCtx.fill();
      });
      machineCtx.restore();
      return;
    }
    machineCtx.beginPath();
    points.forEach((point, index) => {
      const x = point.x * width;
      const y = point.y * height;
      if (index === 0) machineCtx.moveTo(x, y);
      else machineCtx.lineTo(x, y);
    });
    if (object.type === "polygon" && points.length > 2) machineCtx.closePath();
    machineCtx.strokeStyle = object.color || "#fff200";
    machineCtx.lineWidth = object.line_width_ratio ? Number(object.line_width_ratio) * height : 1.25;
    machineCtx.lineCap = "round";
    machineCtx.lineJoin = "round";
    machineCtx.shadowColor = "rgba(0, 0, 0, 0.72)";
    machineCtx.shadowBlur = 5;
    machineCtx.stroke();
    machineCtx.shadowBlur = 0;
  });
  if (state.showMachineOverlay) (contour.markers || []).forEach((marker) => drawMarker(machineCtx, marker, width, height));
  (contour.machine_eraser_strokes || []).forEach((stroke) => drawBrushStroke(machineCtx, stroke, width, height));
  ctx.drawImage(machineLayer, 0, 0);

  drawBrushLayer(ctx, contour.brush_strokes || [], width, height);
  ctx.restore();
}

function drawBrushDab(ctx, point, size, shape, width, height) {
  const x = point.x * width;
  const y = point.y * height;
  if (shape === "square") {
    ctx.fillRect(x - size / 2, y - size / 2, size, size);
    return;
  }
  ctx.beginPath();
  ctx.arc(x, y, size / 2, 0, Math.PI * 2);
  ctx.fill();
}

function drawBrushStroke(ctx, stroke, width, height) {
  const points = stroke.points || [];
  if (!points.length) return;
  const size = stroke.size_ratio ? Number(stroke.size_ratio) * height : Number(stroke.size || 6);
  ctx.save();
  ctx.globalCompositeOperation = stroke.tool === "eraser" ? "destination-out" : "source-over";
  ctx.strokeStyle = stroke.color || "rgba(255, 230, 0, 1)";
  ctx.fillStyle = stroke.color || "rgba(255, 230, 0, 1)";
  ctx.lineWidth = size;
  ctx.lineCap = stroke.shape === "square" ? "butt" : "round";
  ctx.lineJoin = stroke.shape === "square" ? "miter" : "round";
  if (points.length === 1) {
    drawBrushDab(ctx, points[0], size, stroke.shape, width, height);
  } else {
    ctx.beginPath();
    points.forEach((point, index) => {
      const x = point.x * width;
      const y = point.y * height;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    if (stroke.shape === "square") {
      points.forEach((point) => drawBrushDab(ctx, point, size, stroke.shape, width, height));
    }
  }
  ctx.restore();
}

function drawBrushLayer(ctx, strokes, width, height) {
  if (!strokes.length) return;
  const layer = document.createElement("canvas");
  layer.width = width;
  layer.height = height;
  const layerCtx = layer.getContext("2d");
  strokes.forEach((stroke) => drawBrushStroke(layerCtx, stroke, width, height));
  ctx.drawImage(layer, 0, 0);
}

function selectedContourObject() {
  if (!state.contour?.objects?.length) return null;
  return state.contour.objects[state.selectedObjectIndex] || state.contour.objects[0];
}

function setSelectedPoint(index) {
  const points = selectedContourObject()?.points || [];
  state.selectedPointIndex = Number.isInteger(index) && index >= 0 && index < points.length ? index : null;
  updatePointReadout();
}

function updatePointReadout() {
  const target = qs("#pointReadout");
  if (!target) return;
  const canvas = qs("#contourCanvas");
  const point = selectedContourObject()?.points?.[state.selectedPointIndex];
  if (!canvas || !point) {
    const tool = state.editTool === "eraser" ? "Ластик" : state.editTool === "stick" ? "Палочками" : "Кисть";
    const size = state.editTool === "eraser" ? state.eraserSize : state.brushSize;
    target.textContent = `${tool}: ${size} px, ${state.brushShape === "square" ? "квадрат" : "круг"}`;
    return;
  }
  target.textContent = `Точка ${state.selectedPointIndex + 1}: x=${Math.round(point.x * canvas.width)} px, y=${Math.round(point.y * canvas.height)} px`;
}

function findNearestPointIndex(screen, canvas, maxDistance = 14) {
  const points = selectedContourObject()?.points || [];
  let best = { index: null, distance: Infinity };
  points.forEach((point, index) => {
    const candidate = normalizedToScreen(point, canvas);
    const distance = Math.hypot(candidate.x - screen.x, candidate.y - screen.y);
    if (distance < best.distance) best = { index, distance };
  });
  return best.distance <= maxDistance ? best.index : null;
}

function distanceToSegment(point, start, end) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  if (!lengthSquared) return Math.hypot(point.x - start.x, point.y - start.y);
  const t = clamp(((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared, 0, 1);
  return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy));
}

function findNearestSegmentIndex(point) {
  const object = selectedContourObject();
  const points = object?.points || [];
  if (points.length < 2) return points.length - 1;
  let best = { index: 0, distance: Infinity };
  const segmentCount = object.type === "polygon" ? points.length : points.length - 1;
  for (let index = 0; index < segmentCount; index += 1) {
    const start = points[index];
    const end = points[(index + 1) % points.length];
    const distance = distanceToSegment(point, start, end);
    if (distance < best.distance) best = { index, distance };
  }
  return best.index;
}

function insertPointAt(point) {
  const object = selectedContourObject();
  if (!object?.points) return;
  const segmentIndex = findNearestSegmentIndex(point);
  object.points.splice(segmentIndex + 1, 0, {
    x: clamp(point.x, 0, 1),
    y: clamp(point.y, 0, 1),
  });
  setSelectedPoint(segmentIndex + 1);
  drawContour();
}

function renderObjectSelect() {
  const select = qs("#objectSelect");
  if (!select || !state.contour) return;
  const objects = state.contour.objects || [];
  state.selectedObjectIndex = clamp(state.selectedObjectIndex, 0, Math.max(objects.length - 1, 0));
  setSelectedPoint(null);
  select.innerHTML = objects
    .map((object, index) => `<option value="${index}">${escapeHtml(object.label || object.id || `Объект ${index + 1}`)}</option>`)
    .join("");
  select.value = String(state.selectedObjectIndex);
}

function loadCanvasImage(url, canvas) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      state.editorImage = image;
      const naturalWidth = image.naturalWidth || image.width || canvas.width;
      const naturalHeight = image.naturalHeight || image.height || canvas.height;
      const naturalLongSide = Math.max(naturalWidth, naturalHeight, 1);
      const targetLongSide = Math.max(naturalLongSide, 1800);
      const scale = targetLongSide / naturalLongSide;
      canvas.width = Math.round(naturalWidth * scale);
      canvas.height = Math.round(naturalHeight * scale);
      resolve(image);
    };
    image.onerror = reject;
    image.src = `${url}?t=${Date.now()}`;
  });
}

function drawMarker(ctx, marker, width, height) {
  const x = marker.x * width;
  const y = marker.y * height;
  const color = marker.color || "#0ec76d";
  if (marker.type === "point") {
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#061823";
    ctx.lineWidth = 2;
    ctx.stroke();
    if (marker.label) {
      ctx.font = "12px Segoe UI, Arial, sans-serif";
      ctx.fillText(marker.label, x + 8, y - 7);
    }
    ctx.restore();
    return;
  }

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

function drawContour() {
  const canvas = qs("#contourCanvas");
  if (!canvas || !state.contour) return;
  drawDxaScene(canvas, state.contour, state.currentStudy, state.view, true);

  const pointCounter = qs("#roiPointCount");
  if (pointCounter) pointCounter.textContent = `${(state.contour.brush_strokes || []).length} мазков`;
  updatePointReadout();
  updateZoomReadout();
  updateHistoryControls();
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
  if (target) target.textContent = `${Math.round((state.view.zoom / (state.view.fitZoom || 1)) * 100)}%`;
}

function fitImageToViewer() {
  state.view = { zoom: 1, fitZoom: 1, panX: 0, panY: 0 };
}

function setZoom(nextZoom, center = null) {
  const canvas = qs("#contourCanvas");
  if (!canvas) return;
  const oldZoom = state.view.zoom;
  const newZoom = clamp(nextZoom, 0.15, 4);
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
  const value = effectiveQualityClass(study);
  if (value === 0) return "Корректное исследование";
  if (value === 1) return "Некорректное исследование";
  return "Не определено";
}

async function loadAiContour(study) {
  try {
    const data = await getJson(`/api/studies/${study.id}/ai-contour`);
    return data.contour;
  } catch (_) {
    return defaultContourForStudy(study);
  }
}

function cloneContour(contour) {
  return JSON.parse(JSON.stringify(contour || {}));
}

function editorSnapshot() {
  return {
    contour: cloneContour(state.contour),
    selectedObjectIndex: state.selectedObjectIndex,
    selectedPointIndex: state.selectedPointIndex,
  };
}

function restoreEditorSnapshot(snapshot) {
  if (!snapshot) return;
  state.contour = cloneContour(snapshot.contour);
  state.selectedObjectIndex = snapshot.selectedObjectIndex || 0;
  state.selectedPointIndex = Number.isInteger(snapshot.selectedPointIndex) ? snapshot.selectedPointIndex : null;
  state.activeStroke = null;
  state.stickStroke = null;
  renderObjectSelect();
  drawContour();
}

function updateHistoryControls() {
  const undo = qs("#undoEdit");
  const redo = qs("#redoEdit");
  if (undo) undo.disabled = state.undoStack.length === 0;
  if (redo) redo.disabled = state.redoStack.length === 0;
}

function pushUndoSnapshot() {
  if (!state.contour) return;
  state.undoStack.push(editorSnapshot());
  if (state.undoStack.length > state.maxHistory) state.undoStack.shift();
  state.redoStack = [];
  updateHistoryControls();
}

function undoEdit() {
  if (!state.undoStack.length) return;
  state.redoStack.push(editorSnapshot());
  restoreEditorSnapshot(state.undoStack.pop());
  updateHistoryControls();
}

function redoEdit() {
  if (!state.redoStack.length) return;
  state.undoStack.push(editorSnapshot());
  restoreEditorSnapshot(state.redoStack.pop());
  updateHistoryControls();
}

function resetEditorChanges() {
  if (!state.initialContour) return;
  pushUndoSnapshot();
  state.contour = cloneContour(state.initialContour);
  state.selectedObjectIndex = 0;
  state.selectedPointIndex = null;
  state.activeStroke = null;
  state.stickStroke = null;
  renderObjectSelect();
  drawContour();
  updateHistoryControls();
  const saveStatus = qs("#saveStatus");
  if (saveStatus) saveStatus.textContent = "Изменения отменены";
}

function updateVariantLink(studyId) {
  const select = qs("#variantSelect");
  const link = qs("#openVariant");
  if (!select || !link) return;
  link.href = `/api/studies/${studyId}/variant/${select.value}`;
}

function renderContourVersions(studyId, contours) {
  const target = qs("#contourVersions");
  if (!target) return;
  if (!contours.length) {
    target.innerHTML = `<div class="empty-state">Версий пока нет</div>`;
    return;
  }
  target.innerHTML = contours.map((contour) => `
    <div class="archive-item plain contour-version-item">
      <div>
        <strong>Версия ${contour.version}</strong>
        <div class="file-meta">
          <span>${escapeHtml(contour.source)}</span>
          <span>${new Date(contour.created_at).toLocaleString("ru-RU")}</span>
        </div>
      </div>
      <div class="toolbar compact">
        <button class="btn ghost small-btn contour-load" type="button" data-contour-id="${contour.id}">Выбрать</button>
        <button class="btn ghost small-btn contour-delete" type="button" data-contour-id="${contour.id}">Удалить</button>
      </div>
    </div>`).join("");
  qsa(".contour-load", target).forEach((button) => {
    button.addEventListener("click", () => {
      const contour = contours.find((item) => item.id === button.dataset.contourId);
      if (!contour) return;
      pushUndoSnapshot();
      state.contour = cloneContour(contour.payload);
      state.initialContour = cloneContour(contour.payload);
      state.selectedObjectIndex = 0;
      state.selectedPointIndex = null;
      state.activeStroke = null;
      state.stickStroke = null;
      renderObjectSelect();
      drawContour();
      updateVariantLink(studyId);
      const saveStatus = qs("#saveStatus");
      if (saveStatus) saveStatus.textContent = `Открыта версия ${contour.version}`;
    });
  });
  qsa(".contour-delete", target).forEach((button) => {
    button.addEventListener("click", async () => {
      const response = await getJson(`/api/studies/${studyId}/contours/${button.dataset.contourId}`, { method: "DELETE" });
      renderContourVersions(studyId, response.contours || []);
      const saveStatus = qs("#saveStatus");
      if (saveStatus) saveStatus.textContent = "Версия удалена";
    });
  });
}

function currentBrushPoint(event) {
  const point = canvasPoint(event);
  return {
    x: clamp(point.x, 0, 1),
    y: clamp(point.y, 0, 1),
  };
}

function startBrushStroke(event) {
  pushUndoSnapshot();
  state.contour.brush_strokes = state.contour.brush_strokes || [];
  state.contour.machine_eraser_strokes = state.contour.machine_eraser_strokes || [];
  const size = state.editTool === "eraser" ? state.eraserSize : state.brushSize;
  const canvas = qs("#contourCanvas");
  const rect = canvas?.getBoundingClientRect();
  state.activeStroke = {
    tool: state.editTool,
    size,
    size_ratio: rect?.height ? size / rect.height : undefined,
    shape: state.brushShape,
    color: "rgba(255, 230, 0, 1)",
    points: [currentBrushPoint(event)],
  };
  state.contour.brush_strokes.push(state.activeStroke);
  if (state.editTool === "eraser") {
    state.contour.machine_eraser_strokes.push(state.activeStroke);
  }
  drawContour();
}

function extendBrushStroke(event) {
  if (!state.activeStroke) return;
  const point = currentBrushPoint(event);
  const last = state.activeStroke.points[state.activeStroke.points.length - 1];
  if (!last || Math.hypot(point.x - last.x, point.y - last.y) > 0.0015) {
    state.activeStroke.points.push(point);
    drawContour();
  }
}

function finishBrushStroke() {
  state.activeStroke = null;
}

function updateBrushControls() {
  qs("#brushTool")?.classList.toggle("active", state.editTool === "brush");
  qs("#stickTool")?.classList.toggle("active", state.editTool === "stick");
  qs("#eraserTool")?.classList.toggle("active", state.editTool === "eraser");
  const size = state.editTool === "eraser" ? state.eraserSize : state.brushSize;
  const range = qs("#brushSizeRange");
  const label = qs("#brushSizeLabel");
  if (range) range.value = String(size);
  if (label) label.textContent = state.editTool === "eraser" ? "Толщина ластика" : "Толщина кисти";
  const value = qs("#brushSizeValue");
  if (value) value.textContent = `${size} px`;
  updatePointReadout();
}

function updateFullscreenButton() {
  const button = qs("#fullscreenEditor");
  if (!button) return;
  const editor = qs("[data-page='editor']");
  const isEditorFullscreen = document.fullscreenElement === editor;
  button.textContent = isEditorFullscreen ? "Свернуть" : "На весь экран";
}

function addStickPoint(event) {
  state.contour.brush_strokes = state.contour.brush_strokes || [];
  const point = currentBrushPoint(event);
  if (!state.stickStroke) {
    pushUndoSnapshot();
    const canvas = qs("#contourCanvas");
    const rect = canvas?.getBoundingClientRect();
    state.stickStroke = {
      tool: "brush",
      mode: "stick",
      size: state.brushSize,
      size_ratio: rect?.height ? state.brushSize / rect.height : undefined,
      shape: state.brushShape,
      color: "rgba(255, 230, 0, 1)",
      points: [point],
    };
    state.contour.brush_strokes.push(state.stickStroke);
  } else {
    state.stickStroke.points.push(point);
  }
  drawContour();
}

function finishStickStroke(cancel = false) {
  if (!state.stickStroke) return;
  if (cancel) {
    state.contour.brush_strokes = (state.contour.brush_strokes || []).filter((stroke) => stroke !== state.stickStroke);
  }
  state.stickStroke = null;
  drawContour();
}

async function setupStudyPage() {
  const root = qs("[data-page='study']");
  const image = qs("#studyImage");
  const imageLink = qs("#studyImageLink");
  const showOverlay = qs("#showOverlay");
  const showPreview = qs("#showPreview");
  const showEdited = qs("#showEdited");
  if (!root || !image || !imageLink) return;
  const data = await getJson(`/api/studies/${root.dataset.studyId}`);
  const study = data.study;
  qs("#studyTitle").textContent = study.display_name;
  qs("#studyFileName").textContent = study.display_name;
  qs("#studyContainer").textContent = study.group_name === "Отдельные файлы" ? "отдельный файл" : study.group_name;
  qs("#studyQualityClass").textContent = qualitySummary(study);
  qs("#studyRegion").textContent = study.anatomical_region || "-";
  qs("#studyLaterality").textContent = studyLaterality(study) || "-";
  qs("#studyViolations").textContent = study.violation_type || "Не выявлены";
  qs("#studyTime").textContent = study.time_of_processing ? `${study.time_of_processing} c` : "-";
  const overlayUrl = `/api/studies/${study.id}/overlay`;
  const previewUrl = `/api/studies/${study.id}/preview`;
  const editedUrl = `/api/studies/${study.id}/variant/edited`;
  const hasEdited = hasManualContour(study) || manualContours(data.contours).length > 0;
  if (showEdited) showEdited.hidden = !hasEdited;
  const setStudyImage = (url, mode) => {
    image.src = url;
    imageLink.href = url;
    showOverlay?.classList.toggle("active", mode === "overlay");
    showPreview?.classList.toggle("active", mode === "preview");
    showEdited?.classList.toggle("active", mode === "edited");
  };
  setStudyImage(hasEdited ? editedUrl : overlayUrl, hasEdited ? "edited" : "overlay");
  showOverlay?.addEventListener("click", () => setStudyImage(overlayUrl, "overlay"));
  showPreview?.addEventListener("click", () => setStudyImage(previewUrl, "preview"));
  showEdited?.addEventListener("click", () => setStudyImage(editedUrl, "edited"));
}

async function setupEditor() {
  const root = qs("[data-page='editor']");
  const canvas = qs("#contourCanvas");
  if (!root || !canvas) return;
  const data = await getJson(`/api/studies/${root.dataset.studyId}`);
  const study = data.study;
  state.currentStudy = study;
  const savedPayload = data.contours[0]?.payload;
  const latest = savedPayload?.source === "manual_edit" ? savedPayload : await loadAiContour(study);
  latest.objects = latest.objects?.length ? latest.objects : defaultContourForStudy(study).objects;
  state.contour = latest;
  state.initialContour = cloneContour(latest);
  state.undoStack = [];
  state.redoStack = [];
  state.selectedObjectIndex = 0;
  qs("#editorFileName").textContent = study.display_name;
  qs("#editorStatus").textContent = study.processing_status || study.status;
  qs("#reviewRegion").value = study.anatomical_region || "Проксимальный отдел бедра";
  qs("#reviewLaterality").value = study.metadata?.laterality || "";
  qs("#reviewClass").value = String(study.quality_class ?? 0);
  qs("#reviewStatus").value = study.processing_status || "Success";
  qs("#reviewComment").value = study.metadata?.manual_comment || "";
  qs("#dicomSummary").textContent = [
    study.metadata?.modality,
    study.metadata?.study_description,
  ].filter(Boolean).join(" / ") || "демо";
  renderViolationChecklist(study.violation_type || "");
  updateLateralityField();
  renderContourVersions(study.id, data.contours);
  renderObjectSelect();
  await loadCanvasImage(`/api/studies/${study.id}/editor-image`, canvas)
    .catch(() => loadCanvasImage(`/api/studies/${study.id}/preview`, canvas))
    .catch(() => loadCanvasImage(`/api/studies/${study.id}/overlay`, canvas))
    .catch(() => null);
  fitImageToViewer();
  drawContour();
  setupViewerSettings();
  updateBrushControls();
  updateVariantLink(study.id);
  qs("#variantSelect")?.addEventListener("change", () => updateVariantLink(study.id));
  qs("#cancelReview")?.addEventListener("click", resetEditorChanges);
  qs("#cancelReviewTop")?.addEventListener("click", resetEditorChanges);

  qs("#objectSelect")?.addEventListener("change", (event) => {
    state.selectedObjectIndex = Number(event.target.value);
    setSelectedPoint(null);
    drawContour();
  });

  qs("#reviewRegion").addEventListener("change", () => {
    pushUndoSnapshot();
    state.contour.region = qs("#reviewRegion").value;
    updateLateralityField();
    renderViolationChecklist(collectViolations().join("; "));
    drawContour();
  });

  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    if (event.button === 1 || event.altKey || event.code === "Space") {
      state.isPanning = true;
      state.lastPointer = { clientX: event.clientX, clientY: event.clientY };
      return;
    }
    if (state.editTool === "stick") {
      addStickPoint(event);
      return;
    }
    startBrushStroke(event);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (state.activeStroke) {
      extendBrushStroke(event);
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
    finishBrushStroke();
    state.isPanning = false;
    state.lastPointer = null;
  });
  canvas.addEventListener("pointercancel", () => {
    finishBrushStroke();
    state.isPanning = false;
    state.lastPointer = null;
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const screen = pointerCanvasPosition(event);
    const factor = event.deltaY < 0 ? 1.08 : 1 / 1.08;
    setZoom(state.view.zoom * factor, screen);
  }, { passive: false });
  qs("#zoomIn").addEventListener("click", () => {
    setZoom(state.view.zoom * 1.12);
  });
  qs("#zoomOut").addEventListener("click", () => {
    setZoom(state.view.zoom / 1.12);
  });
  qs("#zoomReset").addEventListener("click", () => {
    fitImageToViewer();
    drawContour();
  });
  qs("#undoEdit")?.addEventListener("click", undoEdit);
  qs("#redoEdit")?.addEventListener("click", redoEdit);
  qs("#brushTool")?.addEventListener("click", () => {
    state.editTool = "brush";
    finishStickStroke();
    updateBrushControls();
  });
  qs("#stickTool")?.addEventListener("click", () => {
    state.editTool = "stick";
    updateBrushControls();
  });
  qs("#eraserTool")?.addEventListener("click", () => {
    state.editTool = "eraser";
    finishStickStroke();
    updateBrushControls();
  });
  qs("#brushSizeRange")?.addEventListener("input", (event) => {
    const size = Number(event.target.value || 6);
    if (state.editTool === "eraser") state.eraserSize = size;
    else state.brushSize = size;
    updateBrushControls();
  });
  qs("#brushShapeSelect")?.addEventListener("change", (event) => {
    state.brushShape = event.target.value;
  });
  qs("#machineOverlayToggle")?.addEventListener("change", (event) => {
    state.showMachineOverlay = event.target.checked;
    drawContour();
  });
  qs("#fullscreenEditor")?.addEventListener("click", () => {
    const editor = qs("[data-page='editor']");
    if (!document.fullscreenElement) editor?.requestFullscreen?.();
    else document.exitFullscreen?.();
    setTimeout(() => {
      fitImageToViewer();
      drawContour();
    }, 120);
  });
  document.addEventListener("fullscreenchange", updateFullscreenButton);
  qs("#resetAiContour")?.addEventListener("click", async () => {
    pushUndoSnapshot();
    state.contour = await loadAiContour(study);
    state.contour.brush_strokes = [];
    state.contour.machine_eraser_strokes = [];
    state.selectedObjectIndex = 0;
    state.selectedPointIndex = null;
    state.activeStroke = null;
    state.stickStroke = null;
    renderObjectSelect();
    drawContour();
  });
  document.addEventListener("keydown", (event) => {
    if (!qs("[data-page='editor']")) return;
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === "z") {
      event.preventDefault();
      if (event.shiftKey) redoEdit();
      else undoEdit();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && key === "c") {
      event.preventDefault();
      undoEdit();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && key === "y") {
      event.preventDefault();
      redoEdit();
      return;
    }
    if (state.stickStroke && event.key === "Enter") {
      event.preventDefault();
      finishStickStroke();
      return;
    }
    if (state.stickStroke && event.key === "Escape") {
      event.preventDefault();
      finishStickStroke(true);
      return;
    }
  });
  const saveHandler = async () => {
    const violations = collectViolations();
    const qualityClass = violations.length ? 1 : Number(qs("#reviewClass").value);
    state.contour.region = qs("#reviewRegion").value;
    state.contour.source = "manual_edit";
    state.contour.saved_at = new Date().toISOString();
    const saveStatus = qs("#saveStatus");
    if (saveStatus) saveStatus.textContent = "Сохранение...";
    await getJson(`/api/studies/${root.dataset.studyId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anatomical_region: qs("#reviewRegion").value,
        quality_class: qualityClass,
        quality_prob: null,
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
    const contourResponse = await getJson(`/api/studies/${root.dataset.studyId}/contours`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.contour),
    });
    state.initialContour = cloneContour(state.contour);
    renderContourVersions(root.dataset.studyId, contourResponse.contours || []);
    qs("#variantSelect").value = "edited";
    updateVariantLink(root.dataset.studyId);
    if (saveStatus) saveStatus.textContent = "Изменения сохранены";
  };
  qs("#saveReview").addEventListener("click", saveHandler);
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
  if (!imageBrightness || !imageContrast) return;
  const savedImageBrightness = localStorage.getItem("imageBrightness");
  const savedImageContrast = localStorage.getItem("imageContrast");
  if (savedImageBrightness) imageBrightness.value = savedImageBrightness;
  if (savedImageContrast) imageContrast.value = savedImageContrast;
  imageBrightness.addEventListener("input", () => {
    localStorage.setItem("imageBrightness", imageBrightness.value);
    drawContour();
  });
  imageContrast.addEventListener("input", () => {
    localStorage.setItem("imageContrast", imageContrast.value);
    drawContour();
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
