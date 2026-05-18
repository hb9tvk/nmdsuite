// Ranking-page participant map. Drops one marker per participant onto a
// swisstopo-tiled basemap, fits the viewport to the bounding rectangle of
// all markers so the whole roster is visible at once. Read-only — no
// interaction beyond marker popups.
//
// Marker data is injected by the server as JSON in the container's
// data-markers attribute so we never have to round-trip to the backend.
(function () {
    "use strict";

    const el = document.getElementById("ranking-map");
    if (!el || typeof L === "undefined") return;

    const dataEl = document.getElementById("ranking-markers");
    let markers = [];
    if (dataEl) {
        try {
            markers = JSON.parse(dataEl.textContent || "[]");
        } catch (e) {
            markers = [];
        }
    }

    const map = L.map(el, { scrollWheelZoom: false });

    L.tileLayer(
        "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/current/3857/{z}/{x}/{y}.jpeg",
        {
            attribution: '&copy; <a href="https://www.swisstopo.admin.ch">Swisstopo</a>',
            maxZoom: 17,
        }
    ).addTo(map);

    if (markers.length === 0) {
        // Centre on Switzerland; no markers to fit to.
        map.setView([46.8, 8.2], 8);
        return;
    }

    const layer = L.featureGroup();
    markers.forEach(function (m) {
        const popup = [
            "<strong>" + escapeHtml(m.callsign) + "</strong>",
            escapeHtml(m.first_name || ""),
            m.location_text ? escapeHtml(m.location_text) : "",
            m.altitude_m ? (m.altitude_m + " m") : ""
        ].filter(Boolean).join("<br>");
        L.marker([m.lat, m.lon]).bindPopup(popup).addTo(layer);
    });
    layer.addTo(map);
    map.fitBounds(layer.getBounds(), { padding: [20, 20] });

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }
})();
