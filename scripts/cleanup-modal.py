"""Delete the Modal resources created while testing rnix.

Names are exact. Other rnix images, from projects not listed here, are skipped.
Set DRY_RUN=1 to print what would be deleted.
"""

import json
import os
import subprocess
import sys

import modal
import modal.exception
import modal.experimental

PROJECTS = ["rnix-e2e", "rnix-e2e-gv", "rnix-e2e-off", "rnix-e2e-sysd", "rnix-e2f"]
IMAGE_REPOS = ["rnix-base", "rnix-spike", *(f"rnix-{project}" for project in PROJECTS)]
APPS = ["rnix", "rnix-spike"]
SECRET = "rnix-e2e-secret"
VOLUME = "rnix-state"

DRY_RUN = os.environ.get("DRY_RUN") == "1"
counts = {"deleted": 0, "already gone": 0, "failed": 0}


def modal_cli(*args: str) -> str:
    proc = subprocess.run(["modal", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def act(description: str, action) -> None:
    if DRY_RUN:
        print(f"would delete {description}")
        return
    try:
        action()
    except modal.exception.NotFoundError:
        counts["already gone"] += 1
        print(f"already gone: {description}")
    except Exception as error:
        counts["failed"] += 1
        print(f"failed: {description}: {error}", file=sys.stderr)
    else:
        counts["deleted"] += 1
        print(f"deleted {description}")


def snapshot_ids() -> set[str]:
    """Collect session snapshots from the state volume, before it is deleted."""
    try:
        volume = modal.Volume.from_name(VOLUME)
        entries = volume.listdir("sessions", recursive=True)
    except (FileNotFoundError, modal.exception.NotFoundError):
        return set()
    ids = set()
    for entry in entries:
        if not entry.path.endswith(".json") or entry.path.split("/")[1] not in PROJECTS:
            continue
        record = json.loads(b"".join(volume.read_file(entry.path)))
        ids.update(filter(None, (record.get("exit_image"), record.get("checkpoint_image"))))
    return ids


def image_ids() -> set[str]:
    tags = json.loads(modal_cli("image", "names", "list", "--prefix", "rnix", "--json"))
    ids = set()
    for tag in tags:
        if tag["tag"].split(":")[0] in IMAGE_REPOS:
            ids.add(tag["image_id"])
        else:
            print(f"skipped {tag['tag']}, not a test image")
    return ids


def main() -> int:
    for image_id in sorted(snapshot_ids()):
        act(f"snapshot {image_id}", lambda image_id=image_id: modal.experimental.image_delete(image_id))
    for image_id in sorted(image_ids()):
        act(f"image {image_id}", lambda image_id=image_id: modal.experimental.image_delete(image_id))

    listed = json.loads(modal_cli("app", "list", "--json"))
    apps = {app["description"]: app["app_id"] for app in listed if app["stopped_at"] is None}
    for name in APPS:
        if name in apps:
            act(f"app {name}", lambda app_id=apps[name]: modal_cli("app", "stop", app_id, "--yes"))

    act(f"secret {SECRET}", lambda: modal_cli("secret", "delete", SECRET, "--allow-missing", "--yes"))
    act(f"volume {VOLUME}", lambda: modal_cli("volume", "delete", VOLUME, "--allow-missing", "--yes"))

    if not DRY_RUN:
        print(", ".join(f"{count} {label}" for label, count in counts.items()))
        print("Deleted images keep their tags in `modal image names list`.")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
