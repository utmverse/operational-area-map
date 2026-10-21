#!/usr/bin/env python3
"""Add the GitHub submission bridge to the published page.

The bridge intercepts the existing button handler so every geometry type uses
one consistent, human-readable GitHub issue body with a hidden OAM2 payload.
It deliberately does not select an issue template because the template can
replace/ignore URL-prefilled body content in GitHub's issue composer.
Opening the issue is delegated to the page's openGitHubIssue() so detailed
(e.g. uploaded) areas that exceed GitHub's URL limit fall back to copy/paste.
"""

from pathlib import Path
import sys


BRIDGE = r'''<script>
(function () {
  const button = document.getElementById('createGithubSubmission');
  if (!button) return;

  function encodeBase64Url(value) {
    const bytes = new TextEncoder().encode(value);
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function parsePayload() {
    if (typeof buildSubmissionText !== 'function') return null;
    const text = buildSubmissionText().trim();
    if (!text) return null;
    const json = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
    return JSON.parse(json);
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function formatRadius(meters) {
    return (Number(meters) / 1609.344).toFixed(2) + ' mi (' +
      (Number(meters) / 1000).toFixed(2) + ' km)';
  }

  function buildVisibleBody(payload) {
    const definition = payload.operational_area_definition || {};
    let definitionText = '**Geometry Type:** ' + escapeHtml(
      (payload.geometry && payload.geometry.type) || definition.type || 'Unknown'
    );

    if (definition.type === 'MultiCircle') {
      const circles = Array.isArray(definition.circles) ? definition.circles : [];
      definitionText += '\n\n| Circle | Latitude | Longitude | Radius |\n| --- | ---: | ---: | ---: |\n' +
        circles.map((circle, index) => {
          const point = circle.center_point || {};
          return '| ' + (index + 1) + ' | ' + escapeHtml(point.latitude) +
            ' | ' + escapeHtml(point.longitude) + ' | ' +
            escapeHtml(formatRadius(circle.radius_meters)) + ' |';
        }).join('\n');
    } else if (definition.type === 'Circle') {
      const point = definition.center_point || {};
      definitionText += '\n\n**Center:** ' + escapeHtml(point.latitude) + ', ' +
        escapeHtml(point.longitude) + '\n**Radius:** ' +
        escapeHtml(formatRadius(definition.radius_meters));
    } else if (payload.geometry && payload.geometry.type === 'Polygon') {
      const ring = payload.geometry.coordinates && payload.geometry.coordinates[0];
      if (Array.isArray(ring)) definitionText += '\n\n**Boundary vertices:** ' + Math.max(0, ring.length - 1);
    } else if (payload.geometry && payload.geometry.type === 'MultiPolygon') {
      const polygons = Array.isArray(payload.geometry.coordinates) ? payload.geometry.coordinates : [];
      definitionText += '\n\n**Component polygons:** ' + polygons.length;
    }

    return '<!-- OPERATIONAL-AREA-SUBMISSION -->\n\n## Operational Area Submission\n\n' +
      '**Operator ID:** ' + escapeHtml(payload.operator_id || '') + '\n\n' +
      '**Site / Area ID:** ' + escapeHtml(payload.site_id || payload.site_area_id || '') + '\n\n' +
      '**Metro / Locality:** ' + escapeHtml(payload.metro_locality || '') + '\n\n' +
      '**Effective From:** ' + escapeHtml(payload.effective_from || 'Not specified') + '\n\n' +
      '**Effective To:** ' + escapeHtml(payload.effective_to || 'Not specified') + '\n\n' +
      '### Operational Area Definition\n\n' + definitionText + '\n\n' +
      '<!-- OAM2:' + encodeBase64Url(JSON.stringify(payload)) + ' -->';
  }

  button.addEventListener('click', function (event) {
    let payload;
    try {
      payload = parsePayload();
    } catch (_) {
      return; // Let the existing submission handler report the normal error.
    }
    if (!payload) return;

    event.preventDefault();
    event.stopImmediatePropagation();

    const repoUrl = typeof getGitHubRepositoryUrl === 'function'
      ? getGitHubRepositoryUrl()
      : 'https://github.com/Puchala/operational-area-map';
    const siteId = payload.site_id || payload.site_area_id || '';
    const title = 'Operational Area Submission: ' + siteId;
    const body = buildVisibleBody(payload);
    if (typeof openGitHubIssue === 'function') {
      openGitHubIssue(repoUrl, title, body);
      return;
    }
    const url = repoUrl + '/issues/new?title=' + encodeURIComponent(title) +
      '&body=' + encodeURIComponent(body);

    window.open(url, '_blank', 'noopener');
  }, true);
})();
</script>'''


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('usage: prepare_submission_page.py <html-file>')

    path = Path(sys.argv[1])
    html = path.read_text(encoding='utf-8')
    marker = '<!-- submission-preview-bridge: unified-v3 -->'
    if marker in html:
        return

    # Remove any older bridge generated by previous versions so it cannot
    # leave a stale template= URL in the published artifact.
    old_start = '<!-- submission-preview-bridge:'
    while old_start in html:
        start = html.find(old_start)
        script_start = html.find('<script>', start)
        script_end = html.find('</script>', script_start)
        if script_start == -1 or script_end == -1:
            html = html[:start]
            break
        end = script_end + len('</script>')
        html = html[:start] + html[end:]

    html += '\n' + marker + '\n' + BRIDGE + '\n'
    path.write_text(html, encoding='utf-8')


if __name__ == '__main__':
    main()
