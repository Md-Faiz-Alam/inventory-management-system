(() => {
    "use strict";

    const config = document.getElementById("dashboard-config");
    if (!config || typeof Chart === "undefined") return;
    const { url } = JSON.parse(config.textContent);
    const charts = {};

    function css(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function palette() {
        return ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5", "--chart-6"].map(css);
    }

    function themeDefaults() {
        Chart.defaults.color = css("--muted");
        Chart.defaults.borderColor = css("--border");
        Chart.defaults.font.family = '"Segoe UI", Arial, sans-serif';
    }

    function empty(canvas, text) {
        const box = canvas.parentElement;
        box.innerHTML = `<div class="chart-empty">${text}</div>`;
    }

    function money(value) {
        return "₹" + Number(value).toLocaleString("en-IN", { maximumFractionDigits: 0 });
    }

    function draw(data) {
        themeDefaults();
        const colors = palette();
        Object.values(charts).forEach((chart) => chart.destroy());

        const movements = document.getElementById("chart-movements");
        if (movements && data.movements) {
            charts.movements = new Chart(movements, {
                type: "line",
                data: {
                    labels: data.movements.labels,
                    datasets: [
                        {
                            label: "Received",
                            data: data.movements.received,
                            borderColor: colors[0],
                            backgroundColor: colors[0] + "33",
                            fill: true,
                            tension: 0.35,
                            pointRadius: 0,
                        },
                        {
                            label: "Issued",
                            data: data.movements.issued,
                            borderColor: colors[1],
                            backgroundColor: colors[1] + "33",
                            fill: true,
                            tension: 0.35,
                            pointRadius: 0,
                        },
                    ],
                },
                options: {
                    maintainAspectRatio: false,
                    interaction: { mode: "index", intersect: false },
                    plugins: { legend: { position: "bottom" } },
                    scales: {
                        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
                        y: { beginAtZero: true, ticks: { precision: 0 } },
                    },
                },
            });
        }

        const health = document.getElementById("chart-health");
        if (health && data.health) {
            if (data.health.values.every((value) => value === 0)) {
                empty(health, "No active products yet.");
            } else {
                charts.health = new Chart(health, {
                    type: "doughnut",
                    data: {
                        labels: data.health.labels,
                        datasets: [
                            {
                                data: data.health.values,
                                backgroundColor: [colors[0], css("--warning"), css("--danger")],
                                borderWidth: 0,
                            },
                        ],
                    },
                    options: {
                        maintainAspectRatio: false,
                        cutout: "68%",
                        plugins: { legend: { position: "bottom" } },
                    },
                });
            }
        }

        const category = document.getElementById("chart-category");
        if (category && data.value_by_category) {
            if (!data.value_by_category.values.some((value) => value > 0)) {
                empty(category, "Receive stock to see its value here.");
            } else {
                charts.category = new Chart(category, {
                    type: "bar",
                    data: {
                        labels: data.value_by_category.labels,
                        datasets: [
                            {
                                label: "Stock value",
                                data: data.value_by_category.values,
                                backgroundColor: colors,
                                borderRadius: 6,
                            },
                        ],
                    },
                    options: {
                        indexAxis: "y",
                        maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            tooltip: { callbacks: { label: (ctx) => money(ctx.parsed.x) } },
                        },
                        scales: {
                            x: { ticks: { callback: (value) => money(value) } },
                            y: { grid: { display: false } },
                        },
                    },
                });
            }
        }

        const top = document.getElementById("chart-top");
        if (top && data.top_issued) {
            if (!data.top_issued.values.length) {
                empty(top, "No stock issued in the last 30 days.");
            } else {
                charts.top = new Chart(top, {
                    type: "bar",
                    data: {
                        labels: data.top_issued.labels,
                        datasets: [
                            {
                                label: "Units issued",
                                data: data.top_issued.values,
                                backgroundColor: colors[2],
                                borderRadius: 6,
                            },
                        ],
                    },
                    options: {
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                            x: { grid: { display: false } },
                            y: { beginAtZero: true, ticks: { precision: 0 } },
                        },
                    },
                });
            }
        }
    }

    let latest = null;

    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then((response) => (response.ok ? response.json() : Promise.reject(response)))
        .then((data) => {
            latest = data;
            draw(data);
        })
        .catch(() => {
            document.querySelectorAll(".chart-box canvas").forEach((canvas) =>
                empty(canvas, "Charts could not be loaded. Refresh to try again.")
            );
        });

    // Redraw with the new palette when the theme changes.
    new MutationObserver(() => latest && draw(latest)).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme"],
    });
})();
