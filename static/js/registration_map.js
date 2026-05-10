/* Registration map: Swisstopo WMTS background, click-to-pick, existing-station pins.
 *
 * Two-way binding with the form:
 *   - Click on map → marker + WGS84 written to the lon/lat input fields.
 *   - Edit the lon/lat fields → if the values parse as WGS84, CH1903, or CH1903+
 *     coordinates inside Switzerland, the marker moves to that location.
 *     If the input parses as a number but is out of bounds, or fails to parse,
 *     a non-blocking warning is shown next to the map and the marker stays put.
 *
 * The bounding-box detection mirrors registration/coords.py. The LV95→WGS84
 * formula is Swisstopo's "approximate formula" (~1 m accuracy within CH);
 * sufficient for a UI marker preview. The server still re-runs pyproj on
 * submit for the persisted canonical values.
 */
(function () {
    "use strict";

    const config = window.NMDMapConfig;
    if (!config) return;

    // --- bounds (mirror registration/coords.py) -----------------------------------------------
    const BOUNDS = {
        LV95:  { eMin: 2_470_000, eMax: 2_860_000, nMin: 1_065_000, nMax: 1_310_000 },
        LV03:  { eMin:   470_000, eMax:   860_000, nMin:    65_000, nMax:   310_000 },
        WGS84: { lonMin: 5.5, lonMax: 11.0, latMin: 45.5, latMax: 48.0 },
    };

    function parseNumber(s) {
        if (s == null) return NaN;
        let t = String(s).trim();
        if (!t) return NaN;
        t = t.replace(/'/g, "").replace(/\s/g, "").replace(/°/g, "").replace(",", ".");
        if (t && /[NnSsEeWw]/.test(t.charAt(t.length - 1))) {
            t = t.slice(0, -1);
        }
        const v = parseFloat(t);
        return Number.isFinite(v) ? v : NaN;
    }

    function inRange(v, min, max) { return v >= min && v <= max; }

    /** Swisstopo approximate formula, LV95 → WGS84. ~1 m precision in CH. */
    function lv95ToWGS84(e, n) {
        const yAux = (e - 2_600_000) / 1_000_000;
        const xAux = (n - 1_200_000) / 1_000_000;
        const lonSec =
            2.6779094
            + 4.728982   * yAux
            + 0.791484   * yAux * xAux
            + 0.1306     * yAux * xAux * xAux
            - 0.0436     * yAux * yAux * yAux;
        const latSec =
            16.9023892
            + 3.238272   * xAux
            - 0.270978   * yAux * yAux
            - 0.002528   * xAux * xAux
            - 0.0447     * yAux * yAux * xAux
            - 0.0140     * xAux * xAux * xAux;
        return { lat: latSec * 100 / 36, lon: lonSec * 100 / 36 };
    }

    /**
     * Detect the coordinate system from the two raw inputs and return
     * { lat, lon } if the location falls inside Switzerland, otherwise:
     *   - { error: "unparseable" } if either value isn't a number
     *   - { error: "out_of_bounds" } if numbers parsed but no system matched
     */
    function detectAndConvert(eRaw, nRaw) {
        const e = parseNumber(eRaw);
        const n = parseNumber(nRaw);
        if (!Number.isFinite(e) || !Number.isFinite(n)) return { error: "unparseable" };

        // CH1903+ (LV95)
        if (inRange(e, BOUNDS.LV95.eMin, BOUNDS.LV95.eMax)
            && inRange(n, BOUNDS.LV95.nMin, BOUNDS.LV95.nMax)) return lv95ToWGS84(e, n);
        if (inRange(n, BOUNDS.LV95.eMin, BOUNDS.LV95.eMax)
            && inRange(e, BOUNDS.LV95.nMin, BOUNDS.LV95.nMax)) return lv95ToWGS84(n, e);

        // CH1903 (LV03) — offset by +2_000_000 / +1_000_000 to reach LV95.
        if (inRange(e, BOUNDS.LV03.eMin, BOUNDS.LV03.eMax)
            && inRange(n, BOUNDS.LV03.nMin, BOUNDS.LV03.nMax))
            return lv95ToWGS84(e + 2_000_000, n + 1_000_000);
        if (inRange(n, BOUNDS.LV03.eMin, BOUNDS.LV03.eMax)
            && inRange(e, BOUNDS.LV03.nMin, BOUNDS.LV03.nMax))
            return lv95ToWGS84(n + 2_000_000, e + 1_000_000);

        // WGS84 decimal degrees — E field = longitude, N field = latitude.
        if (inRange(e, BOUNDS.WGS84.lonMin, BOUNDS.WGS84.lonMax)
            && inRange(n, BOUNDS.WGS84.latMin, BOUNDS.WGS84.latMax))
            return { lat: n, lon: e };
        if (inRange(n, BOUNDS.WGS84.lonMin, BOUNDS.WGS84.lonMax)
            && inRange(e, BOUNDS.WGS84.latMin, BOUNDS.WGS84.latMax))
            return { lat: e, lon: n };

        return { error: "out_of_bounds" };
    }

    // --- map setup ----------------------------------------------------------------------------

    const map = L.map(config.elementId, {
        center: [46.8, 8.2],
        zoom: 8,
        minZoom: 7,
        maxZoom: 17,
    });

    L.tileLayer(
        "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg",
        {
            attribution: '&copy; <a href="https://www.swisstopo.admin.ch">Swisstopo</a>',
            maxZoom: 17,
        }
    ).addTo(map);

    const eInput = document.getElementById(config.eInputId);
    const nInput = document.getElementById(config.nInputId);
    const status = document.getElementById("reg-map-status");

    let pickedMarker = null;
    let writingFromMap = false;  // suppress the input-listener loop

    function setStatus(msg, kind) {
        if (!status) return;
        status.textContent = msg || "";
        status.className = "reg-map-status" + (kind ? " " + kind : "");
    }

    function setMarker(lat, lon, options) {
        const latlng = L.latLng(lat, lon);
        if (pickedMarker === null) {
            pickedMarker = L.marker(latlng, { draggable: true });
            pickedMarker.bindTooltip(config.youLabel || "Your location");
            pickedMarker.addTo(map);
            pickedMarker.on("dragend", () => {
                const p = pickedMarker.getLatLng();
                writeFields(p.lat, p.lng);
            });
        } else {
            pickedMarker.setLatLng(latlng);
        }
        if (options && options.recenter) {
            map.panTo(latlng, { animate: true });
        }
    }

    function writeFields(lat, lon) {
        // The form is labelled "easting / longitude" and "northing / latitude" —
        // the *first* field gets the longitude, the *second* the latitude.
        writingFromMap = true;
        try {
            if (eInput) eInput.value = lon.toFixed(6);
            if (nInput) nInput.value = lat.toFixed(6);
        } finally {
            writingFromMap = false;
        }
        setStatus("", null);
    }

    function syncMapFromFields() {
        if (writingFromMap) return;
        if (!eInput || !nInput) return;
        const eRaw = eInput.value;
        const nRaw = nInput.value;
        if (!eRaw && !nRaw) {
            setStatus("", null);
            return;
        }
        if (!eRaw || !nRaw) return;  // one field still empty — wait

        const result = detectAndConvert(eRaw, nRaw);
        if (result.error === "unparseable") {
            setStatus(config.warnInvalid || "Coordinates could not be parsed.", "warn");
            return;
        }
        if (result.error === "out_of_bounds") {
            setStatus(config.warnOutOfBounds || "Coordinates are outside Switzerland.", "warn");
            return;
        }
        setMarker(result.lat, result.lon, { recenter: true });
        setStatus("", null);
    }

    // --- event wiring -------------------------------------------------------------------------

    map.on("click", (ev) => {
        setMarker(ev.latlng.lat, ev.latlng.lng);
        writeFields(ev.latlng.lat, ev.latlng.lng);
    });

    if (eInput && nInput) {
        // 'input' fires on every keystroke — gives instant feedback as the
        // operator pastes/types. 'change' covers the blur case for completeness.
        eInput.addEventListener("input", syncMapFromFields);
        nInput.addEventListener("input", syncMapFromFields);
        eInput.addEventListener("change", syncMapFromFields);
        nInput.addEventListener("change", syncMapFromFields);
        // Initial pass for forms re-rendered with prior values (validation errors).
        syncMapFromFields();
    }

    // --- existing-registration pins -----------------------------------------------------------

    if (config.registrationsUrl) {
        fetch(config.registrationsUrl, { credentials: "same-origin" })
            .then((r) => r.ok ? r.json() : { participants: [] })
            .then((data) => {
                (data.participants || []).forEach((p) => {
                    const m = L.circleMarker([p.lat, p.lon], {
                        radius: 6,
                        color: "#1f5f3f",
                        fillColor: "#1f5f3f",
                        fillOpacity: 0.7,
                        weight: 1,
                    });
                    const altitude = p.altitude_m ? ` &middot; ${p.altitude_m} m` : "";
                    m.bindPopup(`<strong>${p.callsign}</strong>${altitude}<br>${p.canton || ""}`);
                    m.bindTooltip(p.callsign, { direction: "top", offset: [0, -6] });
                    m.addTo(map);
                });
            })
            .catch(() => { /* non-fatal: form still works without the pins */ });
    }
})();
