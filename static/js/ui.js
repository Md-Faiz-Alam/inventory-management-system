(() => {
    "use strict";

    const stack = document.getElementById("toast-stack");

    function showToast(message, level = "success") {
        if (!stack || !message) return;
        const toast = document.createElement("div");
        toast.className = `toast toast-${level}`;
        toast.setAttribute("role", "status");
        toast.textContent = message;
        stack.appendChild(toast);
        setTimeout(() => toast.remove(), 4500);
    }

    document.body.addEventListener("toast", (event) => {
        const detail = event.detail || {};
        showToast(detail.message, detail.level);
    });

    // If the session expired, an HTMX request is redirected to the login page.
    // Go there properly instead of pasting the login form into the page.
    document.body.addEventListener("htmx:beforeSwap", (event) => {
        const responseUrl = (event.detail.xhr && event.detail.xhr.responseURL) || "";
        if (responseUrl.includes("/accounts/login/")) {
            event.detail.shouldSwap = false;
            const next = encodeURIComponent(location.pathname + location.search);
            window.location.href = `/accounts/login/?next=${next}`;
        }
    });

    // HTMX ignores error responses by default; tell the user something failed.
    document.body.addEventListener("htmx:responseError", (event) => {
        const code = event.detail.xhr ? event.detail.xhr.status : "";
        showToast(
            code === 403
                ? "You do not have permission to do that."
                : "Something went wrong. Refresh the page and try again.",
            "error"
        );
    });
    document.body.addEventListener("htmx:sendError", () => {
        showToast("Network problem. Check your connection and try again.", "error");
    });

    // Server-rendered flash messages also fade out after a while.
    document.querySelectorAll(".message-stack").forEach((stackEl) => {
        setTimeout(() => {
            stackEl.style.transition = "opacity 400ms ease";
            stackEl.style.opacity = "0";
            setTimeout(() => stackEl.remove(), 450);
        }, 6000);
    });

    // Confirmation for dangerous buttons.
    document.addEventListener("submit", (event) => {
        const message = event.target.dataset.confirm;
        if (message && !window.confirm(message)) {
            event.preventDefault();
        }
    });

    window.StockFlow = { showToast };
})();
