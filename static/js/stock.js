(() => {
    const form = document.querySelector("[data-stock-form]");

    if (!form) {
        return;
    }

    const input = form.querySelector('[name="quantity"]');
    const preview = form.querySelector("[data-stock-preview]");
    const value = form.querySelector("[data-stock-preview-value]");

    if (!input || !preview || !value) {
        return;
    }

    let available;

    try {
        available = BigInt(form.dataset.stockBalance);
    } catch {
        return;
    }

    const maximum = 9223372036854775807n;
    const formatter = new Intl.NumberFormat("en-IN");

    function updatePreview() {
        const text = input.value.trim();

        if (!text) {
            preview.hidden = true;
            return;
        }

        preview.hidden = false;
        preview.classList.remove("is-invalid");

        if (!/^[0-9]+$/.test(text)) {
            value.textContent = "Enter a positive whole number.";
            preview.classList.add("is-invalid");
            return;
        }

        const quantity = BigInt(text);

        if (quantity <= 0n || quantity > maximum) {
            value.textContent = "Enter a quantity within the supported range.";
            preview.classList.add("is-invalid");
            return;
        }

        const after = form.dataset.stockKind === "receive"
            ? available + quantity
            : available - quantity;

        if (after < 0n) {
            value.textContent = "Quantity exceeds the displayed stock.";
            preview.classList.add("is-invalid");
            return;
        }

        if (after > maximum) {
            value.textContent = "The resulting stock would exceed the limit.";
            preview.classList.add("is-invalid");
            return;
        }

        value.textContent = `${formatter.format(after)} units`;
    }

    input.addEventListener("input", updatePreview);
    updatePreview();
})();