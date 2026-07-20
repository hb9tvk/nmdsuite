/* Unsaved-changes guard for the participant report form.
 *
 * The report text and picture captions all belong to #report-form. This
 * flags edits as "dirty", shows an inline "Unsaved changes" hint, and warns
 * before the operator navigates away without saving. Submitting the report
 * form (or the separate picture upload/delete forms) is an intentional
 * navigation, so those clear the guard first.
 */
(function () {
    "use strict";

    function init() {
        const form = document.getElementById("report-form");
        if (!form) return;

        const hint = form.querySelector("[data-unsaved-hint]");
        const message = form.dataset.unsavedWarning || "";
        let dirty = false;

        function markDirty() {
            if (dirty) return;
            dirty = true;
            if (hint) hint.hidden = false;
        }

        function markClean() {
            dirty = false;
            if (hint) hint.hidden = true;
        }

        // Text area and every caption input are attached to this form.
        const fields = document.querySelectorAll(
            '#report-form textarea, #report-form input, [form="report-form"]'
        );
        fields.forEach((field) => {
            field.addEventListener("input", markDirty);
        });

        // Saving the report persists everything — no longer dirty.
        form.addEventListener("submit", markClean);

        // Picture upload/delete are deliberate actions; don't nag on them.
        // (They still reload the page, which would drop unsaved text, but
        // that's a conscious click, not an accidental navigation.)
        document
            .querySelectorAll(".picture-upload, .picture-delete")
            .forEach((f) => f.addEventListener("submit", markClean));

        window.addEventListener("beforeunload", (event) => {
            if (!dirty) return;
            event.preventDefault();
            // Modern browsers show their own generic text; returnValue must
            // be set (non-empty) for the prompt to appear at all.
            event.returnValue = message;
            return message;
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
