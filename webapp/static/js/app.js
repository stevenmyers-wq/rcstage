// webapp/static/js/app.js

// --- Helper Functions (Standard) ---
function showMessage(message, isError = false) {
  const messagesDiv = document.getElementById("message-box");
  if (!messagesDiv) return;

  messagesDiv.classList.remove("hidden");
  messagesDiv.innerHTML = message;
  messagesDiv.className = `fixed top-4 left-1/2 transform -translate-x-1/2 message-box w-96 z-50 p-3 mb-4 rounded-lg text-sm ${isError ? "bg-red-100 text-red-700 border border-red-200" : "bg-green-100 text-green-700 border border-green-200"}`;

  setTimeout(() => {
    messagesDiv.classList.add("hidden");
  }, 5000);
}

// --- 1. LOGIN HANDLER ---
async function handleLogin(event) {
  event.preventDefault();

  const email = document.getElementById("login-email").value;
  const passcode = document.getElementById("login-passcode").value;
  const submitButton = document.getElementById("passcode-submit");

  submitButton.disabled = true;
  submitButton.innerText = "Logging in...";

  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ email, passcode }),
    });

    const data = await response.json();

    if (response.ok && data.status === "success") {
      window.location.href = data.redirect_url;
    } else {
      showMessage(
        data.message || "Login failed due to an unknown error.",
        true,
      );
    }
  } catch (error) {
    console.error("Login request failed:", error);
    showMessage("Network error during login attempt.", true);
  } finally {
    submitButton.disabled = false;
    submitButton.innerText = "Login and Unlock Dashboard";
  }
}

// --- 2. REX (PKCE) CONNECT HANDLER ---
async function handleRcConnect(event) {
  event.preventDefault();

  const clientIdInput = document.getElementById("rc-client-id-input");
  const clientId = clientIdInput ? clientIdInput.value : null;
  const connectButton = document.getElementById("rc-connect-button");

  if (!clientId) {
    showMessage("RingCentral Client ID is required.", true);
    return;
  }

  connectButton.disabled = true;
  connectButton.innerText = "Redirecting for Auth...";

  try {
    const response = await fetch("/auth/initiate-pkce", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId }),
    });

    const data = await response.json();

    if (response.ok && data.redirect_url) {
      window.location.href = data.redirect_url;
    } else {
      showMessage(
        data.message || "Failed to initiate RingCentral authorization.",
        true,
      );
    }
  } catch (error) {
    console.error("PKCE initiation request failed:", error);
    showMessage(
      "Network error during RingCentral authorization attempt.",
      true,
    );
  } finally {
    if (connectButton && connectButton.disabled) {
      connectButton.disabled = false;
      connectButton.innerText = "Connect and Authorize RingCentral Account";
    }
  }
}

// --- 3. REX (PKCE) DISCONNECT HANDLER ---
async function handleRcDisconnect() {
  const disconnectButton = document.getElementById("rc-disconnect-button");

  disconnectButton.disabled = true;
  disconnectButton.innerText = "Disconnecting...";

  try {
    const response = await fetch("/api/rc/disconnect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });

    const data = await response.json();

    if (response.ok && data.status === "success") {
      showMessage(data.message, false);
      checkRcStatus();
    } else {
      showMessage(data.message || "Failed to disconnect RingCentral.", true);
    }
  } catch (error) {
    console.error("Disconnect request failed:", error);
    showMessage("Network error during disconnect attempt.", true);
  } finally {
    disconnectButton.disabled = false;
    disconnectButton.innerText = "Disconnect RingCentral";
  }
}

function updateRcStatusDisplay(status, rcEmail, clientId) {
  const card = document.getElementById("rc-connection-card");
  const statusDisplay = document.getElementById("rc-status-display");
  const rcEmailDisplay = document.getElementById("current-rc-email");

  const rcClientIdInput = document.getElementById("rc-client-id-input");
  const rcConnectButton = document.getElementById("rc-connect-button");
  const rcDisconnectButton = document.getElementById("rc-disconnect-button");

  if (!card || !statusDisplay) return;

  if (status === "connected") {
    card.classList.remove("rc-disconnected");
    card.classList.add("rc-connected");
    statusDisplay.innerHTML = `Status: <span class="text-green-700 font-bold">Connected</span>`;
    rcEmailDisplay.style.display = "block";
    rcEmailDisplay.innerText = `Connected RC User: ${rcEmail} (Client ID: ${clientId ? clientId.substring(0, 8) + "..." : "N/A"})`;

    if (rcDisconnectButton) rcDisconnectButton.style.display = "block";
    if (rcConnectButton) rcConnectButton.style.display = "none";
    if (rcClientIdInput) rcClientIdInput.disabled = true;
  } else {
    card.classList.remove("rc-connected");
    card.classList.add("rc-disconnected");
    statusDisplay.innerHTML = `Status: <span class="text-red-700 font-bold">Disconnected</span>`;
    rcEmailDisplay.style.display = "none";

    if (rcDisconnectButton) rcDisconnectButton.style.display = "none";
    if (rcClientIdInput) rcClientIdInput.value = "";
    if (rcClientIdInput) rcClientIdInput.disabled = false;
    if (rcConnectButton) rcConnectButton.style.display = "block";
  }
}

async function checkRcStatus() {
  try {
    const response = await fetch(`/api/rc/status`);
    if (response.ok) {
      const data = await response.json();
      updateRcStatusDisplay(data.status, data.rc_user_email, data.client_id);
      return data.status === "connected";
    }
  } catch (error) {
    updateRcStatusDisplay("disconnected", "N/A", "N/A");
  }
  return false;
}

// --- 4. CXONE CONNECT HANDLER ---
async function handleCxoneConnect(event) {
  event.preventDefault();
  const regionInput = document.getElementById("cx-region");
  const accessInput = document.getElementById("cx-access");
  const secretInput = document.getElementById("cx-secret");
  const btn = document.getElementById("cxone-connect-button");

  if (!regionInput || !accessInput || !secretInput || !btn) return;

  const region = regionInput.value;
  const access_key = accessInput.value;
  const secret_key = secretInput.value;

  btn.disabled = true;
  btn.innerText = "Connecting...";

  try {
    const response = await fetch("/api/cxone/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ region, access_key, secret_key }),
    });
    const data = await response.json();
    if (response.ok && data.success) {
      showMessage("Connected to CXone!", false);
      checkCxoneStatus();
    } else {
      showMessage(data.error || "Failed to connect to CXone.", true);
    }
  } catch (error) {
    console.error(error);
    showMessage("Network error during CXone connection.", true);
  } finally {
    btn.disabled = false;
    btn.innerText = "Connect to CXone";
  }
}

// --- 5. CXONE DISCONNECT HANDLER ---
async function handleCxoneDisconnect() {
  const btn = document.getElementById("cxone-disconnect-button");
  if (btn) {
    btn.disabled = true;
    btn.innerText = "Disconnecting...";
  }

  try {
    const response = await fetch("/api/cxone/disconnect", { method: "POST" });
    if (response.ok) {
      showMessage("CXone disconnected.", false);
      checkCxoneStatus();
    }
  } catch (e) {
    showMessage("Network error.", true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = "Disconnect CXone";
    }
  }
}

function updateCxoneStatusDisplay(status, region, buName) {
  const card = document.getElementById("cxone-connection-card-status");
  const statusDisplay = document.getElementById("cxone-status-display");
  const regionDisplay = document.getElementById("current-cxone-region");

  const connectBtn = document.getElementById("cxone-connect-button");
  const disconnectBtn = document.getElementById("cxone-disconnect-button");
  const accessInput = document.getElementById("cx-access");
  const secretInput = document.getElementById("cx-secret");

  if (!card || !statusDisplay) return;

  if (status === "connected") {
    card.classList.remove("rc-disconnected", "rc-expired");
    card.classList.add("rc-connected");
    statusDisplay.innerHTML = `Status: <span class="text-green-700 font-bold">Connected</span>`;
    regionDisplay.style.display = "block";
    regionDisplay.innerText = `Region: ${region}${buName ? " | BU: " + buName : ""}`;

    if (disconnectBtn) disconnectBtn.style.display = "block";
    if (connectBtn) connectBtn.style.display = "none";
    if (accessInput) {
      accessInput.disabled = true;
      secretInput.disabled = true;
      accessInput.value = "********";
      secretInput.value = "********";
    }
  } else if (status === "expired") {
    card.classList.remove("rc-connected", "rc-disconnected");
    card.classList.add("rc-expired");
    statusDisplay.innerHTML = `Status: <span class="text-amber-700 font-bold">Session Expired</span>`;
    regionDisplay.style.display = "block";
    regionDisplay.innerText = "Please disconnect and reconnect to continue.";

    if (disconnectBtn) disconnectBtn.style.display = "block";
    if (connectBtn) connectBtn.style.display = "none";
    if (accessInput) {
      accessInput.disabled = true;
      secretInput.disabled = true;
      accessInput.value = "********";
      secretInput.value = "********";
    }
  } else {
    card.classList.remove("rc-connected", "rc-expired");
    card.classList.add("rc-disconnected");
    statusDisplay.innerHTML = `Status: <span class="text-red-700 font-bold">Disconnected</span>`;
    regionDisplay.style.display = "none";

    if (disconnectBtn) disconnectBtn.style.display = "none";
    if (connectBtn) connectBtn.style.display = "block";
    if (accessInput) {
      accessInput.disabled = false;
      secretInput.disabled = false;
      accessInput.value = "";
      secretInput.value = "";
    }
  }
}

// Tab IDs that use CXone — live session validation runs on these tabs only
const CXONE_TOOL_TABS = [
  "cxone_audio_converter",
  "cxone_script_analyzer",
  "auth_cxone",
];

async function checkCxoneStatus() {
  try {
    const res = await fetch("/api/cxone/status");
    if (res.ok) {
      const data = await res.json();

      if (data.status === "connected") {
        // On CXone-dependent tabs, validate the token is still live with NICE.
        // The Flask session can hold a token long after it has expired.
        const currentTab =
          new URLSearchParams(window.location.search).get("tab") || "";
        if (CXONE_TOOL_TABS.includes(currentTab)) {
          try {
            const vRes = await fetch("/api/cxone/validate");
            if (!vRes.ok) {
              // Validate endpoint unreachable or errored — treat as expired
              updateCxoneStatusDisplay("expired", data.region, data.bu_name);
              return false;
            }
            const vData = await vRes.json();
            if (!vData.valid) {
              updateCxoneStatusDisplay("expired", data.region, data.bu_name);
              return false;
            }
          } catch {
            // Network failure during validation — treat as expired to be safe
            updateCxoneStatusDisplay("expired", data.region, data.bu_name);
            return false;
          }
        }
      }

      updateCxoneStatusDisplay(data.status, data.region, data.bu_name);
      return data.status === "connected";
    }
  } catch (e) {
    updateCxoneStatusDisplay("disconnected", "N/A", "");
  }
  return false;
}

// --- Event Listeners and Initialization ---
document.addEventListener("DOMContentLoaded", () => {
  const loginForm = document.getElementById("login-form");
  const rcConnectForm = document.getElementById("rc-connect-form");
  const rcDisconnectButton = document.getElementById("rc-disconnect-button");

  const cxConnectForm = document.getElementById("cxone-connect-form");
  const cxDisconnectBtn = document.getElementById("cxone-disconnect-button");

  if (loginForm) loginForm.addEventListener("submit", handleLogin);
  if (rcConnectForm) rcConnectForm.addEventListener("submit", handleRcConnect);
  if (rcDisconnectButton)
    rcDisconnectButton.addEventListener("click", handleRcDisconnect);

  if (cxConnectForm)
    cxConnectForm.addEventListener("submit", handleCxoneConnect);
  if (cxDisconnectBtn)
    cxDisconnectBtn.addEventListener("click", handleCxoneDisconnect);

  // This part runs on every page load for authenticated users
  if (document.getElementById("app-dashboard")) {
    checkRcStatus();
    checkCxoneStatus();
  }
});
