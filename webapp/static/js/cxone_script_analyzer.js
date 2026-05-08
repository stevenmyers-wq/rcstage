// webapp/static/js/cxone_script_analyzer.js
document.addEventListener("DOMContentLoaded", async () => {
  const workspace = document.getElementById("cx-workspace");
  if (!workspace) return; // Not on the analyzer tab

  if (
    typeof cytoscape !== "undefined" &&
    typeof cytoscapeDagre !== "undefined"
  ) {
    cytoscape.use(cytoscapeDagre);
  }

  let cxState = { scriptsMap: {} };
  let currentPdfBase64 = null;
  let cxCy = null;
  let hoverPreview = null;

  // Elements
  const folderSelect = document.getElementById("cx-folder");
  const scriptSelect = document.getElementById("cx-script");
  const authorInput = document.getElementById("cx-author");
  const outputArea = document.getElementById("cx-output-area");
  const mdDisplay = document.getElementById("cx-markdown-display");
  const pdfBtn = document.getElementById("cx-download-pdf-btn");

  const loaderWrapper = document.getElementById("cx-global-loader");
  const loaderText = document.getElementById("cx-loading-text");

  // Persistent Error Box Elements
  const errorBox = document.getElementById("cx-error-box");
  const errorText = document.getElementById("cx-error-text");

  // --- Initial Check ---
  try {
    const res = await fetch("/api/cxone/status");
    const data = await res.json();
    if (data.status === "connected") {
      workspace.classList.remove("hidden");
      loadFolders();
    } else {
      workspace.innerHTML = `<div class="p-8 text-center text-gray-500 bg-white rounded-lg border border-gray-200">
              <h3 class="text-xl font-bold text-red-600 mb-2">Not Connected</h3>
              <p>Please connect to RCCC (CXone) in the Authentication menu on the left to use the Script Analyzer.</p>
          </div>`;
      workspace.classList.remove("hidden");
    }
  } catch (e) {
    console.error("Failed to check CXone status", e);
  }

  // --- Loading & Error UI Helpers ---
  function showLoader(message) {
    if (loaderText) loaderText.textContent = message || "Processing...";
    if (loaderWrapper) loaderWrapper.classList.remove("hidden");
  }

  function hideLoader() {
    if (loaderWrapper) loaderWrapper.classList.add("hidden");
  }

  function showError(msg) {
    if (errorBox && errorText) {
      errorText.textContent = msg;
      errorBox.classList.remove("hidden");
      errorBox.scrollIntoView({ behavior: "smooth" });
    }
  }

  function hideError() {
    if (errorBox) errorBox.classList.add("hidden");
  }

  // --- Inner Tab Switching ---
  document.querySelectorAll(".cx-tab-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      document.querySelectorAll(".cx-tab-btn").forEach((b) => {
        b.classList.remove("border-blue-600", "text-blue-600");
        b.classList.add("border-transparent", "text-gray-500");
      });
      e.target.classList.remove("border-transparent", "text-gray-500");
      e.target.classList.add("border-blue-600", "text-blue-600");

      document
        .querySelectorAll(".cx-pane")
        .forEach((p) => p.classList.add("hidden"));
      document
        .getElementById(e.target.dataset.target)
        .classList.remove("hidden");
      hideError();
    });
  });

  // --- 2. Load Folders & Scripts ---
  async function loadFolders() {
    const res = await fetch("/api/cxone/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (data.success && data.folders) {
      folderSelect.innerHTML = data.folders
        .map((f) => `<option value="${f}">${f}</option>`)
        .join("");
      loadScripts();
    } else {
      showError("Failed to fetch folders: " + (data.error || "Unknown Error"));
    }
  }

  folderSelect.addEventListener("change", loadScripts);

  async function loadScripts() {
    scriptSelect.innerHTML = "<option>Loading...</option>";
    const res = await fetch("/api/cxone/scripts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder: folderSelect.value,
      }),
    });
    const data = await res.json();
    scriptSelect.innerHTML = "";
    cxState.scriptsMap = {};

    if (data.success && data.scripts) {
      data.scripts.forEach((s) => {
        const s_id = s.masterID || s.scriptId || s.fileId;
        const s_name = s.scriptName || s.fileName || "Unknown";
        const display = s_name.replace(/\\/g, " / ");
        if (s_id) {
          cxState.scriptsMap[s_id] = { path: s_name, name: display };
          scriptSelect.innerHTML += `<option value="${s_id}">${display} (ID: ${s_id})</option>`;
        }
      });
    } else {
      showError("Failed to fetch scripts: " + (data.error || "Unknown Error"));
    }
  }

  // --- 3. Pane: Compare Versions ---
  let compareHistoryData = [];
  document
    .getElementById("cx-load-history-btn")
    .addEventListener("click", async () => {
      if (scriptSelect.selectedOptions.length !== 1) {
        showError("Please select exactly one script.");
        return;
      }

      hideError();
      showLoader("Fetching script historical versions...");
      const sid = scriptSelect.value;

      try {
        const res = await fetch("/api/cxone/history", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            script_path: cxState.scriptsMap[sid].path,
          }),
        });
        const data = await res.json();
        compareHistoryData = data.history || [];

        if (compareHistoryData.length < 2) {
          showError("Script needs at least 2 versions to compare.");
          return;
        }

        const opts = compareHistoryData
          .map(
            (h) =>
              `<option value="${h.scriptId}">${h.modifyDate} by ${h.modifyUser}</option>`,
          )
          .join("");
        document.getElementById("cx-prev-ver").innerHTML = opts;
        document.getElementById("cx-curr-ver").innerHTML = opts;
        document.getElementById("cx-prev-ver").selectedIndex = 1;

        document.getElementById("compare-selectors").classList.remove("hidden");
        document
          .getElementById("cx-run-compare-btn")
          .classList.remove("hidden");
      } catch (e) {
        console.error(e);
        showError("Failed to fetch history.");
      } finally {
        hideLoader();
      }
    });

  document
    .getElementById("cx-run-compare-btn")
    .addEventListener("click", () => {
      const payload = {
        mode: "compare",
        script_name: cxState.scriptsMap[scriptSelect.value].name,
        prev_id: document.getElementById("cx-prev-ver").value,
        curr_id: document.getElementById("cx-curr-ver").value,
      };
      runAnalysis(payload, document.getElementById("cx-run-compare-btn"));
    });

  // --- 4. Pane: As-Built ---
  document
    .getElementById("cx-run-asbuilt-btn")
    .addEventListener("click", () => {
      if (scriptSelect.selectedOptions.length === 0) {
        showError("Please select at least one script.");
        return;
      }
      const scripts = Array.from(scriptSelect.selectedOptions).map((opt) => ({
        id: opt.value,
        name: cxState.scriptsMap[opt.value].name,
        path: cxState.scriptsMap[opt.value].path,
      }));

      const author = authorInput
        ? authorInput.value.trim() || "[Author Placeholder]"
        : "[Author Placeholder]";

      runAnalysis(
        { mode: "as-built", scripts: scripts, author: author },
        document.getElementById("cx-run-asbuilt-btn"),
      );
    });

  // --- 4b. Pane: Environment Config ---
  document.getElementById("cx-run-config-btn").addEventListener("click", () => {
    runAnalysis(
      { mode: "config" },
      document.getElementById("cx-run-config-btn"),
    );
  });

  // --- 5. Pane: Visualize Script ---
  document
    .getElementById("cx-run-visualize-btn")
    .addEventListener("click", async () => {
      if (scriptSelect.selectedOptions.length !== 1) {
        showError("Please select exactly one script to visualize.");
        return;
      }

      const sid = scriptSelect.value;
      const btn = document.getElementById("cx-run-visualize-btn");
      btn.disabled = true;

      hideError();
      showLoader("Fetching script payload and compiling visual graph...");

      try {
        const histRes = await fetch("/api/cxone/history", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            script_path: cxState.scriptsMap[sid].path,
          }),
        });
        const histData = await histRes.json();

        if (!histData.history || histData.history.length === 0) {
          showError("No version history found to build diagram.");
          return;
        }

        const latestScriptId = histData.history[0].scriptId;

        const res = await fetch("/api/cxone/visualize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            script_id: latestScriptId,
          }),
        });
        const data = await res.json();

        if (data.success && data.graph) {
          const debugWrapper = document.getElementById("cx-debug-wrapper");
          const debugTextarea = document.getElementById("cx-debug-json");
          if (debugWrapper && debugTextarea) {
            debugWrapper.classList.remove("hidden");
            debugTextarea.value =
              data.graph.raw_decoded || "No raw data extracted.";
          }

          if (!data.graph.nodes || data.graph.nodes.length === 0) {
            showError(
              "Warning: Script data fetched, but no valid flowchart actions were found. Check the console.",
            );
            document
              .getElementById("cx-diagram-wrapper")
              .classList.add("hidden");
          } else {
            document
              .getElementById("cx-diagram-wrapper")
              .classList.remove("hidden");
            renderCxGraph(data.graph);
            if (typeof showMessage === "function")
              showMessage(
                `Graph generated successfully! (${data.graph.nodes.length} nodes)`,
                false,
              );
          }
        } else {
          showError(data.error || "Visualization failed.");
        }
      } catch (e) {
        console.error("Visualization Error:", e);
        showError("Error loading diagram.");
      } finally {
        btn.disabled = false;
        hideLoader();
      }
    });

  // --- Hover Tooltip Helpers ---
  function buildTooltipContent(props) {
    if (!props || Object.keys(props).length === 0)
      return '<div class="text-gray-500 italic">No properties available.</div>';
    let html = '<div class="space-y-1">';
    for (const [key, value] of Object.entries(props)) {
      html += `<div><span class="font-bold text-gray-400">${key}:</span> <span class="text-gray-100">${value}</span></div>`;
    }
    html += "</div>";
    return html;
  }

  function showHoverPreview(nodeData, clientX, clientY) {
    removeHoverPreview();
    if (!nodeData.properties) return;

    hoverPreview = document.createElement("div");
    hoverPreview.className =
      "absolute bg-gray-900 border border-gray-700 rounded shadow-2xl z-[9999] p-3 text-xs font-mono text-white max-w-sm max-h-80 overflow-y-auto pointer-events-none opacity-95";
    hoverPreview.innerHTML = buildTooltipContent(nodeData.properties);
    document.body.appendChild(hoverPreview);
    positionNearCursor(hoverPreview, clientX, clientY);
  }

  function moveHoverPreview(clientX, clientY) {
    if (hoverPreview) positionNearCursor(hoverPreview, clientX, clientY);
  }

  function removeHoverPreview() {
    if (hoverPreview) {
      hoverPreview.remove();
      hoverPreview = null;
    }
  }

  function positionNearCursor(el, clientX, clientY) {
    const W = el.offsetWidth;
    const H = el.offsetHeight;
    let x = clientX + 16;
    let y = clientY + 16;

    if (x + W > window.innerWidth) x = clientX - W - 8;
    if (y + H > window.innerHeight) y = clientY - H - 8;

    el.style.left = Math.max(0, x) + "px";
    el.style.top = Math.max(0, y) + "px";
  }

  // --- Graph Rendering ---
  function renderCxGraph(graphData) {
    if (cxCy) cxCy.destroy();

    const style = [
      {
        selector: "node",
        style: {
          shape: "round-rectangle",
          "background-color": "#0ea5e9",
          color: "#ffffff",
          label: "data(label)",
          "text-wrap": "wrap",
          "text-valign": "center",
          "text-halign": "center",
          width: 180,
          height: 60,
          "font-size": "12px",
          "font-family": "Inter, sans-serif",
          "border-width": 2,
          "border-color": "#0284c7",
        },
      },
      {
        selector: 'node[type="begin"]',
        style: { "background-color": "#22c55e", "border-color": "#16a34a" },
      },
      {
        selector: 'node[type="end"]',
        style: { "background-color": "#ef4444", "border-color": "#dc2626" },
      },
      {
        selector: 'node[type="menu"]',
        style: { "background-color": "#8b5cf6", "border-color": "#7c3aed" },
      },
      {
        selector: 'node[type="play"]',
        style: { "background-color": "#f59e0b", "border-color": "#d97706" },
      },
      {
        selector: 'node[type="reqagent"]',
        style: { "background-color": "#10b981", "border-color": "#059669" },
      },
      {
        selector: 'node[type="snippet"]',
        style: { "background-color": "#64748b", "border-color": "#475569" },
      },
      {
        selector: "edge",
        style: {
          width: 2,
          "line-color": "#94a3b8",
          "target-arrow-color": "#94a3b8",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          label: "data(label)",
          "font-size": "10px",
          "font-family": "Inter, sans-serif",
          "text-background-color": "#ffffff",
          "text-background-opacity": 1,
          "text-background-padding": "3px",
          "text-border-color": "#cbd5e1",
          "text-border-width": 1,
          "text-border-opacity": 1,
          color: "#1e293b",
        },
      },
    ];

    cxCy = cytoscape({
      container: document.getElementById("cx-diagram-canvas"),
      elements: graphData,
      style: style,
      layout: {
        name: "dagre",
        rankDir: "TB",
        nodeSep: 60,
        rankSep: 80,
        padding: 50,
      },
      wheelSensitivity: 0.2,
      minZoom: 0.1,
      maxZoom: 3,
    });

    cxCy.on("mouseover", "node", (evt) => {
      const nd = evt.target.data();
      if (nd.properties) {
        showHoverPreview(
          nd,
          evt.originalEvent.clientX,
          evt.originalEvent.clientY,
        );
      }
    });

    cxCy.on("mousemove", "node", (evt) => {
      moveHoverPreview(evt.originalEvent.clientX, evt.originalEvent.clientY);
    });

    cxCy.on("mouseout", "node", () => {
      removeHoverPreview();
    });
  }

  document
    .getElementById("cx-diagram-png-btn")
    .addEventListener("click", () => {
      if (!cxCy) return;
      const pngData = cxCy.png({ scale: 2, bg: "#f8fafc", full: true });
      const link = document.createElement("a");
      link.href = pngData;
      link.download = `CXone_Flowchart_${new Date().getTime()}.png`;
      link.click();
    });

  // --- Core Executor ---
  async function runAnalysis(customPayload, btnElement) {
    const originalText = btnElement.textContent;
    btnElement.disabled = true;
    outputArea.classList.add("hidden");
    hideError();

    showLoader("Analyzing with Gemini AI / Extracting Config...");

    const payload = {
      ...customPayload,
    };

    try {
      const res = await fetch("/api/cxone/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (data.success) {
        currentPdfBase64 = data.pdf_b64;
        mdDisplay.innerHTML = marked.parse(data.markdown);
        outputArea.classList.remove("hidden");
        outputArea.scrollIntoView({ behavior: "smooth" });
        if (typeof showMessage === "function")
          showMessage("Analysis complete!", false);
      } else {
        showError(data.error || "Analysis failed.");
      }
    } catch (e) {
      console.error("Analysis Error:", e);
      showError("Network timeout or error during analysis.");
    } finally {
      btnElement.disabled = false;
      btnElement.textContent = originalText;
      hideLoader();
    }
  }

  // --- PDF Download ---
  pdfBtn.addEventListener("click", () => {
    if (!currentPdfBase64) return;
    const link = document.createElement("a");
    link.href = `data:application/pdf;base64,${currentPdfBase64}`;
    link.download = `CXone_Analysis_${new Date().getTime()}.pdf`;
    link.click();
  });
});
