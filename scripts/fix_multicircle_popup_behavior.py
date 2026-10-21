from pathlib import Path
import re
import sys


if len(sys.argv) != 2:
    raise SystemExit("Usage: fix_multicircle_popup_behavior.py <html>")

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
marker = "<!-- multicircle-popup-fix: 2026-09-21 -->"

# Always rebuild the generated popup block from the source index.html. This avoids
# layering patches and makes the Pages build deterministic.
pattern = re.compile(
    r'''            const centerRow = center && typeof center\.latitude === 'number'.*?\n            const markerCenters = definition\?\.type === 'MultiCircle' ''',
    re.S,
)
match = pattern.search(text)
if not match:
    raise SystemExit("Could not locate the existing popup/center-marker block in index.html")

replacement = '''            const validCenters = definition?.type === 'MultiCircle' && Array.isArray(definition.circles)
              ? definition.circles.map((circle, index) => ({
                  index,
                  center: circle?.center_point,
                  radiusMeters: circle?.radius_meters
                })).filter(item => item.center && typeof item.center.latitude === 'number' && typeof item.center.longitude === 'number' && typeof item.radiusMeters === 'number')
              : [];
            const primaryCenter = center && typeof center.latitude === 'number' && typeof center.longitude === 'number' ? center : null;
            const distanceSquared = (centerPoint, latlng) => {
              const latScale = 111320;
              const lngScale = Math.cos((centerPoint.latitude * Math.PI) / 180) * latScale;
              const dy = (centerPoint.latitude - latlng.lat) * latScale;
              const dx = (centerPoint.longitude - latlng.lng) * lngScale;
              return (dx * dx) + (dy * dy);
            };
            const selectedCircleIndex = latlng => {
              if (!validCenters.length || !latlng) return null;
              let best = validCenters[0];
              let bestDistance = distanceSquared(best.center, latlng);
              validCenters.slice(1).forEach(candidate => {
                const distance = distanceSquared(candidate.center, latlng);
                if (distance < bestDistance) {
                  best = candidate;
                  bestDistance = distance;
                }
              });
              return best.index;
            };
            const haversineMeters = (a, b) => {
              const earthRadius = 6371008.8;
              const lat1 = Number(a.latitude) * Math.PI / 180;
              const lat2 = Number(b.latitude) * Math.PI / 180;
              const dLat = lat2 - lat1;
              const dLng = (Number(b.longitude) - Number(a.longitude)) * Math.PI / 180;
              const sinLat = Math.sin(dLat / 2);
              const sinLng = Math.sin(dLng / 2);
              const h = sinLat * sinLat + Math.cos(lat1) * Math.cos(lat2) * sinLng * sinLng;
              return earthRadius * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(Math.max(0, 1 - h)));
            };
            const findCircleOverlapPairs = selected => {
              if (!selected || definition?.type !== 'MultiCircle') return [];
              const pairs = [];
              features.forEach(otherFeature => {
                if (otherFeature === feature) return;
                const otherDefinition = otherFeature?.properties?.operational_area_definition;
                const otherCircles = otherDefinition?.type === 'MultiCircle' && Array.isArray(otherDefinition.circles)
                  ? otherDefinition.circles
                  : (otherDefinition?.type === 'Circle' ? [otherDefinition] : []);
                otherCircles.forEach((otherCircle, otherIndex) => {
                  const otherCenter = otherCircle?.center_point;
                  const otherRadius = Number(otherCircle?.radius_meters);
                  if (!otherCenter || !Number.isFinite(otherRadius) || otherRadius < 0) return;
                  const distance = haversineMeters(selected.center, otherCenter);
                  if (distance <= selected.radiusMeters + otherRadius) {
                    const otherProperties = otherFeature.properties || {};
                    pairs.push({
                      operatorId: otherProperties.operator_id || 'Unknown operator',
                      siteId: otherProperties.site_id || 'Unknown area',
                      circleNumber: otherDefinition?.type === 'MultiCircle' ? otherIndex + 1 : 1,
                      distanceMeters: distance
                    });
                  }
                });
              });
              return pairs;
            };
            const buildPopup = selectedIndex => {
              const selected = selectedIndex == null ? null : validCenters.find(item => item.index === selectedIndex);
              const popupCenter = selected?.center || primaryCenter;
              const selectedOverlapPairs = findCircleOverlapPairs(selected);
              const centerRow = popupCenter
                ? `<div class="popup-row"><strong>Center:</strong> ${escapeHtml(Number(popupCenter.latitude).toFixed(6))}, ${escapeHtml(Number(popupCenter.longitude).toFixed(6))}</div>`
                : '';
              const addressLabel = selected ? `Approx. address (Circle ${selected.index + 1} center)` : (definition?.type === 'MultiCircle' ? 'Approx. address (first center)' : 'Address');
              let definitionRow = '';
              if (definition?.type === 'Circle' && typeof definition.radius_meters === 'number') {
                definitionRow = `<div class="popup-row"><strong>Definition:</strong> Circle — radius ${escapeHtml(formatRadius(definition.radius_meters))}</div>`;
              } else if (definition?.type === 'MultiCircle' && Array.isArray(definition.circles)) {
                definitionRow = `<div class="popup-row"><strong>Definition:</strong> MultiCircle — ${definition.circles.length} circle${definition.circles.length === 1 ? '' : 's'}</div>`;
                if (selected) {
                  definitionRow += `<div class="popup-row"><strong>Selected circle:</strong> Circle ${selected.index + 1} — radius ${escapeHtml(formatRadius(selected.radiusMeters))}</div>`;
                  definitionRow += selectedOverlapPairs.length
                    ? `<div class="popup-row"><strong>Circle status:</strong> Potential geographic overlap</div><div class="popup-row"><strong>Overlapping with:</strong> ${selectedOverlapPairs.map(pair => `${escapeHtml(pair.operatorId)} · ${escapeHtml(pair.siteId)} (Circle ${pair.circleNumber})`).join(', ')}</div>`
                    : `<div class="popup-row"><strong>Circle status:</strong> No detected overlap</div>`;
                }
                definition.circles.forEach((circle, index) => {
                  if (!circle?.center_point || typeof circle.radius_meters !== 'number') return;
                  const c = circle.center_point;
                  const selectedStyle = selected && selected.index === index ? ' style="font-weight:700"' : '';
                  definitionRow += `<div class="popup-row"${selectedStyle}><strong>Circle ${index + 1}:</strong> ${escapeHtml(Number(c.latitude).toFixed(6))}, ${escapeHtml(Number(c.longitude).toFixed(6))} — ${escapeHtml(formatRadius(circle.radius_meters))}</div>`;
                });
              }
              const effectiveFrom = p.effective_from || null;
              const effectiveTo = p.effective_to || null;
              const effectiveText = effectiveFrom
                ? `${escapeHtml(effectiveFrom)}${effectiveTo ? ` to ${escapeHtml(effectiveTo)}` : ''}`
                : (effectiveTo ? `Until ${escapeHtml(effectiveTo)}` : 'Not specified');
              const areaStatus = p.potential_overlap ? 'Potential geographic overlap' : 'No detected overlap';
              return {
                html: `<div class="popup-title">${escapeHtml(p.operator_id)} · ${escapeHtml(p.site_id)}</div><div class="popup-row"><strong>Locality:</strong> ${escapeHtml(p.metro_locality || 'Not specified')}</div>${selected ? `<div class="popup-row"><strong>Selected circle:</strong> Circle ${selected.index + 1}</div>` : ''}${centerRow}${addressRowHtml(addressLabel, popupCenter)}${definitionRow}<div class="popup-row"><strong>Operational-area status:</strong> ${areaStatus}</div>${overlapWith.length ? `<div class="popup-row"><strong>Area-level overlap with:</strong> ${overlapWith.map(escapeHtml).join(', ')}</div>` : ''}${p.coordination_contact ? `<div class="popup-contact"><strong>Coordination contact:</strong><br>${escapeHtml(p.coordination_contact)}</div>` : ''}<div class="popup-note">Informational awareness only — not an authorization or coordination determination. Address is an approximate reverse-geocoded location from the ${selected ? `selected Circle ${selected.index + 1} center` : 'center point'}.</div>`
              };
            };
            // Lets the MultiCircle renderer build a popup for one specific circle
            // instead of cloning the feature-level popup, which would show circle 1's
            // center next to circle N's address.
            layer._oamBuildPopup = selected => buildPopup(selected && typeof selected === 'object' ? selected.index : selected);
            let activePopupInfo = buildPopup(null);
            layer.bindPopup(activePopupInfo.html);

            if (definition?.type === 'MultiCircle' && validCenters.length) {
              layer.on('click', event => {
                const selectedIndex = selectedCircleIndex(event.latlng);
                activePopupInfo = buildPopup(selectedIndex);
                layer.bindPopup(activePopupInfo.html, { autoPan: true });
                layer.openPopup(event.latlng);
              });
            }

            const markerCenters = definition?.type === 'MultiCircle' '''

text = text[:match.start()] + replacement + text[match.end():]

# The hardened reverse geocoder now lives in index.html so there is exactly one
# copy of it. Fail loudly rather than silently shipping a page without it.
if "reverse-geocode:v3" not in text:
    raise SystemExit(
        "index.html is missing the hardened reverse geocoder (reverse-geocode:v3)"
    )

text = re.sub(r'\n\s*<!-- multicircle-popup-fix: 2026-09-21 -->', '', text)
text = text.replace('</head>', f'  {marker}\n</head>', 1)
path.write_text(text, encoding="utf-8")
print(f"Patched {path}")