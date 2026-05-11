/* Client-side validation for the QSO entry form.
 *
 * Mirrors the server-side validators in portal/qso_validators.py + the
 * per-row validity properties on QsoEntry. Toggles `invalid-input` on each
 * field as the operator types, auto-uppercases callsigns, ignores
 * empty submits, and lets space jump between non-text fields.
 *
 * The view layer is permissive (saves anything), so this script's only job
 * is to guide the operator. The final "submit log" action is what enforces
 * every-field-must-be-valid.
 */
(function () {
    "use strict";

    // --- pure validators (mirror qso_validators.py) -------------------------------------------

    const TEXT_CHARSET = /^[a-z0-9 .\-/?]*$/i;
    const MIN_TEXT_CHARS = 15;

    function isValidUTC(v) {
        v = (v || "").trim();
        if (!/^\d{4}$/.test(v)) return false;
        const h = parseInt(v.slice(0, 2), 10), m = parseInt(v.slice(2), 10);
        return h >= 6 && h <= 9 && m >= 0 && m <= 59;
    }

    function isValidCallsign(v) {
        v = (v || "").trim().toUpperCase();
        return /^[A-Z0-9]+(\/[A-Z0-9]+)?(\/[A-Z]{1,2})?$/.test(v);
    }

    function isValidRST(v) {
        return /^\d{2,3}$/.test((v || "").trim());
    }

    function isTextValid(v) {
        v = v || "";
        if (!v) return true;
        if (!TEXT_CHARSET.test(v)) return false;
        return v.replace(/\s/g, "").length >= MIN_TEXT_CHARS;
    }

    // --- field bindings -----------------------------------------------------------------------

    const VALIDATORS = {
        utc: isValidUTC,
        remote_call: isValidCallsign,
        rsts: isValidRST,
        rstr: isValidRST,
        txts: isTextValid,
        txtr: isTextValid,
    };

    function fieldByName(form, name) {
        return form.querySelector('[name="' + name + '"]');
    }

    function refreshFieldValidity(input) {
        const validator = VALIDATORS[input.name];
        if (!validator) return;
        const value = (input.value || "").trim();
        // Empty value isn't itself invalid; the operator may still be typing.
        if (!value) {
            input.classList.remove("invalid-input");
            return;
        }
        if (validator(value)) {
            input.classList.remove("invalid-input");
        } else {
            input.classList.add("invalid-input");
        }
    }

    function refreshAll(form) {
        Object.keys(VALIDATORS).forEach((name) => {
            const f = fieldByName(form, name);
            if (f) refreshFieldValidity(f);
        });
        // RST-pair consistency: if both filled and lengths differ, flag both.
        const rsts = fieldByName(form, "rsts");
        const rstr = fieldByName(form, "rstr");
        if (rsts && rstr && rsts.value && rstr.value && rsts.value.length !== rstr.value.length) {
            rsts.classList.add("invalid-input");
            rstr.classList.add("invalid-input");
        }
    }

    // --- form-level handlers ------------------------------------------------------------------

    function bindForm(form) {
        if (!form || form.dataset.qsoBound === "1") return;
        form.dataset.qsoBound = "1";

        const fields = ["utc", "remote_call", "rsts", "txts", "rstr", "txtr"]
            .map((n) => fieldByName(form, n))
            .filter(Boolean);

        fields.forEach((input) => {
            input.addEventListener("input", () => {
                if (input.name === "remote_call") {
                    const pos = input.selectionStart;
                    input.value = input.value.toUpperCase();
                    if (pos !== null) input.setSelectionRange(pos, pos);
                }
                refreshFieldValidity(input);
                // Re-flag RST pair consistency on every change.
                const rsts = fieldByName(form, "rsts");
                const rstr = fieldByName(form, "rstr");
                if (rsts && rstr) {
                    if (rsts.value && rstr.value && rsts.value.length !== rstr.value.length) {
                        rsts.classList.add("invalid-input");
                        rstr.classList.add("invalid-input");
                    } else {
                        // Re-evaluate each side individually.
                        refreshFieldValidity(rsts);
                        refreshFieldValidity(rstr);
                    }
                }
            });
        });

        // Space key in non-text fields jumps to the next field — same as the legacy app.
        const navOrder = ["utc", "remote_call", "rsts", "txts", "rstr", "txtr"];
        fields.forEach((input) => {
            input.addEventListener("keydown", (e) => {
                if (e.key !== " ") return;
                if (input.name === "txts" || input.name === "txtr") return;  // text fields keep spaces
                e.preventDefault();
                const idx = navOrder.indexOf(input.name);
                for (let i = idx + 1; i < navOrder.length; i++) {
                    const next = fieldByName(form, navOrder[i]);
                    if (next && !next.disabled) { next.focus(); break; }
                }
            });
        });

        // Empty submit → don't fire HTMX request at all.
        form.addEventListener("htmx:configRequest", (e) => {
            const allEmpty = fields.every((f) => !(f.value || "").trim());
            if (allEmpty) {
                e.preventDefault();
            }
        });

        refreshAll(form);
    }

    // --- attach + re-attach after HTMX swaps --------------------------------------------------

    function bindAll() {
        document.querySelectorAll("#qso-form").forEach(bindForm);
    }

    function focusUTCField() {
        const utc = document.querySelector('#qso-form [name="utc"]');
        if (utc) utc.focus();
    }

    document.addEventListener("DOMContentLoaded", () => {
        bindAll();
        focusUTCField();   // ready for the first QSO
    });
    document.body.addEventListener("htmx:afterSwap", (e) => {
        bindAll();
        // After a save / edit / delete the whole #qso-app gets swapped; put
        // the caret back into UTC so the operator can keep typing without
        // reaching for the mouse.
        if (e.target && e.target.id === "qso-app") {
            focusUTCField();
        }
    });
    document.body.addEventListener("htmx:load", bindAll);
})();
