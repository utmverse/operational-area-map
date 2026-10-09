from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: fix_geometry_overlap_ui.py <html>")

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
marker = "<!-- geometry-overlap-ui-fix: 2026-09-21 -->"

pattern = re.compile(
    r'''            const validCenters = definition\?\.type === 'MultiCircle'.*?\n            const markerCenters = definition\?\.type === 'MultiCircle' ''',
    re.S,
)
match = pattern.search(text)
if not match:
    raise SystemExit("Could not locate the generated popup block")

replacement = '''            const validCenters = definition?.type === 'MultiCircle' && Array.isArray(definition.circles)
              ? definition.circles.map((circle, index) => ({
                  type: 'Circle', index,
                  center: circle?.center_point,
                  radiusMeters: circle?.radius_meters
                })).filter(item => item.center && Number.isFinite(Number(item.center.latitude)) && Number.isFinite(Number(item.center.longitude)))
              : [];
            const polygonChildLayers = definition?.type !== 'Circle' && definition?.type !== 'MultiCircle' && typeof layer.getLayers === 'function'
              ? layer.getLayers().filter(child => typeof child.getBounds === 'function')
              : [];
            const distanceSquared = (centerPoint, latlng) => {
              const latScale = 111320;
              const lngScale = Math.cos((Number(centerPoint.latitude) * Math.PI) / 180) * latScale;
              const dy = (Number(centerPoint.latitude) - latlng.lat) * latScale;
              const dx = (Number(centerPoint.longitude) - latlng.lng) * lngScale;
              return (dx * dx) + (dy * dy);
            };
            const selectedCircleIndex = latlng => {
              if (!validCenters.length || !latlng) return null;
              let best = validCenters[0];
              let bestDistance = distanceSquared(best.center, latlng);
              validCenters.slice(1).forEach(candidate => {
                const distance = distanceSquared(candidate.center, latlng);
                if (distance < bestDistance) { best = candidate; bestDistance = distance; }
              });
              return best.index;
            };
            const buildPopup = selected => {
              let popupCenter = selected?.center || null;
              if (!popupCenter && selected?.type === 'Polygon' && polygonChildLayers[selected.index]) {
                const center = polygonChildLayers[selected.index].getBounds().getCenter();
                popupCenter = { latitude: center.lat, longitude: center.lng };
              }
              const centerRow = popupCenter
                ? `<div class="popup-row"><strong>Center:</strong> ${escapeHtml(Number(popupCenter.latitude).toFixed(6))}, ${escapeHtml(Number(popupCenter.longitude).toFixed(6))}</div>`
                : '';
              return {
                html: `<div class="popup-title">${escapeHtml(p.operator_id)} · ${escapeHtml(p.site_id)}</div><div class="popup-row"><strong>Locality:</strong> ${escapeHtml(p.metro_locality || 'Not specified')}</div>${centerRow}${addressRowHtml('Address', popupCenter)}${p.coordination_contact ? `<div class="popup-contact"><strong>Coordination contact:</strong><br>${escapeHtml(p.coordination_contact)}</div>` : ''}`
              };
            };
            layer._oamBuildPopup = buildPopup;
            let activePopupInfo = buildPopup(null);
            layer.bindPopup(activePopupInfo.html);
            const openSelectedPopup = (selected, event) => {
              activePopupInfo = buildPopup(selected);
              layer.bindPopup(activePopupInfo.html, { autoPan: true });
              layer.openPopup(event?.latlng);
            };
            if (definition?.type === 'MultiCircle' && validCenters.length) {
              layer.on('click', event => {
                const index = selectedCircleIndex(event.latlng);
                openSelectedPopup(validCenters.find(item => item.index === index) || null, event);
              });
            } else if (polygonChildLayers.length > 1) {
              polygonChildLayers.forEach((child, index) => {
                child.on('click', event => {
                  openSelectedPopup({ type: 'Polygon', index }, event);
                  if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
                });
              });
            } else if (definition?.type === 'Circle') {
              layer.on('click', event => {
                openSelectedPopup({ type: 'Circle', index: 0, center: definition.center_point }, event);
              });
            }
            const markerCenters = definition?.type === 'MultiCircle' '''

text = text[:match.start()] + replacement + text[match.end():]
text = text.replace(marker, "")
text = text.replace('</head>', f'  {marker}\n</head>', 1)
path.write_text(text, encoding='utf-8')
print(f'Patched {path}')
