#!/usr/bin/env bash
## Regenerate grafana-dashboards-cm.yml from the JSON files in this directory.
## Run after editing any dashboard *.json:
##
##   ./dashboards/regen-cm.sh
##
## The CM embeds JSON in YAML literal blocks; this script keeps it in sync
## with the source JSON files so the diff stays readable.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="$(dirname "$HERE")"

python3 - <<EOF
import json, os

out = '''## Grafana dashboards provisioning. Provider config + dashboards live in
## one ConfigMap; mounted at /etc/grafana/provisioning/dashboards. Grafana's
## file provider expects only its YAML config files at that path and looks
## elsewhere for JSON, so we mount the JSON files separately via subPath in
## the Deployment.
##
## REGENERATE THIS FILE FROM dashboards/*.json:
##   ./dashboards/regen-cm.sh
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-provider
  namespace: monitoring
data:
  mdapi.yaml: |
    apiVersion: 1
    providers:
      - name: mdapi
        orgId: 1
        folder: ''
        type: file
        disableDeletion: false
        updateIntervalSeconds: 30
        allowUiUpdates: false
        options:
          path: /var/lib/grafana/dashboards/mdapi
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-mdapi
  namespace: monitoring
data:
'''
for fname in sorted(os.listdir('$HERE')):
    if not fname.endswith('.json'):
        continue
    d = json.load(open(os.path.join('$HERE', fname)))
    body = json.dumps(d, indent=2)
    indented = '\n'.join('    ' + l for l in body.split('\n'))
    out += f'  {fname}: |\n' + indented + '\n'

open('$BUNDLE/grafana-dashboards-cm.yml','w').write(out)
print('wrote $BUNDLE/grafana-dashboards-cm.yml', len(out), 'bytes')
EOF
