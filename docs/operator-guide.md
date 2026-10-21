# Operator Guide

## Current POC workflow

1. Open the shared map or submission tool.
2. In the submission tool, choose one of the supported drawing options:
   - Polygon
   - Rectangle
   - Circle
   - Upload GeoJSON, KML, or KMZ files (see [Uploading files](#uploading-files))
3. For a polygon or rectangle, draw the operational area directly.
4. For a circle, click and drag from the center point to define the radius.
5. Review the shape, center point, geographic bounds, and radius when applicable.
6. Complete:
   - Operator ID
   - Site / Area ID
   - Metro / Locality
   - Optional coordination contact
   - Optional effective dates
7. Click **Open Prefilled GitHub Submission**. GitHub opens a new Issue with the submission fields pre-populated.
8. Review the submission and click **Create issue**.
9. For a circle, the center point and radius are preserved as the operational-area definition. A GeoJSON polygon approximation is generated for interoperability and overlap analysis.
10. GitHub Actions process and validate the submission.
11. The validated operational area is published to the shared map.

Published records are stored under:

```text
operational-areas/<OPERATOR-ID>/<SITE-ID>.geojson
```

## Uploading files

Use **Choose Files** or drag and drop files onto the upload panel. Supported formats are `.geojson`/`.json`, `.kml`, and `.kmz` (up to 20 MB each), with coordinates in WGS 84 longitude/latitude.

- Select several files at once, or upload in batches. Each upload is added to the operational area, and re-uploading a file with the same name (for example after editing it) replaces its earlier shapes.
- Only Polygon and MultiPolygon shapes are used. Points and lines are ignored and reported.
- All uploaded polygons, plus one drawn polygon or rectangle if present, are merged into a single Polygon or MultiPolygon. Overlaps are dissolved, and the submission processor repairs any remaining invalid ring structure before publication so the area passes validation. In the rare case that the browser cannot merge a particular set of shapes, they are submitted unmerged and merged automatically during processing.
- Uploaded polygons and circles cannot be combined: uploading removes any circles, and adding a circle removes the uploads.
- Each uploaded file is listed with its polygon and vertex counts and can be removed individually. Uploads are paused while the map's edit or delete tool is open.
- Uploads are limited to 5,000 vertices in total. A single GitHub submission can carry roughly 1,800 vertices, so larger areas must be simplified in a GIS tool before uploading.

If an area that includes uploads is too detailed to prefill through a GitHub link, the tool shows **Copy Issue Text** and **Open GitHub Issue** buttons: copy the text, open the issue, replace the description with the copied text, and click **Create**. If the issue was created without the copied text, edit the issue description, paste the text, and save; the submission is processed when the edit is saved.

## Geometry handling

The POC accepts:

- `Polygon`
- `MultiPolygon`

For circular operational areas, the published GeoJSON `geometry` is a polygon approximation, while `properties.operational_area_definition` preserves the circle definition:

```json
{
  "type": "Circle",
  "center_point": {
    "latitude": 37.355,
    "longitude": -121.915
  },
  "radius_meters": 1000
}
```

This allows the map and overlap analysis to use standard GeoJSON while retaining the original center-point-and-radius representation.

## Important

The submission tool is the preferred operator interface. Operators do not need to hand-author GeoJSON.

The map indicates potential geographic overlap for awareness. It does not determine whether coordination is required or authorize an operation.
