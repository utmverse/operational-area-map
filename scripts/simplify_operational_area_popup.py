#!/usr/bin/env python3
"""Keep operational-area popups minimal and make address lookup work for every geometry."""

from pathlib import Path
import sys

MARKER = "<!-- simplified-operational-area-popup: 2026-09-21 -->"
START = "            const buildPopup = selected => {"
END = "            let activePopupInfo = buildPopup(null);"

REPLACEMENT = r'''            const buildPopup = selected => {
              let popupCenter = selected?.center || null;
              if (!popupCenter && selected?.type === 'Circle' && definition?.center_point) {
                popupCenter = definition.center_point;
              }
              if (!popupCenter && selected?.type === 'Polygon' && polygonChildLayers[selected.index]) {
                const center = polygonChildLayers[selected.index].getBounds().getCenter();
                popupCenter = { latitude: center.lat, longitude: center.lng };
              }
              if (!popupCenter && definition?.type === 'MultiCircle' && validCenters.length) {
                popupCenter = validCenters[0].center;
              }
              if (!popupCenter && layer?.getBounds) {
                const bounds = layer.getBounds();
                if (bounds && bounds.isValid()) {
                  const center = bounds.getCenter();
                  popupCenter = { latitude: center.lat, longitude: center.lng };
                }
              }

              const centerRow = popupCenter
                ? `<div class="popup-row"><strong>Center:</strong> ${escapeHtml(Number(popupCenter.latitude).toFixed(6))}, ${escapeHtml(Number(popupCenter.longitude).toFixed(6))}</div>`
                : '';
              const addressLabel = selected?.type === 'Circle'
                ? `Address (Circle ${selected.index + 1} center)`
                : 'Address';

              // The address row carries its own coordinate, so whichever popup ends
              // up open resolves the center it is actually displaying. That removes
              // the stale-id and fading-container races that previously left popups
              // stuck on "Looking up...".
              return {
                html: `<div class="popup-title">${escapeHtml(p.operator_id)} · ${escapeHtml(p.site_id)}</div><div class="popup-row"><strong>Locality:</strong> ${escapeHtml(p.metro_locality || 'Not specified')}</div>${centerRow}${addressRowHtml(addressLabel, popupCenter)}${p.coordination_contact ? `<div class="popup-contact"><strong>Coordination contact:</strong><br>${escapeHtml(p.coordination_contact)}</div>` : ''}`
              };
            };
            layer._oamBuildPopup = buildPopup;
'''


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: simplify_operational_area_popup.py <index.html>")

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("Minimal operational-area popup already present; nothing to do.")
        return

    start = text.find(START)
    end = text.find(END, start)
    if start < 0 or end < 0:
        raise SystemExit("Could not locate generated popup builder")

    text = text[:start] + REPLACEMENT + text[end:]
    text = text.replace("</head>", f"  {MARKER}\n</head>", 1)

    # Guard the two ways this patch chain has broken address lookup before: losing
    # the shared geocoder, and leaving a superseded handler that resolves the
    # address span by an identifier no longer in scope.
    if "reverse-geocode:v3" not in text:
        raise SystemExit("Page is missing the hardened reverse geocoder (reverse-geocode:v3)")
    if "getElementById(addressId)" in text:
        raise SystemExit("Page still contains a superseded address-lookup handler")
    hooks = text.count("bindPopupAddressLookup(")
    if hooks < 3:
        raise SystemExit(f"Expected the shared address-lookup hook to be wired up, found {hooks} references")
    path.write_text(text, encoding="utf-8")
    print("Applied minimal operational-area popup with shared reverse geocoder.")


if __name__ == "__main__":
    main()
