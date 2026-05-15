const state = {
  files: [],
  selectedFileId: null,
  selectedFile: null,
  pendingAnalysis: null,
};

const els = {
  tabs: document.querySelectorAll(".tab-button"),
  extractTab: document.querySelector("#extract-tab"),
  upgradesTab: document.querySelector("#upgrades-tab"),
  uploadForm: document.querySelector("#upload-form"),
  uploadMessage: document.querySelector("#upload-message"),
  fileList: document.querySelector("#file-list"),
  fileDetail: document.querySelector("#file-detail"),
  refreshFiles: document.querySelector("#refresh-files"),
  refreshUpgrades: document.querySelector("#refresh-upgrades"),
  upgradesList: document.querySelector("#upgrades-list"),
};

function setMessage(text, kind = "") {
  els.uploadMessage.textContent = text;
  els.uploadMessage.className = `message ${kind}`.trim();
}

function formatConfidence(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function formatDate(value) {
  if (!value) return "";
  return new Date(value).toLocaleString();
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with ${response.status}`);
  }
  return payload;
}

async function loadFiles(selectFirst = false) {
  const payload = await api("/api/files");
  state.files = payload.files;
  renderFileList();
  if (selectFirst && state.files.length > 0) {
    await selectFile(state.files[0].id);
  } else if (state.selectedFileId) {
    const stillExists = state.files.some((file) => file.id === state.selectedFileId);
    if (stillExists) renderFileList();
  }
}

function renderFileList() {
  if (state.files.length === 0) {
    els.fileList.innerHTML = '<div class="empty-state">No OCR files uploaded yet.</div>';
    return;
  }

  els.fileList.innerHTML = "";
  for (const file of state.files) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `file-row ${file.id === state.selectedFileId ? "active" : ""}`;
    button.innerHTML = `
      <span class="file-name"></span>
      <span class="status ${file.status}"></span>
      <span class="file-meta"></span>
    `;
    button.querySelector(".file-name").textContent = file.filename;
    button.querySelector(".status").textContent =
      file.status === "extracted" ? "Extracted" : "Needs review";
    button.querySelector(".file-meta").textContent =
      `${file.line_count} lines · min confidence ${formatConfidence(file.min_confidence)}`;
    button.addEventListener("click", () => selectFile(file.id));
    els.fileList.appendChild(button);
  }
}

async function selectFile(fileId) {
  const payload = await api(`/api/files/${fileId}`);
  state.selectedFileId = fileId;
  state.selectedFile = payload.file;
  state.pendingAnalysis = null;
  renderFileList();
  renderFileDetail(payload.file);
}

function renderFileDetail(file) {
  const minConfidence = Number(file.min_confidence || 0);
  const needsReview = file.status === "needs_review" || minConfidence < 0.97;
  const lines = Array.isArray(file.lines) ? file.lines : [];

  els.fileDetail.className = "";
  els.fileDetail.innerHTML = `
    <div class="detail-header">
      <div>
        <h2 class="detail-title"></h2>
        <div class="detail-meta"></div>
      </div>
      <span class="status ${file.status}"></span>
    </div>
    <div id="review-area"></div>
    <section class="lines-section">
      <h3>Extraction Output</h3>
      <div id="lines-table"></div>
    </section>
    <section class="diagnostics-section">
      <details>
        <summary>Diagnostics</summary>
        <pre id="diagnostics-json"></pre>
      </details>
    </section>
  `;

  els.fileDetail.querySelector(".detail-title").textContent = file.filename;
  els.fileDetail.querySelector(".detail-meta").textContent =
    `${lines.length} lines · minimum confidence ${formatConfidence(minConfidence)} · uploaded ${formatDate(file.uploaded_at)}`;
  els.fileDetail.querySelector(".status").textContent =
    file.status === "extracted" ? "Extracted" : "Needs review";
  els.fileDetail.querySelector("#diagnostics-json").textContent =
    JSON.stringify(file.diagnostics, null, 2);

  const reviewArea = els.fileDetail.querySelector("#review-area");
  if (needsReview) {
    reviewArea.innerHTML = `
      <div class="warning-panel">
        <strong>We could not extract invoice lines for this file</strong>
        <div>The file fell below the 97% confidence threshold.</div>
        <div class="warning-actions">
          <button id="understand-button" type="button" class="primary">Understand why?</button>
        </div>
      </div>
      <div id="analysis-result"></div>
    `;
    reviewArea.querySelector("#understand-button").addEventListener("click", runAnalysis);
  }

  renderLines(lines, els.fileDetail.querySelector("#lines-table"));
}

function renderLines(lines, target) {
  if (!lines.length) {
    target.innerHTML = '<div class="empty-state">No invoice lines were extracted.</div>';
    return;
  }

  target.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Tax</th>
            <th>Item code</th>
            <th>Description</th>
            <th>Qty</th>
            <th>Unit price</th>
            <th>Amount</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  `;

  const tbody = target.querySelector("tbody");
  for (const line of lines) {
    const tr = document.createElement("tr");
    const confidence = Number(line.confidence || 0);
    tr.innerHTML = `
      <td></td>
      <td></td>
      <td></td>
      <td></td>
      <td class="number"></td>
      <td class="number"></td>
      <td class="number"></td>
      <td class="number"></td>
    `;
    const cells = tr.querySelectorAll("td");
    cells[0].textContent = line.line_type || "";
    cells[1].textContent = line.tax_code || "";
    cells[2].textContent = line.item_code || "";
    cells[3].textContent = line.description || "";
    cells[4].textContent = line.quantity || "";
    cells[5].textContent = line.unit_price || "";
    cells[6].textContent = line.amount || "";
    cells[7].textContent = formatConfidence(confidence);
    cells[7].className = `number ${confidence < 0.97 ? "confidence-low" : "confidence-good"}`;
    tbody.appendChild(tr);
  }
}

async function runAnalysis() {
  if (!state.selectedFileId) return;
  const button = document.querySelector("#understand-button");
  const target = document.querySelector("#analysis-result");
  button.disabled = true;
  button.textContent = "Analyzing...";
  target.innerHTML = "";

  try {
    const payload = await api(`/api/files/${state.selectedFileId}/understand`, {
      method: "POST",
    });
    state.pendingAnalysis = payload.analysis;
    renderAnalysis(payload.analysis);
  } catch (error) {
    target.innerHTML = `<div class="error-panel"></div>`;
    target.querySelector(".error-panel").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Understand why?";
  }
}

function renderAnalysis(analysis) {
  const target = document.querySelector("#analysis-result");
  target.innerHTML = `
    <div class="analysis-panel">
      <h3>LLM Analysis</h3>
      <p class="analysis-text" id="llm-response"></p>
      <h3>Fix Proposal</h3>
      <p class="analysis-text" id="fix-proposal"></p>
      <div class="analysis-actions">
        <button id="ignore-analysis" type="button">Ignore</button>
        <button id="save-analysis" type="button" class="primary">Save for future upgrade</button>
      </div>
    </div>
  `;
  target.querySelector("#llm-response").textContent = analysis.llm_response || "";
  target.querySelector("#fix-proposal").textContent = analysis.fix_proposal || "No separate fix proposal returned.";
  target.querySelector("#ignore-analysis").addEventListener("click", () => {
    state.pendingAnalysis = null;
    target.innerHTML = "";
  });
  target.querySelector("#save-analysis").addEventListener("click", saveAnalysis);
}

async function saveAnalysis() {
  if (!state.selectedFileId || !state.pendingAnalysis) return;
  const button = document.querySelector("#save-analysis");
  button.disabled = true;
  button.textContent = "Saving...";
  try {
    await api("/api/future-upgrades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_id: state.selectedFileId,
        llm_response: state.pendingAnalysis.llm_response,
        fix_proposal: state.pendingAnalysis.fix_proposal,
      }),
    });
    state.pendingAnalysis = null;
    await loadUpgrades();
    document.querySelector("#analysis-result").innerHTML =
      '<div class="analysis-panel">Saved for future upgrade.</div>';
  } catch (error) {
    document.querySelector("#analysis-result").insertAdjacentHTML(
      "beforeend",
      `<div class="error-panel"></div>`,
    );
    document.querySelector("#analysis-result .error-panel:last-child").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Save for future upgrade";
  }
}

async function loadUpgrades() {
  const payload = await api("/api/future-upgrades");
  renderUpgrades(payload.upgrades);
}

function renderUpgrades(upgrades) {
  if (!upgrades.length) {
    els.upgradesList.innerHTML = '<div class="empty-state">No saved future upgrades yet.</div>';
    return;
  }

  els.upgradesList.innerHTML = "";
  for (const upgrade of upgrades) {
    const item = document.createElement("article");
    item.className = "upgrade-item";
    item.innerHTML = `
      <h3></h3>
      <div class="file-meta"></div>
      <p class="llm-response"></p>
      <p class="fix-proposal"></p>
    `;
    item.querySelector("h3").textContent = upgrade.filename;
    item.querySelector(".file-meta").textContent = `Saved ${formatDate(upgrade.created_at)}`;
    item.querySelector(".llm-response").textContent = upgrade.llm_response;
    item.querySelector(".fix-proposal").textContent =
      upgrade.fix_proposal ? `Fix proposal: ${upgrade.fix_proposal}` : "Fix proposal: not provided.";
    els.upgradesList.appendChild(item);
  }
}

function switchTab(tabName) {
  for (const button of els.tabs) {
    button.classList.toggle("active", button.dataset.tab === tabName);
  }
  els.extractTab.classList.toggle("active", tabName === "extract");
  els.upgradesTab.classList.toggle("active", tabName === "upgrades");
  if (tabName === "upgrades") {
    loadUpgrades().catch((error) => {
      els.upgradesList.innerHTML = `<div class="error-panel"></div>`;
      els.upgradesList.querySelector(".error-panel").textContent = error.message;
    });
  }
}

els.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(els.uploadForm);
  setMessage("Uploading and extracting...");
  try {
    const payload = await api("/api/files", {
      method: "POST",
      body: formData,
    });
    setMessage("Extraction finished.", "success");
    await loadFiles();
    await selectFile(payload.file.id);
    els.uploadForm.reset();
  } catch (error) {
    setMessage(error.message, "error");
  }
});

els.refreshFiles.addEventListener("click", () => {
  loadFiles().catch((error) => setMessage(error.message, "error"));
});

els.refreshUpgrades.addEventListener("click", () => {
  loadUpgrades().catch((error) => {
    els.upgradesList.innerHTML = `<div class="error-panel"></div>`;
    els.upgradesList.querySelector(".error-panel").textContent = error.message;
  });
});

for (const button of els.tabs) {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
}

loadFiles(true).catch((error) => setMessage(error.message, "error"));
loadUpgrades().catch(() => {});
