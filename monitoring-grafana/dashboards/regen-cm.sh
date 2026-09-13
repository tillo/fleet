#!/usr/bin/env bash
## Regenerate grafana-dashboards-cm.yml from the JSON files in this directory.
## Run after editing any dashboard *.json:
##
##   ./dashboards/regen-cm.sh
##
## LAYOUT: dashboards/<Folder>/<name>.json
## Each subdirectory becomes (a) its own ConfigMap and (b) a Grafana folder,
## via the provider's foldersFromFilesStructure. Files are NOT read from the
## top level -- put every dashboard in a folder.
##
## WHY ONE CM PER FOLDER: a ConfigMap is capped at 1 MiB. A single combined CM
## reached 866 KB (83% of the cap) on 2026-08-07 with 23 dashboards, so roughly
## three more would have broken the apply -- and the failure surfaces as a Fleet
## apply error, not as a missing dashboard. Splitting per folder keeps each CM
## small and makes the ceiling a per-folder problem rather than a global one.
##
## ADDING A FOLDER also needs a volume + volumeMount in grafana-deploy.yml,
## mounted at /var/lib/grafana/dashboards/mdapi/<Folder>. The script prints the
## snippet to paste.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="$(dirname "$HERE")"

python3 - <<EOF
import json, os

here = '$HERE'
folders = sorted(d for d in os.listdir(here)
                 if os.path.isdir(os.path.join(here, d)) and not d.startswith('.'))

out = '''## Grafana dashboards provisioning. ONE ConfigMap PER FOLDER -- see
## dashboards/regen-cm.sh for why (the combined CM hit 83% of the 1 MiB
## ConfigMap cap). Each dashboards/<Folder>/ directory becomes a ConfigMap
## mounted at /var/lib/grafana/dashboards/mdapi/<Folder>, and
## foldersFromFilesStructure turns that directory into a Grafana folder.
##
## REGENERATE THIS FILE FROM dashboards/*/*.json:
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
          ## Each mounted subdirectory becomes a Grafana folder.
          foldersFromFilesStructure: true
      - name: harvester
        ## Fed by the k8s dashboard sidecar (see grafana-deploy.yml). The sidecar
        ## writes each CM into a subfolder named by its grafana_folder annotation
        ## (Kubernetes/Rancher/Longhorn); foldersFromFilesStructure turns those
        ## subdirs into Grafana folders. Un-annotated CMs (the Harvester VM
        ## dashboards) stay at the root.
        orgId: 1
        folder: 'Harvester'
        type: file
        disableDeletion: false
        updateIntervalSeconds: 30
        allowUiUpdates: false
        options:
          path: /var/lib/grafana/dashboards/harvester
          foldersFromFilesStructure: true
'''

report = []
for folder in folders:
    fdir = os.path.join(here, folder)
    files = sorted(f for f in os.listdir(fdir) if f.endswith('.json'))
    if not files:
        continue
    out += f'''---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-{folder.lower()}
  namespace: monitoring
data:
'''
    size = 0
    for fname in files:
        d = json.load(open(os.path.join(fdir, fname)))
        body = json.dumps(d, indent=2)
        indented = '\n'.join('    ' + l for l in body.split('\n'))
        chunk = f'  {fname}: |\n' + indented + '\n'
        out += chunk
        size += len(chunk)
    report.append((folder, len(files), size))

open('$BUNDLE/grafana-dashboards-cm.yml', 'w').write(out)
print('wrote $BUNDLE/grafana-dashboards-cm.yml', len(out), 'bytes total')
print()
print('%-12s %10s %12s %s' % ('FOLDER', 'DASHBOARDS', 'CM BYTES', 'PCT OF 1 MiB'))
LIMIT = 1024 * 1024
for folder, n, size in report:
    pct = 100.0 * size / LIMIT
    flag = '  <-- WATCH' if pct > 60 else ''
    print('%-12s %10d %12d %11.1f%%%s' % (folder, n, size, pct, flag))
EOF
