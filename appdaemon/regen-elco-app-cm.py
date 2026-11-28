#!/usr/bin/env python3
"""Regenerate appdaemon-elco-app-cm.yml from the upstream python source.

Run when ~/elco-remocon-net-appdaemon/apps/elco-remocon-net-appdaemon/elco-remocon-net-appdaemon.py
changes (or you bump apps.yaml settings below). Output is committed to fleet.
"""
import os
import yaml

REPO = os.environ.get("ELCO_REPO") or os.path.expanduser("~/elco-remocon-net-appdaemon")
SRC_PATH = f"{REPO}/apps/elco-remocon-net-appdaemon/elco-remocon-net-appdaemon.py"
OUT = os.environ.get("CM_OUT") or os.path.join(os.path.dirname(__file__), "appdaemon-elco-app-cm.yml")

APPS_YAML = """remocon:
  module: elco-remocon-net-appdaemon
  class: Remocon
  plugin: HASS
  base_url: https://www.remocon-net.remotethermo.com
  username: !secret remocon_username
  password: !secret remocon_password
  gateway_id: !secret remocon_gateway_id
  bearer_token: !secret remocon_bearer_token
  refresh_rate: 10
  enable_writes: true
"""


class LiteralStr(str):
    pass


def literal_repr(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(LiteralStr, literal_repr)


def main():
    src = open(SRC_PATH).read()
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "appdaemon-elco-app",
            "annotations": {
                "mdapi.ch/source": "https://gitlab.mdapi.ch/mdapi/elco-remocon-net-appdaemon",
            },
        },
        "data": {
            "apps.yaml": LiteralStr(APPS_YAML),
            "elco-remocon-net-appdaemon.py": LiteralStr(src),
        },
    }
    with open(OUT, "w") as f:
        yaml.dump(cm, f, sort_keys=False, default_flow_style=False, width=10000, allow_unicode=True)
    print(f"Wrote {OUT} ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()
