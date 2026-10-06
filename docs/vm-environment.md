# Development VM

The disposable VirtualBox VM uses **`bento/fedora-43` version `202511.16.0`**
and keeps the existing `/vagrant` project mount, 4 GiB RAM, and two CPUs.
Vagrant selects the host architecture automatically; the evaluation below
tests **amd64 only**. Ollama and model weights are not bundled.

The application uses an isolated, uv-managed Python 3.14 environment at
`/home/vagrant/.venvs/mininet-ai`, with the locked PyPI Mininet dependency.
Distro packages supply the native `mn`, `mnexec`, OVS, and test controller.
The venv, interpreter, caches, live databases and control sockets stay
guest-local. `scripts/vm-run.sh` still selects the same environment under sudo.

Fedora has an approximately [13-month release lifecycle](https://docs.fedoraproject.org/en-US/releases/lifecycle/).
Re-evaluate and update the pinned image before Fedora 43 reaches end of life;
the version pin does not extend distro security support.

## Starting and provisioning

```bash
vagrant up
vagrant ssh
cd /vagrant
```

The first start downloads/imports the pinned box and provisions dependencies.
Later `vagrant up` commands boot the existing environment without provisioning.
Use `vagrant provision` after changing provisioning, or `vagrant up --provision`
when deliberately booting and reprovisioning together. Neither command switches
an existing VM to a different base image.

Do not destroy an existing VM blindly: its guest-local run artifacts and agent
memory are not on the shared mount. Export finalized runs and preserve any
needed private offline database snapshots before replacing a VM. Existing VMs
and saved data are never automatically migrated or deleted.

## Networking tools

Alongside Mininet, `mnexec`, Open vSwitch and its test controller, the VM bundles:

| Commands | Purpose |
| --- | --- |
| `iperf`, `iperf3` | Throughput testing; the maintained example still uses iperf v2 |
| `tcpdump` | Packet capture |
| `traceroute`, `mtr` | Path and reachability diagnosis |
| `ethtool` | Link and device inspection |
| `dig` | DNS queries |
| `ip`, `ss`, `tc` | Interfaces, routes, sockets, and traffic control |
| `ping`, `nc` | Connectivity and TCP/UDP probing |

Tools being installed does not authorize agents to execute them. Agent access
still requires explicitly configured capabilities. Captures may contain
sensitive traffic; store and share them accordingly.

## Verification

Run `scripts/test-vm.sh` from the host, only against the disposable VM. It checks
the bundled commands as well as Python imports, tests, lint, types, OVS, live
networking and cleanup. Missing diagnostic commands fail verification but do not
prevent independent live-network checks. Missing native networking prerequisites
skip destructive live checks.

The storage acceptance check is separate. Inside the VM:

```bash
cd /vagrant
sudo scripts/vm-run.sh python scripts/check-vm-storage.py
```

Retain the printed `BUNDLE` path, halt and boot the VM from the host, then verify
the same bundle inside the guest:

```bash
sudo scripts/vm-run.sh python scripts/check-vm-storage.py verify BUNDLE
```

## Image evaluation

Compare the existing Ubuntu image against Debian, Fedora, and openSUSE with
identical resources. Use isolated project copies and new Vagrant machines;
never use `mn -c` or storage smoke checks against a non-disposable environment.

Measure first-time setup separately. For repeat startup, perform three
`vagrant halt` / `vagrant up` cycles without `--provision`, timing from the start
of `vagrant up` through SSH, the project mount, OVS database access, and the
actual application environment's Python/Mininet imports. Record each trial and
its median, not just the best result. Record provisioned root-filesystem usage
before running tests or generating artifacts; compressed box download size is
a different metric.

The goal is at least 20% faster repeat startup. Below that target, prefer lower
provisioned disk usage, then smaller downloads, while rejecting alternatives
more than 10% slower than Ubuntu. Live compatibility and storage correctness
are mandatory regardless of timing. No custom kernels, custom boxes, or
source-built Mininet/OVS are used.

### Measurements on 2026-10-06

Host: Intel Core i5-12450H, Linux `7.2.8-arch1-2`, VirtualBox `7.2.20`,
Vagrant `2.4.10.dev`. One VM ran at a time. Ubuntu used its original
provisioning; alternatives included the additional tools and managed Python.
Provisioning installed repository versions of the requested packages, not a
full OS upgrade. Results are host-specific, not general distro performance
claims.

These repeat-start measurements use the final evaluated recipes; Ubuntu is
the unchanged baseline. Passing readiness checks alone is not a compatibility
pass.

| Pinned VirtualBox box (amd64) | Repeat-start trials (seconds) | Median | Compatibility |
| --- | --- | --- | --- |
| `bento/ubuntu-26.04` `202606.01.0` | 133.52, 134.16, 134.65 | 134.16 s | Original baseline |
| `bento/debian-13` `202510.26.0` | 39.01, 39.46, 39.00 | 39.01 s | Not accepted: typechecking and live reachability failures |
| `bento/fedora-43` `202511.16.0` | 24.27, 24.33, 24.37 | 24.33 s | All 28 VM checks and rebooted storage verification passed |
| `bento/opensuse-leap-16.0` `202510.26.0` | 43.16, 43.06, 43.36 | 43.16 s | Not accepted: tool checks, export tests, and live storage failed |

Fedora reduces the measured median repeat startup by **81.9%**. Debian's
current evaluation recipe lacks Node's `libatomic.so.1` dependency for Pyright
and failed one live host-reachability assertion. These are recipe/test results,
not a claim that Debian cannot support Mininet.

openSUSE was evaluated beyond the Debian/Ubuntu family. Its corrected recipe
used the distro's `iperf` (v3) and `iperf2` packages and an explicit sbin PATH
for provisioning. The final verification still failed native/diagnostic command
discovery, eight export tests involving `/proc/self/fd/.../run.log`, and live
storage deployment because the locked PyPI Mininet needed missing `ifconfig`.
It was therefore rejected without weakening compatibility or storage gates.
Earlier openSUSE boots without completed provisioning are excluded from the
startup results. These failures describe the tested recipe, not all openSUSE
configurations.

### Footprint and initial setup

| Box | Initial provisioned root usage | Compressed download | First creation/provisioning attempt |
| --- | --- | --- | --- |
| Ubuntu | 5.20 GiB | 3.08 GiB | 196.7 s, successful; box already cached |
| Debian | 1.49 GiB | 1.05 GiB | 255.6 s, successful provisioning; later verification failures |
| Fedora | 2.39 GiB | 0.89 GiB | 168.4 s, successful provisioning; later verification passed after fixes |
| openSUSE | 4.27 GiB | 1.72 GiB | 341.2 s, failed initial provisioning attempt |

Footprints are snapshots of the **initial provisioning recipes before tests**,
not measurements of the final post-verification guests. Final Fedora root
usage was 2.56 GiB after test artifacts and Pyright's Node cache existed. The
initial-to-final recipe changes fixed cache-parent ownership and verification;
the Fedora networking package set and managed Python selection did not change.
openSUSE's footprint is from its first completed provisioning after retries,
before running tests; that corrected boot/provision attempt took 63.6 s on an
existing VM, not a fresh creation, and still failed subsequent verification.

Root usage is `df -B1 --output=used /`, not virtual-disk capacity or host
allocation. Download bytes come from HTTP `Content-Range` on the pinned
VirtualBox amd64 archives. First-setup durations include the observed
`vagrant up` creation/provisioning attempt, separate from subsequent readiness
checks. They are not directly comparable: Ubuntu's box was cached; the other
boxes' cache states were not captured, and download/network conditions differed.

Fedora's measured idle RAM was higher (0.76 GiB versus Ubuntu's 0.52 GiB);
Debian used 0.45 GiB. This change optimizes startup, not minimum RAM, and keeps
the original allocation rather than tuning resources during the comparison.

Box metadata: [Ubuntu](https://vagrantcloud.com/api/v2/vagrant/bento/ubuntu-26.04),
[Debian](https://vagrantcloud.com/api/v2/vagrant/bento/debian-13),
[Fedora](https://vagrantcloud.com/api/v2/vagrant/bento/fedora-43), and
[openSUSE](https://vagrantcloud.com/api/v2/vagrant/bento/opensuse-leap-16.0).
