document.addEventListener("DOMContentLoaded", async () => {
  // ── State ─────────────────────────────────────────────────────────────────
  let sourceMode = "convert"; // 'convert' | 'generate'
  let outputMode = "download"; // 'download' | 'upload'
  let cxConnected = false;
  let cxBuName = "";
  let currentStep = 1;

  // ── Element refs ──────────────────────────────────────────────────────────
  const convertFileInput = document.getElementById("convert-file-input");
  const genFileInput = document.getElementById("gen-file-input");
  const genVoice = document.getElementById("gen-voice");
  const genAccent = document.getElementById("gen-accent");
  const templateBtn = document.getElementById("template-btn");
  const startBtn = document.getElementById("start-btn");
  const startOverBtn = document.getElementById("start-over-btn");

  // ── Step indicator helpers ────────────────────────────────────────────────
  function setStepState(n, state) {
    // state: 'locked' | 'active' | 'done'
    const circle = document.getElementById(`ind-circle-${n}`);
    const label = document.getElementById(`ind-label-${n}`);
    if (!circle || !label) return;

    circle.classList.remove(
      "bg-blue-600",
      "bg-emerald-500",
      "bg-gray-200",
      "text-white",
      "text-gray-400",
    );
    label.classList.remove("text-gray-700", "text-gray-400", "text-gray-500");

    if (state === "active") {
      circle.classList.add("bg-blue-600", "text-white");
      label.classList.add("text-gray-700");
      circle.textContent = n;
    } else if (state === "done") {
      circle.classList.add("bg-emerald-500", "text-white");
      label.classList.add("text-gray-500");
      circle.textContent = "✓";
    } else {
      circle.classList.add("bg-gray-200", "text-gray-400");
      label.classList.add("text-gray-400");
      circle.textContent = n;
    }

    // Connecting line before this step
    const line = document.getElementById(`ind-line-${n - 1}`);
    if (line) {
      line.classList.toggle("bg-emerald-400", state !== "locked");
      line.classList.toggle("bg-gray-200", state === "locked");
    }
  }

  function showStep(n) {
    currentStep = n;

    // Mark previous steps done, current active, future locked
    for (let i = 1; i <= 3; i++) {
      if (i < n) setStepState(i, "done");
      else if (i === n) setStepState(i, "active");
      else setStepState(i, "locked");
    }

    // Show/hide step cards
    [1, 2, 3].forEach((i) => {
      const card = document.getElementById(`step-${i}`);
      if (card) card.classList.toggle("hidden", i > n);
    });
  }

  // ── Step 1: Source mode toggle ────────────────────────────────────────────
  document.querySelectorAll(".src-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      sourceMode = btn.dataset.src;

      document.querySelectorAll(".src-btn").forEach((b) => {
        const active = b.dataset.src === sourceMode;
        b.classList.toggle("bg-blue-600", active);
        b.classList.toggle("text-white", active);
        b.classList.toggle("bg-white", !active);
        b.classList.toggle("text-gray-600", !active);
        b.classList.toggle("hover:bg-gray-50", !active);
      });

      document
        .getElementById("src-convert-panel")
        .classList.toggle("hidden", sourceMode !== "convert");
      document
        .getElementById("src-generate-panel")
        .classList.toggle("hidden", sourceMode !== "generate");

      // Reset step 2+ if source changes
      if (currentStep > 1) {
        showStep(1);
        document.getElementById("step-2").classList.add("hidden");
        document.getElementById("step-3").classList.add("hidden");
      }
      checkStep1();
    });
  });

  function checkStep1() {
    const hasFile =
      sourceMode === "convert"
        ? convertFileInput && convertFileInput.files.length > 0
        : genFileInput && genFileInput.files.length > 0;

    if (hasFile && currentStep === 1) {
      loadStep2();
    }
  }

  if (convertFileInput) convertFileInput.addEventListener("change", checkStep1);
  if (genFileInput) genFileInput.addEventListener("change", checkStep1);

  // ── Step 2: Output mode ───────────────────────────────────────────────────
  async function loadStep2() {
    showStep(2);

    const noCxEl = document.getElementById("step2-no-cx");
    const cxEl = document.getElementById("step2-cx");

    // Validate the CXone session with a live API call — the local session
    // token can exist long after it has expired on NICE's side.
    try {
      const res = await fetch("/api/cxone_audio_converter/validate_cxone");
      const data = await res.json();
      cxConnected = data.valid === true;
      cxBuName = data.bu_name || "";

      if (!cxConnected && data.reason === "expired") {
        // Token expired — update the no-cx message to say so
        const msgEl = noCxEl.querySelector("div");
        if (msgEl)
          msgEl.innerHTML = `
          <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 flex-shrink-0 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M12 2a10 10 0 110 20A10 10 0 0112 2z"/>
          </svg>
          <span>CXone session has expired — files will be downloaded. Please reconnect via the <strong>Authentication</strong> tab to upload directly.</span>`;
      }
    } catch {
      cxConnected = false;
    }

    if (cxConnected) {
      noCxEl.classList.add("hidden");
      cxEl.classList.remove("hidden");
      document.getElementById("step2-cx-badge").textContent =
        `📤 CXone: ${cxBuName || "Connected"}`;
      setOutputMode("download");
    } else {
      cxEl.classList.add("hidden");
      noCxEl.classList.remove("hidden");
      outputMode = "download";
      setTimeout(() => loadStep3(), 300);
    }
  }

  document.querySelectorAll(".out-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      setOutputMode(btn.dataset.out);
      if (currentStep === 2) loadStep3();
    });
  });

  function setOutputMode(mode) {
    outputMode = mode;
    document.querySelectorAll(".out-btn").forEach((b) => {
      const active = b.dataset.out === mode;
      b.classList.toggle("bg-blue-600", active);
      b.classList.toggle("text-white", active);
      b.classList.toggle("bg-white", !active);
      b.classList.toggle("text-gray-600", !active);
      b.classList.toggle("hover:bg-gray-50", !active);
    });
  }

  // ── Step 3: Start ─────────────────────────────────────────────────────────
  function loadStep3() {
    showStep(3);
    updateSummary();

    // Reset results area
    document.getElementById("step3-results").innerHTML = "";
    document.getElementById("step3-results").classList.add("hidden");
    document.getElementById("step3-progress").classList.add("hidden");
    startOverBtn.classList.add("hidden");
    startBtn.disabled = false;
    startBtn.textContent = startLabel();
  }

  function startLabel() {
    if (sourceMode === "convert") {
      return outputMode === "upload"
        ? "Convert & Upload to CXone"
        : "Convert & Download";
    }
    return outputMode === "upload"
      ? "Generate & Upload to CXone"
      : "Generate & Download";
  }

  function updateSummary() {
    const el = document.getElementById("step3-summary");
    if (!el) return;

    let fileDesc = "";
    if (
      sourceMode === "convert" &&
      convertFileInput &&
      convertFileInput.files.length
    ) {
      const n = convertFileInput.files.length;
      fileDesc = `Convert ${n} file${n > 1 ? "s" : ""} to CXone format`;
    } else if (
      sourceMode === "generate" &&
      genFileInput &&
      genFileInput.files.length
    ) {
      fileDesc = `Generate audio from ${genFileInput.files[0].name}`;
    }

    const destDesc =
      outputMode === "upload"
        ? `then upload to CXone (${cxBuName || "connected tenant"})`
        : "then download as ZIP";

    el.textContent = `${fileDesc} — ${destDesc}.`;
  }

  // ── Start button handler ───────────────────────────────────────────────────
  if (startBtn) {
    startBtn.addEventListener("click", () => {
      if (sourceMode === "convert") {
        outputMode === "upload"
          ? runConvertAndUpload()
          : runConvertAndDownload();
      } else {
        outputMode === "upload"
          ? runGenerateAndUpload()
          : runGenerateAndDownload();
      }
    });
  }

  // ── Start over ─────────────────────────────────────────────────────────────
  if (startOverBtn) {
    startOverBtn.addEventListener("click", () => {
      // Reset file inputs
      if (convertFileInput) convertFileInput.value = "";
      if (genFileInput) genFileInput.value = "";

      // Hide steps 2 and 3, reset indicators
      document.getElementById("step-2").classList.add("hidden");
      document.getElementById("step-3").classList.add("hidden");
      document.getElementById("step2-no-cx").classList.add("hidden");
      document.getElementById("step2-cx").classList.add("hidden");

      setStepState(1, "active");
      setStepState(2, "locked");
      setStepState(3, "locked");
      currentStep = 1;

      // Reset output toggle to download
      setOutputMode("download");
    });
  }

  // ── Template download ─────────────────────────────────────────────────────
  if (templateBtn) {
    templateBtn.addEventListener("click", () => {
      const csv = [
        "filename,text,voice,accent",
        "welcome_greeting,Thank you for calling. Please hold while we connect you.,Kore,Australian English",
        "hold_message,Your call is important to us. An agent will be with you shortly.,,",
        "closed_greeting,You have reached us outside of business hours. Our hours are Monday to Friday 9am to 5pm.,,",
      ].join("\n");
      triggerDownload(
        new Blob([csv], { type: "text/csv" }),
        "cxone_audio_template.csv",
      );
    });
  }

  // ── Convert & Download ────────────────────────────────────────────────────
  function runConvertAndDownload() {
    startBtn.disabled = true;
    startBtn.textContent = "Converting...";
    showProgress("blue", "Uploading files...", "0%");

    const formData = new FormData();
    for (const f of convertFileInput.files) formData.append("audio", f);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/cxone_audio_converter/convert", true);
    xhr.responseType = "blob";

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 90);
        setProgress(pct + "%", pct + "%");
        if (e.loaded === e.total) setProgressText("Converting on server...");
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        setProgress("100%", "100%", "green");
        setProgressText("Done!");
        const filename =
          extractFilename(xhr) ||
          (convertFileInput.files.length === 1
            ? `cxone_${convertFileInput.files[0].name.replace(/\.[^.]+$/, "")}.wav`
            : "cxone_converted_audio.zip");
        triggerDownload(xhr.response, filename);
        showMessage("Conversion complete.");
        showResults([{ filename, success: true, note: "Downloaded" }]);
      } else {
        setProgress("100%", "Failed", "red");
        readBlobError(xhr.response);
      }
      finishRun();
    };
    xhr.onerror = () => {
      setProgress("100%", "Error", "red");
      finishRun();
    };
    xhr.send(formData);
  }

  // ── Convert & Upload ──────────────────────────────────────────────────────
  async function runConvertAndUpload() {
    startBtn.disabled = true;
    startBtn.textContent = "Converting & Uploading...";
    showProgress("emerald", "Processing...", "");

    const formData = new FormData();
    for (const f of convertFileInput.files) formData.append("audio", f);

    try {
      const res = await fetch("/api/cxone_audio_converter/convert_and_upload", {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      hideProgress();
      if (!res.ok) {
        showMessage(data.error || "Failed.", true);
      } else {
        showResults(data.results);
        resultMessage(data.success_count, data.total);
        const conflicts = data.results.filter((r) => r.exists);
        if (conflicts.length) showConflictPanel(conflicts);
      }
    } catch {
      hideProgress();
      showMessage("Network error.", true);
    }
    finishRun();
  }

  // ── Generate & Download ───────────────────────────────────────────────────
  function runGenerateAndDownload() {
    const accent = genAccent ? genAccent.value.trim() : "Australian English";
    if (!accent) {
      showMessage("Please enter an accent.", true);
      return;
    }

    startBtn.disabled = true;
    startBtn.textContent = "Generating...";
    showProgress("purple", "Uploading CSV...", "0%");

    const formData = new FormData();
    formData.append("generation_file", genFileInput.files[0]);
    formData.append("voice", genVoice ? genVoice.value : "Kore");
    formData.append("accent", accent);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/cxone_audio_converter/generate", true);
    xhr.responseType = "blob";

    xhr.upload.onloadend = () => {
      setProgress("10%", "");
      setProgressText("Generating audio with AI — this may take a minute...");
      pulseProgress(true);
    };

    xhr.onload = () => {
      pulseProgress(false);
      if (xhr.status >= 200 && xhr.status < 300) {
        setProgress("100%", "100%", "green");
        setProgressText("Done!");
        const filename = extractFilename(xhr) || "cxone_generated_audio.zip";
        triggerDownload(xhr.response, filename);
        showMessage("Generation complete.");
        showResults([{ filename, success: true, note: "Downloaded" }]);
      } else {
        setProgress("100%", "Failed", "red");
        readBlobError(xhr.response);
      }
      finishRun();
    };
    xhr.onerror = () => {
      pulseProgress(false);
      setProgress("100%", "Error", "red");
      finishRun();
    };
    xhr.send(formData);
  }

  // ── Generate & Upload ─────────────────────────────────────────────────────
  async function runGenerateAndUpload() {
    const accent = genAccent ? genAccent.value.trim() : "Australian English";
    if (!accent) {
      showMessage("Please enter an accent.", true);
      return;
    }

    startBtn.disabled = true;
    startBtn.textContent = "Generating & Uploading...";
    showProgress("emerald", "Generating audio and uploading to CXone...", "");
    pulseProgress(true);

    const formData = new FormData();
    formData.append("generation_file", genFileInput.files[0]);
    formData.append("voice", genVoice ? genVoice.value : "Kore");
    formData.append("accent", accent);

    try {
      const res = await fetch(
        "/api/cxone_audio_converter/generate_and_upload",
        { method: "POST", body: formData },
      );
      const data = await res.json();
      pulseProgress(false);
      hideProgress();
      if (!res.ok) {
        showMessage(data.error || "Failed.", true);
      } else {
        showResults(data.results);
        resultMessage(data.success_count, data.total);
        const conflicts = data.results.filter((r) => r.exists);
        if (conflicts.length) showConflictPanel(conflicts);
      }
    } catch {
      pulseProgress(false);
      hideProgress();
      showMessage("Network error.", true);
    }
    finishRun();
  }

  // ── Conflict resolution panel ─────────────────────────────────────────────
  // Shown after an upload run when one or more files already exist in CXone.
  // Lets the user choose which to overwrite before re-uploading.

  function showConflictPanel(conflicts) {
    // Remove any existing panel first
    const existing = document.getElementById("conflict-panel");
    if (existing) existing.remove();

    const panel = document.createElement("div");
    panel.id = "conflict-panel";
    panel.className =
      "mt-4 border border-amber-200 bg-amber-50 rounded-lg p-4 text-sm";

    panel.innerHTML = `
      <p class="font-semibold text-amber-800 mb-3">
        ${conflicts.length} file${conflicts.length > 1 ? "s" : ""} already exist in CXone — select which to overwrite:
      </p>
      <div id="conflict-list" class="space-y-2 mb-4">
        ${conflicts
          .map(
            (c, i) => `
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" class="conflict-cb rounded" data-index="${i}" checked />
            <span class="font-mono text-xs text-gray-700">${c.cx_path}</span>
          </label>`,
          )
          .join("")}
      </div>
      <div class="flex gap-2">
        <button id="conflict-overwrite-btn"
          class="bg-amber-600 hover:bg-amber-700 text-white font-semibold text-xs px-4 py-2 rounded-lg transition duration-150 disabled:opacity-50">
          Overwrite Selected
        </button>
        <button id="conflict-cancel-btn"
          class="bg-white border border-gray-300 text-gray-600 font-medium text-xs px-4 py-2 rounded-lg hover:bg-gray-50 transition duration-150">
          Cancel
        </button>
      </div>`;

    // Append below the results panel
    const resultsEl = document.getElementById("step3-results");
    if (resultsEl) resultsEl.after(panel);

    document
      .getElementById("conflict-cancel-btn")
      .addEventListener("click", () => panel.remove());

    document
      .getElementById("conflict-overwrite-btn")
      .addEventListener("click", async () => {
        const btn = document.getElementById("conflict-overwrite-btn");
        btn.disabled = true;
        btn.textContent = "Overwriting…";

        // Collect checked files
        const selected = [...panel.querySelectorAll(".conflict-cb:checked")]
          .map((cb) => conflicts[parseInt(cb.dataset.index)])
          .map((c) => ({ cx_path: c.cx_path, file_data_b64: c.file_data_b64 }));

        if (!selected.length) {
          btn.disabled = false;
          btn.textContent = "Overwrite Selected";
          return;
        }

        try {
          const res = await fetch("/api/cxone_audio_converter/overwrite", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ files: selected }),
          });
          const data = await res.json();

          if (!res.ok) {
            showMessage(data.error || "Overwrite failed.", true);
            btn.disabled = false;
            btn.textContent = "Overwrite Selected";
            return;
          }

          // Update per-file status rows in the results table
          data.results.forEach((r) => {
            // Find the matching row by cx_path text
            const rows = document.querySelectorAll("#step3-results > div");
            rows.forEach((row) => {
              const pathEl = row.querySelector("p.font-mono");
              if (pathEl && pathEl.textContent.trim() === r.cx_path) {
                const badge = row.querySelector("span.flex-shrink-0");
                if (badge) {
                  badge.textContent = r.success ? "✓" : "✗";
                  badge.className =
                    "flex-shrink-0 text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap " +
                    (r.success
                      ? "bg-emerald-100 text-emerald-700"
                      : "bg-red-100 text-red-700");
                }
                // Remove the "File already exists" error line if overwrite succeeded
                if (r.success) {
                  const errEl = row.querySelector("p.text-red-500");
                  if (errEl) errEl.remove();
                }
              }
            });
          });

          const ok = data.results.filter((r) => r.success).length;
          panel.remove();
          showMessage(
            `${ok} of ${data.total} file${data.total > 1 ? "s" : ""} overwritten.`,
          );
        } catch {
          showMessage("Network error during overwrite.", true);
          btn.disabled = false;
          btn.textContent = "Overwrite Selected";
        }
      });
  }

  // ── Results & progress helpers ────────────────────────────────────────────
  function showProgress(colour, text, pct) {
    const bar = document.getElementById("step3-progress-bar");
    const prog = document.getElementById("step3-progress");
    bar.className = `bg-${colour}-600 h-2.5 rounded-full transition-all duration-300`;
    bar.style.width = pct || "0%";
    setProgressText(text);
    document.getElementById("step3-progress-pct").textContent = pct;
    prog.classList.remove("hidden");
  }

  function setProgress(width, pct, colour) {
    const bar = document.getElementById("step3-progress-bar");
    bar.style.width = width;
    document.getElementById("step3-progress-pct").textContent = pct;
    if (colour) {
      bar.className = `bg-${colour}-600 h-2.5 rounded-full transition-all duration-300`;
    }
  }

  function setProgressText(text) {
    document.getElementById("step3-progress-text").textContent = text;
  }

  function pulseProgress(on) {
    const bar = document.getElementById("step3-progress-bar");
    bar.classList.toggle("animate-pulse", on);
    if (on) bar.style.width = "100%";
  }

  function hideProgress() {
    document.getElementById("step3-progress").classList.add("hidden");
  }

  function showResults(results) {
    const el = document.getElementById("step3-results");
    el.innerHTML = "";
    results.forEach((r) => {
      const row = document.createElement("div");
      row.className =
        "flex items-center justify-between gap-3 py-2 px-3 bg-gray-50 rounded-lg border border-gray-100";
      row.innerHTML = `
        <div class="min-w-0">
          <p class="text-sm font-medium text-gray-700 truncate">${r.filename}</p>
          ${r.cx_path ? `<p class="text-xs text-gray-400 font-mono">${r.cx_path}</p>` : ""}
          ${r.error ? `<p class="text-xs text-red-500 mt-0.5">${r.error}</p>` : ""}
          ${r.note ? `<p class="text-xs text-gray-400">${r.note}</p>` : ""}
        </div>
        <span class="flex-shrink-0 text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap
          ${r.success ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}">
          ${r.success ? "✓" : "✗"}
        </span>`;
      el.appendChild(row);
    });
    el.classList.remove("hidden");
  }

  function resultMessage(ok, total) {
    if (ok === total)
      showMessage(
        `All ${total} file${total === 1 ? "" : "s"} uploaded to CXone.`,
      );
    else
      showMessage(`${ok} of ${total} files uploaded. See results below.`, true);
  }

  function finishRun() {
    startBtn.disabled = false;
    startBtn.textContent = startLabel();
    startOverBtn.classList.remove("hidden");
    setStepState(3, "done");
  }

  // ── Shared helpers ────────────────────────────────────────────────────────
  function extractFilename(xhr) {
    const disp = xhr.getResponseHeader("Content-Disposition");
    if (!disp) return null;
    const m = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/.exec(disp);
    return m && m[1] ? m[1].replace(/['"]/g, "") : null;
  }

  function triggerDownload(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  }

  function readBlobError(blob) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        showMessage(JSON.parse(reader.result).error || "Server error.", true);
      } catch {
        showMessage("Server error.", true);
      }
    };
    reader.readAsText(blob);
  }
});
