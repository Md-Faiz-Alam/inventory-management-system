(() => {
    const root = document.documentElement;
    const themeButtons = document.querySelectorAll("[data-theme-toggle]");

    function updateThemeButtons() {
        const dark = root.dataset.theme === "dark";

        themeButtons.forEach((button) => {
            button.textContent = dark ? "Light theme" : "Dark theme";
            button.setAttribute(
                "aria-label",
                dark ? "Switch to light theme" : "Switch to dark theme"
            );
        });
    }

    themeButtons.forEach((button) => {
        button.addEventListener("click", () => {
            const theme = root.dataset.theme === "dark" ? "light" : "dark";
            root.dataset.theme = theme;

            try {
                localStorage.setItem("inventory-theme", theme);
            } catch {
                // Theme switching still works when browser storage is unavailable.
            }

            updateThemeButtons();
        });
    });

    updateThemeButtons();

    const passwordToggle = document.querySelector("[data-password-toggle]");
    const passwordInput = document.getElementById("id_password");

    if (passwordToggle && passwordInput) {
        passwordToggle.hidden = false;

        passwordToggle.addEventListener("click", () => {
            const reveal = passwordInput.type === "password";
            passwordInput.type = reveal ? "text" : "password";
            passwordToggle.textContent = reveal ? "Hide" : "Show";
            passwordToggle.setAttribute("aria-pressed", String(reveal));
        });
    }

    const menuToggle = document.querySelector("[data-menu-toggle]");
    const sidebar = document.getElementById("sidebar");

    if (menuToggle && sidebar) {
        menuToggle.hidden = false;

        function closeMenu() {
            sidebar.classList.remove("is-open");
            menuToggle.setAttribute("aria-expanded", "false");
        }

        menuToggle.addEventListener("click", () => {
            const open = sidebar.classList.toggle("is-open");
            menuToggle.setAttribute("aria-expanded", String(open));
        });

        sidebar.querySelectorAll("a").forEach((link) => {
            link.addEventListener("click", closeMenu);
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && sidebar.classList.contains("is-open")) {
                closeMenu();
                menuToggle.focus();
            }
        });

        document.addEventListener("click", (event) => {
            if (
                !sidebar.contains(event.target) &&
                !menuToggle.contains(event.target)
            ) {
                closeMenu();
            }
        });

        window.matchMedia("(min-width: 901px)").addEventListener("change", closeMenu);
    }
})();