"""THROWAWAY GH-99: publish negative evidence with one host-identifier redaction.

No services, namespace changes, acquired-code execution, inference or deletion.
Keep the exact raw local receipt unchanged; redact only the journal-file path.
"""

import gzip
import hashlib
import json
from pathlib import Path
import re


BASE = Path(__file__).resolve().parent
raw = (BASE / "ollama-containment-service-fixtures.corrected.observations.json").read_bytes()
report = json.loads(raw)
redactions = []
for index, command in enumerate(report["commands"]):
    if command["argv"][0] != "journalctl":
        continue
    sanitized, count = re.subn(r"/var/log/journal/[^\n,]+", "/var/log/journal/<REDACTED-HOST-PATH>", command["stderr"])
    if count:
        command["stderr"] = sanitized
        redactions.append({"field": f"commands[{index}].stderr", "count": count,
                           "reason": "unneeded host journal machine/file identifiers; error and outcome preserved"})
if sum(item["count"] for item in redactions) != 1:
    raise RuntimeError("Unexpected redaction shape; do not publish silently")
manual_path = Path("/usr/share/man/man1/bwrap.1.gz")
manual_bytes = manual_path.read_bytes()
manual = gzip.decompress(manual_bytes).decode()
if not all(text in manual for text in ("nested user namespace", "max_user_namespaces", "unable to raise")):
    raise RuntimeError("Installed manual does not support the recorded documentation summary")
report["publication"] = {
    "raw_local_receipt_sha256": hashlib.sha256(raw).hexdigest(),
    "public_exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "redactions": redactions,
    "raw_receipt_retained_unchanged": True,
    "installed_documentation": {
        "path": str(manual_path), "compressed_file_sha256": hashlib.sha256(manual_bytes).hexdigest(),
        "evidence_class": "installed documentation, not observed ownership",
        "summary": "--disable-userns sets user.max_user_namespaces to 1, then enters a nested user namespace unable to raise the outer limit",
        "failed_run_network_owner_or_effective_caps_observed": False,
    },
}
output = BASE / "ollama-containment-service-fixtures.corrected.public.observations.json"
with output.open("x") as stream:
    json.dump(report, stream, indent=2)
    stream.write("\n")
print(json.dumps({"public_evidence": str(output), "raw_sha256": report["publication"]["raw_local_receipt_sha256"],
                  "redactions": redactions, "success_of_underlying_fixture": report["success"]}, indent=2))
