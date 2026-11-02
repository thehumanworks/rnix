"""Session and image records.

These live in a Modal Volume rather than a Dict, because Dict entries expire after
7 days of inactivity and snapshots last 30.
"""

import io
import json

import modal
import modal.exception

VOLUME = "rnix-state"


class Store:
    def __init__(self, environment: str | None):
        self.volume = modal.Volume.from_name(VOLUME, create_if_missing=True, environment_name=environment)

    def read(self, path: str) -> dict | None:
        try:
            return json.loads(b"".join(self.volume.read_file(path)))
        except (FileNotFoundError, modal.exception.NotFoundError):
            return None

    def write(self, path: str, record: dict) -> None:
        data = json.dumps(record, indent=2).encode()
        with self.volume.batch_upload(force=True) as batch:
            batch.put_file(io.BytesIO(data), path)

    def remove(self, path: str) -> None:
        self.volume.remove_file(path)

    def names(self, directory: str) -> list[str]:
        try:
            entries = self.volume.listdir(directory)
        except (FileNotFoundError, modal.exception.NotFoundError):
            return []
        return sorted(entry.path.rsplit("/", 1)[-1].removesuffix(".json") for entry in entries)


def session_path(project: str, name: str) -> str:
    return f"sessions/{project}/{name}.json"


def image_path(project: str) -> str:
    return f"images/{project}.json"
