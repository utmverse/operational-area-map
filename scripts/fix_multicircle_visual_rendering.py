#!/usr/bin/env python3
"""Render MultiCircle definitions as separate Leaflet circles instead of only the union geometry."""

import sys
from pathlib import Path


MARKER = "<!-- multicircle-visual-rendering-fix: 2026-09-21 -->"
ANCHOR = "        }).addTo(map);\n\n        features.forEach((feature, i) => {"

INJECTION = f'''        }}).addTo(map);\n\n        // MultiCircle is stored as a unioned MultiPolygon for GeoJSON interoperability,\n        // but the map must visually preserve each authoritative circle independently.\n        features.forEach((feature) => {{\n          const definition = feature.properties?.operational_area_definition;\n          if (definition?.type !== 'MultiCircle' || !Array.isArray(definition.circles)) return;\n\n          const featureLayer = layers.getLayers().find((candidate) => candidate.feature === feature);\n          if (!featureLayer) return;\n\n          featureLayer.setStyle({{ opacity: 0, fillOpacity: 0, weight: 0 }});\n          const p = feature.properties || {{}};\n          const overlap = !!p.potential_overlap;\n          const popup = featureLayer.getPopup();\n          const popupContent = popup ? popup.getContent() : null;\n\n          featureLayer._individualCircleLayers = [];\n          definition.circles.forEach((circle, index) => {{\n            const point = circle?.center_point;\n            const radius = circle?.radius_meters;\n            if (!point || typeof point.latitude !== 'number' || typeof point.longitude !== 'number' || typeof radius !== 'number' || radius <= 0) return;\n\n            const circleLayer = L.circle([point.latitude, point.longitude], {{\n              radius,\n              color: overlap ? '#f97316' : '#2563eb',\n              weight: 2.25,\n              opacity: 0.9,\n              fillColor: overlap ? '#fb923c' : '#60a5fa',\n              fillOpacity: 0.12\n            }}).addTo(map);\n\n            // Each circle gets a popup built for its own center. Cloning the\n            // feature-level popup instead would pair circle 1's coordinates with\n            // this circle's looked-up address.\n            const circlePopup = typeof featureLayer._oamBuildPopup === 'function'\n              ? featureLayer._oamBuildPopup({{ type: 'Circle', index, center: point }})\n              : null;\n            const circlePopupContent = (circlePopup && circlePopup.html) || popupContent;\n            if (circlePopupContent) circleLayer.bindPopup(circlePopupContent);\n            bindPopupAddressLookup(circleLayer);\n            featureLayer._individualCircleLayers.push(circleLayer);\n          }});\n        }});\n\n        features.forEach((feature, i) => {{'''


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: fix_multicircle_visual_rendering.py <index.html>")

    path = Path(sys.argv[1])
    text = path.read_text()
    if MARKER in text:
        print("MultiCircle visual rendering fix already present; nothing to do.")
        return
    if ANCHOR not in text:
        raise SystemExit("Could not locate map-layer insertion point")

    text = text.replace(ANCHOR, INJECTION + "\n", 1)
    text = text.replace("</body>", f"{MARKER}\n</body>", 1)
    path.write_text(text)
    print("Applied MultiCircle visual rendering fix.")


if __name__ == "__main__":
    main()
