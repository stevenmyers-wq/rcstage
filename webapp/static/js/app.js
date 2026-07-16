// webapp/static/js/app.js

// --- Theme Toggle Logic ---
function initTheme() {
    const themeToggleBtn = document.getElementById('theme-toggle');
    const darkIcon = document.getElementById('theme-toggle-dark-icon');
    const lightIcon = document.getElementById('theme-toggle-light-icon');
    const htmlElement = document.documentElement;

    // Check localStorage or system preference
    if (localStorage.getItem('color-theme') === 'dark' || (!('color-theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        htmlElement.classList.add('dark');
        if (lightIcon) lightIcon.classList.remove('hidden');
    } else {
        htmlElement.classList.remove('dark');
        if (darkIcon) darkIcon.classList.remove('hidden');
    }

    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', () => {
            darkIcon.classList.toggle('hidden');
            lightIcon.classList.toggle('hidden');

            if (htmlElement.classList.contains('dark')) {
                htmlElement.classList.remove('dark');
                localStorage.setItem('color-theme', 'light');
            } else {
                htmlElement.classList.add('dark');
                localStorage.setItem('color-theme', 'dark');
            }
        });
    }
}

// ... Keep all your existing app.js code below here (handleLogin, handleRcConnect, etc) ...

// Ensure initTheme is called on load
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  // ... your existing DOMContentLoaded code ...
  const loginForm = document.getElementById("login-form");
  if (document.getElementById("app-dashboard")) {
    checkRcStatus();
    checkCxoneStatus();
  }
});
