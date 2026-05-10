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

    /** Swisstopo approximate formula, WGS84 → LV95. ~1 m precision in CH.
     *  Used to query Swisstopo's height service, which only takes Swiss grid
     *  coordinates. */
    function wgs84ToLV95(lat, lon) {
        const phi = (lat * 3600 - 169_028.66) / 10_000;
        const lam = (lon * 3600 - 26_782.5)   / 10_000;
        const e =
            2_600_072.37
            + 211_455.93 * lam
            - 10_938.51  * lam * phi
            - 0.36       * lam * phi * phi
            - 44.54      * lam * lam * lam;
        const n =
            1_200_147.07
            + 308_807.95 * phi
            + 3_745.25   * lam * lam
            + 76.63      * phi * phi
            - 194.56     * lam * lam * phi
            + 119.79     * phi * phi * phi;
        return { e, n };
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
    const altInput = config.altitudeInputId ? document.getElementById(config.altitudeInputId) : null;
    const cantonInput = config.cantonInputId ? document.getElementById(config.cantonInputId) : null;
    const status = document.getElementById("reg-map-status");
    const altInfo = document.getElementById("altitude-info");

    const MIN_ALTITUDE_M = 800;
    const HEIGHT_DEBOUNCE_MS = 350;
    const CANTON_DEBOUNCE_MS = 350;

    // FSO canton numbering (Bundesamt für Statistik) → ISO 3166-2:CH 2-letter code.
    // Swisstopo's swissBOUNDARIES3D layer returns the FSO number as `ktnr`.
    const CANTON_BY_FSO = {
         1: "ZH",  2: "BE",  3: "LU",  4: "UR",  5: "SZ",  6: "OW",  7: "NW",
         8: "GL",  9: "ZG", 10: "FR", 11: "SO", 12: "BS", 13: "BL", 14: "SH",
        15: "AR", 16: "AI", 17: "SG", 18: "GR", 19: "AG", 20: "TG", 21: "TI",
        22: "VD", 23: "VS", 24: "NE", 25: "GE", 26: "JU",
    };

    let pickedMarker = null;
    let writingFromMap = false;       // suppress coord input-listener loop
    let altitudeFetchTimer = null;
    let altitudeFetchSeq = 0;         // discard stale altitude responses
    let cantonFetchTimer = null;
    let cantonFetchSeq = 0;           // discard stale canton responses
    let userTouchedCanton = false;    // once true, never auto-fill canton

    function setStatus(msg, kind) {
        if (!status) return;
        status.textContent = msg || "";
        status.className = "reg-map-status" + (kind ? " " + kind : "");
    }

    function setAltitudeInfo(msg, kind) {
        if (!altInfo) return;
        altInfo.textContent = msg || "";
        altInfo.className = "altitude-info" + (kind ? " " + kind : "");
    }

    function showAltitude(m) {
        if (!altInput) return;
        altInput.value = String(m);
        if (m < MIN_ALTITUDE_M) {
            setAltitudeInfo(config.warnLowAltitude
                || `Below ${MIN_ALTITUDE_M} m — contest rules require minimum ${MIN_ALTITUDE_M} m a.s.l.`,
                "warn");
        } else {
            setAltitudeInfo(
                (config.altitudeFromSwisstopo || "Altitude from Swisstopo: {n} m")
                    .replace("{n}", m),
                null);
        }
    }

    function fetchAltitude(lat, lon) {
        if (!config.heightApi || !altInput) return;
        if (altitudeFetchTimer) clearTimeout(altitudeFetchTimer);
        altitudeFetchTimer = setTimeout(() => {
            const seq = ++altitudeFetchSeq;
            const lv = wgs84ToLV95(lat, lon);
            const url = `${config.heightApi}?easting=${lv.e}&northing=${lv.n}`;
            fetch(url, { credentials: "omit" })
                .then((r) => r.ok ? r.json() : null)
                .then((data) => {
                    if (seq !== altitudeFetchSeq) return;          // a newer fetch already won
                    if (!data || data.height == null) return;
                    const m = Math.round(parseFloat(data.height));
                    if (!Number.isFinite(m)) return;
                    showAltitude(m);
                })
                .catch(() => { /* non-fatal — server-side validation will reject empty submits */ });
        }, HEIGHT_DEBOUNCE_MS);
    }

    function fetchCanton(lat, lon) {
        if (!config.identifyApi || !cantonInput) return;
        if (userTouchedCanton) return;  // operator overrode the auto-fill
        if (cantonFetchTimer) clearTimeout(cantonFetchTimer);
        cantonFetchTimer = setTimeout(() => {
            const seq = ++cantonFetchSeq;
            const lv = wgs84ToLV95(lat, lon);
            // Tight mapExtent around the click (1 m per pixel) makes tolerance
            // semantics predictable: the point lookup is a true point-in-polygon.
            const m = 100;
            const params = new URLSearchParams({
                layers: "all:ch.swisstopo.swissboundaries3d-kanton-flaeche.fill",
                geometry: `${lv.e},${lv.n}`,
                geometryType: "esriGeometryPoint",
                geometryFormat: "geojson",
                sr: "2056",
                mapExtent: `${lv.e - m},${lv.n - m},${lv.e + m},${lv.n + m}`,
                imageDisplay: "200,200,96",
                tolerance: "5",
                returnGeometry: "false",
            });
            const url = `${config.identifyApi}?${params}`;
            fetch(url, { credentials: "omit" })
                .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
                .then((data) => {
                    if (seq !== cantonFetchSeq) return;
                    if (userTouchedCanton) return;
                    const result = data && data.results && data.results[0];
                    if (!result) {
                        console.warn("[NMD] canton lookup: empty results", data);
                        return;
                    }
                    const attrs = result.attributes || result.properties || {};
                    const code = extractCantonCode(attrs);
                    if (!code) {
                        console.warn("[NMD] canton lookup: no recognizable canton in attrs", attrs);
                        return;
                    }
                    // Setting .value programmatically does not fire 'change'; the
                    // `userTouchedCanton` flag stays false so further auto-fills work.
                    cantonInput.value = code;
                })
                .catch((err) => { console.warn("[NMD] canton lookup failed:", err); });
        }, CANTON_DEBOUNCE_MS);
    }

    /** Try every plausible attribute key from the Swisstopo identify response. */
    function extractCantonCode(attrs) {
        // Common abbreviation keys, lowercased values normalised to upper.
        const abbrKeys = ["kanton", "abbreviation", "ktkz", "ktz", "code", "abbr"];
        for (const k of abbrKeys) {
            const v = attrs[k];
            if (typeof v === "string" && /^[A-Za-z]{2}$/.test(v)) {
                return v.toUpperCase();
            }
        }
        // Numeric FSO-number keys.
        const numKeys = ["ktnr", "kantonsnu", "kantonsnummer", "id", "objectid"];
        for (const k of numKeys) {
            const num = parseInt(attrs[k], 10);
            if (Number.isFinite(num) && CANTON_BY_FSO[num]) return CANTON_BY_FSO[num];
        }
        // Last resort: full name → code mapping for the 26 cantons.
        const name = (attrs.name || attrs.NAME || "").toString().trim().toLowerCase();
        const NAME_BY_CODE = {
            zürich:"ZH", zurich:"ZH", bern:"BE", luzern:"LU", uri:"UR", schwyz:"SZ",
            obwalden:"OW", nidwalden:"NW", glarus:"GL", zug:"ZG", "fribourg":"FR",
            "freiburg":"FR", solothurn:"SO", "basel-stadt":"BS", "basel-landschaft":"BL",
            schaffhausen:"SH", "appenzell ausserrhoden":"AR", "appenzell innerrhoden":"AI",
            "st. gallen":"SG", "sankt gallen":"SG", graubünden:"GR", graubunden:"GR",
            aargau:"AG", thurgau:"TG", ticino:"TI", tessin:"TI", vaud:"VD", waadt:"VD",
            valais:"VS", wallis:"VS", "neuchâtel":"NE", neuenburg:"NE", "genève":"GE",
            "geneve":"GE", genf:"GE", jura:"JU",
        };
        return NAME_BY_CODE[name] || "";
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
                fetchAltitude(p.lat, p.lng);
                fetchCanton(p.lat, p.lng);
            });
        } else {
            pickedMarker.setLatLng(latlng);
        }
        if (options && options.recenter) {
            map.panTo(latlng, { animate: true });
        }
        fetchAltitude(lat, lon);
        fetchCanton(lat, lon);
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

    if (altInput && altInput.value.trim().length > 0) {
        // Form re-rendered with a previous altitude value (e.g. validation error
        // elsewhere); refresh the warning state.
        const v = parseInt(altInput.value, 10);
        if (Number.isFinite(v)) showAltitude(v);
    }

    if (cantonInput) {
        cantonInput.addEventListener("change", () => {
            // Manual selection (programmatic .value sets don't fire 'change').
            userTouchedCanton = !!cantonInput.value;
        });
        // If the form was rerendered with a canton already selected, treat that
        // as user intent — don't auto-overwrite their previous choice.
        if (cantonInput.value) userTouchedCanton = true;
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
