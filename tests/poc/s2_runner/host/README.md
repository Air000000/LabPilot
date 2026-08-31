# Candidate B native-host setup (Ubuntu 26.04 PoC only)

This is a human-admin runbook for the disposable native Ubuntu 26.04 VM. The Python PoC never
runs `sudo`, creates users, edits `/etc`, or reboots the host. Candidate B remains unselected until
native evidence is complete.

## 1. Remove the Candidate A test authorization

The exact-unit polkit rule used to falsify Candidate A must not remain active:

```bash
sudo rm -f /etc/polkit-1/rules.d/49-labpilot-s2-permission-probe.rules
sudo systemctl restart polkit
```

Do not replace it with NOPASSWD sudo, a wildcard polkit rule, or `systemd --user`.

## 2. Create the two runtime principals

`labpilot-controller` must have `labpilot-control` as its primary group because the Runner checks
both kernel peer UID and GID. `labpilot-train` must not belong to `labpilot-control`.

```bash
getent group labpilot-control >/dev/null \
  || sudo groupadd --system labpilot-control

id labpilot-controller >/dev/null 2>&1 \
  || sudo useradd --system \
       --gid labpilot-control \
       --home-dir /nonexistent \
       --shell /usr/sbin/nologin \
       labpilot-controller

id labpilot-train >/dev/null 2>&1 \
  || sudo useradd --system \
       --user-group \
       --home-dir /nonexistent \
       --shell /usr/sbin/nologin \
       labpilot-train

id labpilot-controller
id labpilot-train
test "$(id -gn labpilot-controller)" = "labpilot-control" \
  || { echo 'FAIL: wrong controller primary group'; exit 1; }
test "$(id -gn labpilot-train)" = "labpilot-train" \
  || { echo 'FAIL: wrong training primary group'; exit 1; }
id -nG labpilot-train | tr ' ' '\n' | grep -Fx labpilot-control \
  && { echo 'FAIL: labpilot-train is in labpilot-control'; exit 1; } \
  || true
```

Required identity shape:

```text
labpilot-controller primary group = labpilot-control
labpilot-train      primary group = labpilot-train
labpilot-train      NOT in labpilot-control
```

## 3. Install the reviewed PoC import tree as root-owned code

Run these commands from the reviewed Candidate B checkout. The root daemon must never import from
a checkout writable by `air` or `labpilot-controller`.

```bash
sudo rm -rf /opt/labpilot-s2-runner-poc
sudo install -d -o root -g root -m 0755 \
  /opt/labpilot-s2-runner-poc/tests/poc/s2_runner

sudo install -o root -g root -m 0644 tests/__init__.py \
  /opt/labpilot-s2-runner-poc/tests/__init__.py
sudo install -d -o root -g root -m 0755 \
  /opt/labpilot-s2-runner-poc/tests/poc
sudo install -o root -g root -m 0644 tests/poc/__init__.py \
  /opt/labpilot-s2-runner-poc/tests/poc/__init__.py
sudo find tests/poc/s2_runner -maxdepth 1 -type f -name '*.py' -print0 \
  | sudo xargs -0 -I{} install -o root -g root -m 0644 {} \
      /opt/labpilot-s2-runner-poc/tests/poc/s2_runner/

sudo find /opt/labpilot-s2-runner-poc -type d -exec chmod 0755 {} +
sudo find /opt/labpilot-s2-runner-poc -type f -exec chmod 0644 {} +
sudo chown -R root:root /opt/labpilot-s2-runner-poc
```

Verify that no unprivileged runtime principal can write the import tree:

```bash
find /opt/labpilot-s2-runner-poc -printf '%u:%g %m %p\n' | sort
sudo -u labpilot-controller -g labpilot-control -- \
  test ! -w /opt/labpilot-s2-runner-poc/tests/poc/s2_runner/runner_daemon.py
sudo -u labpilot-train -- \
  test ! -w /opt/labpilot-s2-runner-poc/tests/poc/s2_runner/runner_daemon.py
```

## 4. Install the fixed root-owned policy and service

```bash
sudo install -d -o root -g root -m 0755 /etc/labpilot-s2-runner-poc
sudo install -o root -g root -m 0600 \
  tests/poc/s2_runner/host/runner-policy.example.json \
  /etc/labpilot-s2-runner-poc/policy.json

sudo install -o root -g root -m 0644 \
  tests/poc/s2_runner/host/labpilot-s2-runner-poc.service \
  /etc/systemd/system/labpilot-s2-runner-poc.service
```

The service creates only its fixed volatile runtime directory on every start, so `/run` loss across
P11 reboot does not prevent Runner recovery.

## 5. Create persistent PoC data roots

```bash
sudo install -d -o root -g root -m 0755 /var/lib/labpilot-s2-poc
sudo install -d -o labpilot-controller -g labpilot-control -m 0750 \
  /var/lib/labpilot-s2-poc/controller-state \
  /var/lib/labpilot-s2-poc/evidence
sudo install -d -o labpilot-train -g labpilot-train -m 0750 \
  /var/lib/labpilot-s2-poc/workspace
sudo install -d -o root -g labpilot-train -m 0731 \
  /var/lib/labpilot-s2-poc/markers
```

The fixed workload profile uses `UMask=0022`, so the controller can read the known, non-secret
start-marker file without gaining write access to the marker directory.

## 6. Start the Runner and record provenance

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now labpilot-s2-runner-poc.service
sudo systemctl --no-pager --full status labpilot-s2-runner-poc.service

sudo sha256sum \
  /etc/systemd/system/labpilot-s2-runner-poc.service \
  /etc/labpilot-s2-runner-poc/policy.json \
  /opt/labpilot-s2-runner-poc/tests/poc/s2_runner/*.py

stat -c '%U:%G %a %n' \
  /etc/systemd/system/labpilot-s2-runner-poc.service \
  /etc/labpilot-s2-runner-poc/policy.json \
  /run/labpilot-s2-runner-poc \
  /run/labpilot-s2-runner-poc/control.sock
```

Expected socket boundary:

```text
/run/labpilot-s2-runner-poc              root:labpilot-control 750
/run/labpilot-s2-runner-poc/control.sock root:labpilot-control 660
```

Prove that the Runner reports the actual kernel `SO_PEERCRED` UID/GID observed for the Controller
connection, rather than trusting request data:

```bash
cd /opt/labpilot-s2-runner-poc
sudo -u labpilot-controller -g labpilot-control -- /usr/bin/python3 - <<'PY'
import os
from pathlib import Path
from tests.poc.s2_runner.scenario_runner import runner_info

info = runner_info(Path('/run/labpilot-s2-runner-poc/control.sock'))
assert info.get('peer_uid') == os.getuid(), info
assert info.get('peer_gid') == os.getgid(), info
print('PASS: Runner SO_PEERCRED', info['peer_uid'], info['peer_gid'])
PY
```

The `characterize` command repeats this comparison and records `controller_uid`, `controller_gid`,
`runner_peer_uid`, and `runner_peer_gid`; a mismatch is a P0 `HOST_CAPABILITY_GAP`.

## 7. Prove direct system-manager authority is absent

Run as the actual runtime Controller principal:

```bash
sudo -u labpilot-controller -g labpilot-control -- \
  systemd-run --system --no-ask-password \
    --unit=labpilot-s2-controller-must-be-denied.service \
    --collect --wait /usr/bin/true

echo "direct-systemd rc=$?"
```

Required: access/authentication denied and non-zero return code.

## 8. Native peer-negative checks that the harness cannot sudo itself

N1 — unrelated principal must not reach the Runner:

```bash
cd /opt/labpilot-s2-runner-poc
sudo -u nobody -g nogroup -- /usr/bin/python3 - <<'PY'
from pathlib import Path
from tests.poc.s2_runner.scenario_runner import runner_info

try:
    print(runner_info(Path('/run/labpilot-s2-runner-poc/control.sock')))
except OSError as error:
    print(type(error).__name__, error)
    raise SystemExit(0)
raise SystemExit('FAIL: unrelated peer reached Runner')
PY
```

N11 — training principal must not reach the Runner:

```bash
cd /opt/labpilot-s2-runner-poc
sudo -u labpilot-train -- /usr/bin/python3 - <<'PY'
from pathlib import Path
from tests.poc.s2_runner.scenario_runner import runner_info

try:
    print(runner_info(Path('/run/labpilot-s2-runner-poc/control.sock')))
except OSError as error:
    print(type(error).__name__, error)
    raise SystemExit(0)
raise SystemExit('FAIL: training peer reached Runner')
PY
```

N9 — malformed and oversized frames must be bounded rejections and the Runner must remain healthy:

```bash
cd /opt/labpilot-s2-runner-poc
sudo -u labpilot-controller -g labpilot-control -- /usr/bin/python3 - <<'PY'
import socket
import struct
from pathlib import Path

from tests.poc.s2_runner.runner_protocol import MAX_FRAME_BYTES, decode_response, recv_frame
from tests.poc.s2_runner.scenario_runner import runner_info

path = '/run/labpilot-s2-runner-poc/control.sock'

# A malformed client may disappear before reading the error. Force an immediate RST so
# the Runner's best-effort error reply cannot terminate the accept loop on BrokenPipe.
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.connect(path)
    sock.sendall(struct.pack('!I', 1) + b'{')
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))

for payload in (b'{', None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(path)
        if payload is None:
            sock.sendall(struct.pack('!I', MAX_FRAME_BYTES + 1))
        else:
            sock.sendall(struct.pack('!I', len(payload)) + payload)
        response = decode_response(recv_frame(sock))
        assert response.get('ok') is False, response

assert runner_info(Path(path)).get('ok') is True
print('PASS: disconnected/malformed/oversized clients bounded; Runner healthy')
PY
```

Keep the complete outputs for the bounded evidence report.

## 9. Run P0/P12 as `labpilot-controller`

```bash
cd /opt/labpilot-s2-runner-poc
export LABPILOT_POC_STATE=/var/lib/labpilot-s2-poc/controller-state
export LABPILOT_POC_EVIDENCE=/var/lib/labpilot-s2-poc/evidence
export LABPILOT_POC_NAMESPACE=labpilot-s2-poc-native-b

sudo -u labpilot-controller -g labpilot-control -- \
  /usr/bin/python3 -m tests.poc.s2_runner.scenario_runner \
    --state-root "$LABPILOT_POC_STATE" \
    --evidence-root "$LABPILOT_POC_EVIDENCE" \
    --installation-namespace "$LABPILOT_POC_NAMESPACE" \
    --evidence-stack systemd+journal \
    characterize

sudo -u labpilot-controller -g labpilot-control -- \
  /usr/bin/python3 -m tests.poc.s2_runner.scenario_runner \
    --state-root "$LABPILOT_POC_STATE" \
    --evidence-root "$LABPILOT_POC_EVIDENCE" \
    --installation-namespace "$LABPILOT_POC_NAMESPACE" \
    --evidence-stack systemd+journal \
    p12
```

Stop immediately if P0 reports `HOST_CAPABILITY_GAP` or P12 reports `FAIL`. Do not weaken host
policy to continue.

Only after the focused diff and this runbook have been reviewed should the VM be provisioned.
