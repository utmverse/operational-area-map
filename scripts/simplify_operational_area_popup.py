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
              const addressId = `address-${L.Util.stamp(layer)}-${selected ? `${selected.type}-${selected.index}` : 'primary'}`;
              const addressLabel = selected?.type === 'Circle'
                ? `Address (Circle ${selected.index + 1} center)`
                : 'Address';

              return {
                addressId,
                center: popupCenter,
                html: `<div class="popup-title">${escapeHtml(p.operator_id)} · ${escapeHtml(p.site_id)}</div><div class="popup-row"><strong>Locality:</strong> ${escapeHtml(p.metro_locality || 'Not specified')}</div>${centerRow}<div class="popup-row"><strong>${addressLabel}:</strong> <span id="${addressId}">Looking up…</span></div>${p.coordination_contact ? `<div class="popup-contact"><strong>Coordination contact:</strong><br>${escapeHtml(p.coordination_contact)}</div>` : ''}`
              };
            };

            // The geometry/popup patcher installs a popup-open hook that calls this
            // helper. Keep the lookup tied to the popup's actual center so polygons,
            // circles, and MultiCircles all use the same reverse-geocoding path.
            const reverseGeocodeForPopup = async popupInfo => {
              // Resolve the span inside the popup that is actually open rather than
              // by document id. Clicking a shape rebinds a fresh popup that reuses the
              // same addressId, and Leaflet keeps the closing popup's container in the
              // DOM for its 200ms fade, so getElementById can return the node that is
              // fading out -- leaving the visible popup stuck on "Looking up...".
              const popupElement = layer.getPopup()?.getElement();
              const element = popupElement
                ? popupElement.querySelector(`[id="${popupInfo?.addressId}"]`)
                  || popupElement.querySelector('[id^="address-"]')
                : null;
              const center = popupInfo?.center;
              if (!element || !center) return;
              const lat = Number(center.latitude);
              const lng = Number(center.longitude);
              if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
                element.textContent = 'Address not available';
                return;
              }
              try {
                element.textContent = await reverseGeocode(lat, lng);
              } catch (error) {
                console.error('Reverse geocoding failed:', error);
                element.textContent = 'Address not available';
              }
            };
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
    path.write_text(text, encoding="utf-8")
    print("Applied minimal operational-area popup with shared reverse geocoder.")


if __name__ == "__main__":
    main()
