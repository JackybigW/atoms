#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${CLASH_CONFIG_PATH:-/etc/clash/config.yaml}"
CLASH_DIR="${CLASH_DIR:-/etc/clash}"
CLASH_BIN="${CLASH_BIN:-/usr/local/bin/clash}"
CLASH_SERVICE="${CLASH_SERVICE:-clash}"
AUTO_TOLERANCE_MS="${CLASH_AUTO_TOLERANCE_MS:-500}"
AUTO_INTERVAL_SECONDS="${CLASH_AUTO_INTERVAL_SECONDS:-300}"
AUTO_TEST_URL="${CLASH_AUTO_TEST_URL:-https://www.gstatic.com/generate_204}"

python3 - "$CONFIG_PATH" "$AUTO_TOLERANCE_MS" "$AUTO_INTERVAL_SECONDS" "$AUTO_TEST_URL" <<'PY'
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
tolerance = int(sys.argv[2])
interval = int(sys.argv[3])
test_url = sys.argv[4]

allowed = [
    "level1-新加坡01-NF",
    "level1-新加坡02-NF",
    "level1-日本01-NF",
    "level1-日本02-NF",
    "level1-台湾01",
    "level1-台湾02",
    "美国01",
    "美国02",
    "level1-韩国01",
]

cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
proxies = cfg.get("proxies") or []
by_name = {proxy.get("name"): proxy for proxy in proxies}
missing = [name for name in allowed if name not in by_name]
if missing:
    raise SystemExit("missing required Clash proxies: " + ", ".join(missing))

backup_path = config_path.with_name(
    config_path.name + ".bak.atoms_proxy_policy_" + datetime.now().strftime("%Y%m%d%H%M%S")
)
shutil.copy2(config_path, backup_path)

cfg["proxies"] = [by_name[name] for name in allowed]
groups = cfg.setdefault("proxy-groups", [])

def find_group(name: str):
    for group in groups:
        if group.get("name") == name:
            return group
    group = {"name": name}
    groups.append(group)
    return group

auto = find_group("Auto")
auto["type"] = "url-test"
auto["proxies"] = allowed[:]
auto["url"] = test_url
auto["interval"] = interval
auto["tolerance"] = tolerance

proxy = find_group("Proxy")
proxy["type"] = "select"
proxy["proxies"] = ["Auto"] + allowed[:]

config_path.write_text(
    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
print(f"backup={backup_path}")
print(f"auto_tolerance_ms={tolerance}")
print("allowed_proxies=" + ",".join(allowed))
PY

"$CLASH_BIN" -t -d "$CLASH_DIR"
systemctl restart "$CLASH_SERVICE"
sleep 2
systemctl is-active "$CLASH_SERVICE"
