#!/usr/bin/env python3
"""Process a structured operational-area GitHub Issue into a PR."""

import base64
import gzip
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request


API = "https://api.github.com"
TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
EVENT_PATH = os.environ["GITHUB_EVENT_PATH"]


def api(method, path, payload=None):
    url = API + path
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "operational-area-map-submission",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"message": raw}
        return exc.code, detail


def fail(message):
    print(f"::error::{message}")
    raise SystemExit(1)


def clean(value):
    return (value or "").replace("\r", "").strip()


def strip_code_fence(value):
    value = clean(value)
    fence = chr(96) * 3
    if value.startswith(fence):
        value = value[len(fence):].lstrip()
        if value.lower().startswith("json"):
            value = value[4:].lstrip()
        if value.endswith(fence):
            value = value[:-len(fence)].rstrip()
    return value.strip()


def field(body, label, required=False):
    marker = f"### {label}"
    start = body.find(marker)
    if start < 0:
        if required:
            fail(f"Missing required field: {label}")
        return ""

    value_start = start + len(marker)
    next_heading = body.find("\n### ", value_start)
    value = body[value_start:] if next_heading < 0 else body[value_start:next_heading]
    value = value.strip()

    if required and not value:
        fail(f"Missing required field: {label}")
    return value


def stable(value):
    if isinstance(value, list):
        return [stable(item) for item in value]
    if isinstance(value, dict):
        return {key: stable(value[key]) for key in sorted(value)}
    return value


def same_json(left, right):
    return stable(left) == stable(right)


def circle_to_polygon(center_lat, center_lng, radius_meters, segments=64):
    earth_radius = 6371008.8
    angular_distance = radius_meters / earth_radius
    lat1 = math.radians(center_lat)
    lon1 = math.radians(center_lng)
    coordinates = []

    for i in range(segments + 1):
        bearing = 2 * math.pi * i / segments
        lat2 = math.asin(
            math.sin(lat1) * math.cos(angular_distance)
            + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
        )
        lon2 = lon1 + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
            math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
        )
        normalized_lon = (math.degrees(lon2) + 540) % 360 - 180
        coordinates.append([
            round(normalized_lon, 7),
            round(math.degrees(lat2), 7),
        ])

    return {"type": "Polygon", "coordinates": [coordinates]}


def repair_geometry(geometry):
    """Return valid geometry unchanged; repair invalid geometry before publication.

    CI rejects geometry that shapely reports as invalid, which would leave the
    submission PR unmerged. Merged browser uploads can occasionally produce
    ring structures GEOS rejects even though the covered area is correct, and
    when the browser cannot merge uploads at all it submits the shapes
    unmerged (overlapping MultiPolygon parts). Each part is repaired on its
    own and the parts are unioned: make_valid() on the whole MultiPolygon
    would turn overlaps into holes instead of merging them.
    """
    try:
        from shapely.geometry import mapping, shape
        from shapely.ops import unary_union
        from shapely.validation import make_valid
        geom = shape(geometry)
    except Exception as exc:  # shapely unavailable or unparseable: leave it to CI as before.
        print(f"::warning::Skipping geometry repair: {exc}")
        return geometry

    if geom.is_valid:
        return geometry

    polygons = []
    for part in getattr(geom, "geoms", [geom]):
        fixed = make_valid(part)
        polygons.extend(
            piece for piece in getattr(fixed, "geoms", [fixed])
            if piece.geom_type in ("Polygon", "MultiPolygon")
        )
    repaired = unary_union(polygons) if polygons else None
    if (
        repaired is None
        or repaired.is_empty
        or not repaired.is_valid
        or repaired.geom_type not in ("Polygon", "MultiPolygon")
    ):
        fail("Geometry is invalid and could not be repaired automatically.")
    print("::warning::Submitted geometry was invalid and has been repaired with make_valid().")
    return json.loads(json.dumps(mapping(repaired)))


def safe_filename(value):
    value = re.sub(r"[^A-Za-z0-9._-]", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:80]


def comment(issue_number, message):
    status, response = api(
        "POST",
        f"/repos/{REPOSITORY}/issues/{issue_number}/comments",
        {"body": message},
    )
    if status not in (200, 201):
        fail(f"Unable to comment on Issue #{issue_number}: {response}")


def close_issue(issue_number):
    status, response = api(
        "PATCH",
        f"/repos/{REPOSITORY}/issues/{issue_number}",
        {"state": "closed", "state_reason": "completed"},
    )
    if status != 200:
        fail(f"Unable to close Issue #{issue_number}: {response}")


def get_file(path, ref):
    encoded_path = urllib.parse.quote(path, safe="/")
    encoded_ref = urllib.parse.quote(ref, safe="")
    status, response = api(
        "GET",
        f"/repos/{REPOSITORY}/contents/{encoded_path}?ref={encoded_ref}",
    )
    if status == 404:
        return None
    if status != 200:
        fail(f"Unable to read {path} from {ref}: {response}")
    if isinstance(response, list):
        fail(f"Expected {path} to be a file, but GitHub returned a directory.")
    return response


def main():
    with open(EVENT_PATH, "r", encoding="utf-8") as handle:
        event = json.load(handle)

    issue = event.get("issue") or {}
    issue_number = int(issue["number"])
    issue_url = issue.get("html_url", "")
    body = issue.get("body") or ""

    payload_match = re.search(r'<!--\s*OAM(1|2):([A-Za-z0-9_-]+)\s*-->', body)
    payload = None
    if payload_match:
        version = payload_match.group(1)
        token = payload_match.group(2)
        try:
            padded = token + ('=' * (-len(token) % 4))
            encoded = base64.urlsafe_b64decode(padded.encode('ascii'))
            if version == '1':
                decoded = gzip.decompress(encoded).decode('utf-8')
            else:
                decoded = encoded.decode('utf-8')
            payload = json.loads(decoded)
        except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            fail(f'Embedded submission payload is invalid: {exc}')

    if payload is not None:
        if payload.get('version') != 1:
            fail('Unsupported embedded submission payload version.')
        if payload.get('confirm_minimum_info') is not True or payload.get('authorized') is not True:
            fail('Submission confirmation requirements were not satisfied.')

        operator_id = clean(payload.get('operator_id'))
        site_id = clean(payload.get('site_id'))
        metro = clean(payload.get('metro_locality'))
        geometry = payload.get('geometry')
        operational_area_definition = payload.get('operational_area_definition')
        center_raw = clean(payload.get('center_point')) if isinstance(payload.get('center_point'), str) else ''
        bounds = payload.get('geographic_bounds')
        contact_name = clean(payload.get('coordination_contact_name'))
        contact_email = clean(payload.get('coordination_contact_email'))
        contact_phone = clean(payload.get('coordination_contact_phone'))
        effective_from = clean(payload.get('effective_from'))
        effective_to = clean(payload.get('effective_to'))
    else:
        operator_id = clean(field(body, 'Operator ID', required=True))
        site_id = clean(field(body, 'Site / Area ID', required=True))
        metro = clean(field(body, 'Metro / Locality', required=True))
        geometry_raw = strip_code_fence(field(body, 'Operational Area Geometry'))
        center_raw = clean(field(body, 'Center Point (optional)'))
        bounds_raw = strip_code_fence(field(body, 'Geographic Bounds (optional)'))
        definition_raw = strip_code_fence(field(body, 'Operational Area Definition (optional)'))
        contact_name = clean(field(body, 'Coordination Contact Name (optional)'))
        contact_email = clean(field(body, 'Coordination Contact Email (optional)'))
        contact_phone = clean(field(body, 'Coordination Contact Phone (optional)'))
        effective_from = clean(field(body, 'Effective From (optional)'))
        effective_to = clean(field(body, 'Effective To (optional)'))

        geometry = None
        if geometry_raw:
            try:
                geometry = json.loads(geometry_raw)
            except json.JSONDecodeError:
                fail('Operational Area Geometry is not valid JSON.')

            if (
                not isinstance(geometry, dict)
                or geometry.get('type') not in ('Polygon', 'MultiPolygon')
                or not isinstance(geometry.get('coordinates'), list)
            ):
                fail('Geometry must be a GeoJSON Polygon or MultiPolygon.')

        bounds = None
        if bounds_raw:
            try:
                bounds = json.loads(bounds_raw)
            except json.JSONDecodeError:
                fail('Geographic Bounds must be valid JSON.')

        operational_area_definition = None
        if definition_raw:
            try:
                operational_area_definition = json.loads(definition_raw)
            except json.JSONDecodeError:
                fail('Operational Area Definition must be valid JSON.')

    if not operator_id or not site_id or not metro:
        fail('Operator ID, Site / Area ID, and Metro / Locality are required.')

    if payload is not None:
        if geometry is not None and (
            not isinstance(geometry, dict)
            or geometry.get('type') not in ('Polygon', 'MultiPolygon')
            or not isinstance(geometry.get('coordinates'), list)
        ):
            fail('Embedded geometry must be a GeoJSON Polygon or MultiPolygon.')

        if operational_area_definition is not None:
            if not isinstance(operational_area_definition, dict):
                fail('Embedded operational area definition must be a JSON object.')
            if operational_area_definition.get('type') != 'Circle':
                fail('Embedded operational area definition type must be Circle.')
            definition_center = operational_area_definition.get('center_point')
            radius_meters = operational_area_definition.get('radius_meters')
            if (
                not isinstance(definition_center, dict)
                or not isinstance(definition_center.get('latitude'), (int, float))
                or not isinstance(definition_center.get('longitude'), (int, float))
            ):
                fail('Circle definition must include a valid center_point.')
            if (
                definition_center['latitude'] < -90
                or definition_center['latitude'] > 90
                or definition_center['longitude'] < -180
                or definition_center['longitude'] > 180
            ):
                fail('Circle center_point is outside valid latitude/longitude ranges.')
            if (
                not isinstance(radius_meters, (int, float))
                or isinstance(radius_meters, bool)
                or radius_meters <= 0
                or radius_meters > 20000000
            ):
                fail('Circle radius_meters must be greater than 0 and no more than 20000000.')
            center_point = {
                'latitude': float(definition_center['latitude']),
                'longitude': float(definition_center['longitude']),
            }
            geometry = circle_to_polygon(
                center_point['latitude'], center_point['longitude'], float(radius_meters)
            )
            operational_area_definition = {
                'type': 'Circle',
                'center_point': center_point,
                'radius_meters': round(float(radius_meters), 2),
            }
        else:
            center_point = None
            if center_raw:
                parts = [part.strip() for part in center_raw.split(',')]
                if len(parts) == 2:
                    try:
                        center_point = {'latitude': float(parts[0]), 'longitude': float(parts[1])}
                    except ValueError:
                        fail('Center Point must be latitude,longitude.')
            if center_point is not None and geometry is not None:
                pass
    else:
        center_point = None
        if center_raw:
            parts = [part.strip() for part in center_raw.split(',')]
            if len(parts) != 2:
                fail('Center Point must be latitude,longitude.')
            try:
                center_point = {'latitude': float(parts[0]), 'longitude': float(parts[1])}
            except ValueError:
                fail('Center Point must be latitude,longitude.')

        if operational_area_definition:
            if not isinstance(operational_area_definition, dict):
                fail('Operational Area Definition must be a JSON object.')
            if operational_area_definition.get('type') != 'Circle':
                fail('Operational Area Definition type must be Circle.')
            definition_center = operational_area_definition.get('center_point')
            radius_meters = operational_area_definition.get('radius_meters')
            if (
                not isinstance(definition_center, dict)
                or not isinstance(definition_center.get('latitude'), (int, float))
                or not isinstance(definition_center.get('longitude'), (int, float))
            ):
                fail('Circle definition must include a valid center_point.')
            if (
                definition_center['latitude'] < -90
                or definition_center['latitude'] > 90
                or definition_center['longitude'] < -180
                or definition_center['longitude'] > 180
            ):
                fail('Circle center_point is outside valid latitude/longitude ranges.')
            if (
                not isinstance(radius_meters, (int, float))
                or isinstance(radius_meters, bool)
                or radius_meters <= 0
                or radius_meters > 20000000
            ):
                fail('Circle radius_meters must be greater than 0 and no more than 20000000.')
            if center_point is not None and (
                abs(center_point['latitude'] - definition_center['latitude']) > 1e-5
                or abs(center_point['longitude'] - definition_center['longitude']) > 1e-5
            ):
                fail('Center Point does not match the circle definition center_point.')
            center_point = {
                'latitude': float(definition_center['latitude']),
                'longitude': float(definition_center['longitude']),
            }
            geometry = circle_to_polygon(
                center_point['latitude'], center_point['longitude'], float(radius_meters)
            )
            operational_area_definition = {
                'type': 'Circle',
                'center_point': center_point,
                'radius_meters': round(float(radius_meters), 2),
            }

    if geometry is None:
        fail('Provide Operational Area Geometry or a Circle Operational Area Definition.')
    geometry = repair_geometry(geometry)
    safe_operator = safe_filename(operator_id)
    safe_site = safe_filename(site_id)
    if not safe_operator or not safe_site:
        fail("Operator ID and Site / Area ID must contain usable filename characters.")

    properties = {
        "operator_id": operator_id,
        "site_id": site_id,
        "metro_locality": metro,
    }

    if center_point is not None:
        properties["center_point"] = center_point
    if operational_area_definition is not None:
        properties["operational_area_definition"] = operational_area_definition
    if bounds is not None:
        properties["geographic_bounds"] = bounds
    if contact_name or contact_email or contact_phone:
        contact_parts = [part for part in (contact_name, contact_email, contact_phone) if part]
        properties["coordination_contact"] = " | ".join(contact_parts)
    if effective_from:
        properties["effective_from"] = effective_from
    if effective_to:
        properties["effective_to"] = effective_to

    feature = {
        "type": "Feature",
        "properties": properties,
        "geometry": geometry,
    }

    path = f"operational-areas/{safe_operator}/{safe_site}.geojson"
    branch = f"submission/issue-{issue_number}"

    existing_main = get_file(path, "main")
    if existing_main:
        try:
            existing_text = base64.b64decode(existing_main["content"]).decode("utf-8")
            existing_feature = json.loads(existing_text)
        except (KeyError, ValueError, UnicodeDecodeError) as exc:
            fail(f"Existing operational-area file {path} is not valid JSON: {exc}")

        if same_json(feature, existing_feature):
            comment(
                issue_number,
                (
                    f"This operational area is already published at {path}. "
                    "No new Pull Request was created because the submitted data "
                    "matches the existing published area.\n\n"
                    f"Published source: https://github.com/{REPOSITORY}/blob/main/{path}"
                ),
            )
            close_issue(issue_number)
            print(f"Duplicate submission detected for {path}; no PR created.")
            return

    status, open_prs = api(
        "GET",
        f"/repos/{REPOSITORY}/pulls?state=open&base=main&per_page=100",
    )
    if status != 200:
        fail(f"Unable to list open pull requests: {open_prs}")

    for pull in open_prs:
        pr_number = pull["number"]
        status, files = api(
            "GET",
            f"/repos/{REPOSITORY}/pulls/{pr_number}/files?per_page=100",
        )
        if status != 200:
            fail(f"Unable to inspect PR #{pr_number}: {files}")

        if any(item.get("filename") == path for item in files):
            comment(
                issue_number,
                (
                    f"An open Pull Request already exists for this operational area: "
                    f"PR #{pr_number}. No second Pull Request was created.\n\n"
                    f"Existing PR: {pull['html_url']}"
                ),
            )
            close_issue(issue_number)
            print(f"Open PR #{pr_number} already changes {path}; no PR created.")
            return

    status, main_ref = api("GET", f"/repos/{REPOSITORY}/git/ref/heads/main")
    if status != 200:
        fail(f"Unable to resolve main branch: {main_ref}")
    main_sha = main_ref["object"]["sha"]

    status, response = api(
        "POST",
        f"/repos/{REPOSITORY}/git/refs",
        {"ref": f"refs/heads/{branch}", "sha": main_sha},
    )
    if status not in (201, 422):
        fail(f"Unable to create submission branch {branch}: {response}")

    content = json.dumps(feature, indent=2) + "\n"
    existing_branch = get_file(path, branch)

    payload = {
        "message": (
            f"Update operational area from submission #{issue_number}"
            if existing_main
            else f"Add operational area from submission #{issue_number}"
        ),
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing_branch:
        payload["sha"] = existing_branch["sha"]

    status, response = api(
        "PUT",
        f"/repos/{REPOSITORY}/contents/{urllib.parse.quote(path, safe='/')}",
        payload,
    )
    if status not in (200, 201):
        fail(f"Unable to write {path} on {branch}: {response}")

    action_word = "Update" if existing_main else "Publish"
    pr_payload = {
        "title": f"{action_word} operational area: {operator_id} / {site_id}",
        "head": branch,
        "base": "main",
        "draft": False,
        "body": (
            "## Automated operational-area submission\n\n"
            f"This PR was generated from Issue #{issue_number}: {issue_url}.\n\n"
            "### Proposed area\n"
            f"- Operator: {operator_id}\n"
            f"- Site / Area: {site_id}\n"
            f"- Metro / Locality: {metro}\n"
            "- Source: Operator submission form\n\n"
            "### Review checklist\n"
            "- [ ] Geometry is correct and represents only the intended operational area.\n"
            "- [ ] Minimum-necessary information principle is satisfied.\n"
            "- [ ] No routes, trajectories, customer data, volumes, or sensitive strategy are included.\n"
            "- [ ] Contact information is appropriate for publication.\n"
            "- [ ] Effective dates are correct, if supplied.\n"
            "- [ ] Automated validation passes.\n"
            "- [ ] Potential overlap has been reviewed as an awareness signal only.\n\n"
            "Publication is automatic when the required CI validation passes."
        ),
    }

    status, pr = api("POST", f"/repos/{REPOSITORY}/pulls", pr_payload)
    if status not in (200, 201):
        fail(f"Unable to create pull request: {pr}")

    dispatch_payload = {
        "event_type": "operational-area-submission",
        "client_payload": {
            "pr_number": pr["number"],
            "head_sha": pr["head"]["sha"],
            "head_ref": pr["head"]["ref"],
            "issue_number": issue_number,
        },
    }
    status, response = api("POST", f"/repos/{REPOSITORY}/dispatches", dispatch_payload)
    if status != 204:
        fail(f"Unable to trigger validation workflow for PR #{pr['number']}: {response}")

    comment(
        issue_number,
        (
            f"Submission processed. Pull Request #{pr['number']} was created. "
            "The area will be published automatically when the required CI validation passes.\n\n"
            f"PR: {pr['html_url']}"
        ),
    )
    close_issue(issue_number)
    print(f"Created PR #{pr['number']}: {pr['html_url']}")


if __name__ == "__main__":
    main()
