/* Live total-weight rollup for the station-description form.
 *
 * Sums the per-component weight inputs and updates the footer; flags the
 * total in red when the operator exceeds the 6 kg contest limit.
 */
(function () {
    "use strict";

    const LIMIT_G = 6000;

    function init() {
        const form = document.querySelector(".station-form");
        if (!form) return;
        const display = form.querySelector("#station-total-display");
        const warning = form.querySelector("#station-weight-warning");
        if (!display) return;

        const weightInputs = form.querySelectorAll('input[name^="sta"][name$="gramm"]');

        function recompute() {
            let total = 0;
            weightInputs.forEach((input) => {
                const v = parseInt(input.value, 10);
                if (Number.isFinite(v) && v > 0) total += v;
            });
            display.textContent = total.toLocaleString("de-CH");
            if (total > LIMIT_G) {
                display.classList.add("over-limit");
                if (warning) warning.textContent = warning.dataset.warnText || "";
            } else {
                display.classList.remove("over-limit");
                if (warning) warning.textContent = "";
            }
        }

        weightInputs.forEach((input) => input.addEventListener("input", recompute));
        recompute();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
