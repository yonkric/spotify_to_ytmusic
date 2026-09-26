"""Background transfer jobs for the single-user local web UI."""

import json
import logging
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from spotify_to_ytmusic.sync.engine import Selection, apply_choices, run_transfer
from spotify_to_ytmusic.web.auth import MATCH_CACHE_FILE, friendly_error

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    direction: str
    status: str = "running"  # running | done | failed
    message: str = "Starting…"
    done: int = 0
    total: int = 0
    report: list[dict] = field(default_factory=list)
    error: str | None = None
    dest: Any = field(default=None, repr=False)  # library the transfer wrote to

    @property
    def not_found_text(self) -> str:
        lines = []
        for section in self.report:
            if section["not_found"]:
                lines.append(
                    f"## {section['label']} ({len(section['not_found'])} not found)"
                )
                for item in section["not_found"]:
                    lines.append(f"{item['label']}  —  {item['reason']}")
                lines.append("")
        return "\n".join(lines) or "Everything was matched.\n"


def _load_cache() -> dict:
    if not MATCH_CACHE_FILE.is_file():
        return {}
    return json.loads(MATCH_CACHE_FILE.read_text())


def _save_cache(cache: dict) -> None:
    MATCH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MATCH_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))


class JobRunner:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def latest(self) -> Job | None:
        return next(reversed(self._jobs.values()), None)

    def forget_finished(self) -> None:
        """Drop finished jobs, e.g. after switching accounts, so their review lists
        can't write to the account that was disconnected."""
        with self._lock:
            self._jobs = {k: j for k, j in self._jobs.items() if j.status == "running"}

    def busy(self) -> bool:
        return any(j.status == "running" for j in self._jobs.values())

    def start(self, source, dest, selection: Selection) -> Job:
        with self._lock:
            if self.busy():
                raise RuntimeError(
                    "A transfer is already running; wait for it to finish."
                )
            job = Job(
                id=uuid.uuid4().hex[:12],
                direction=f"{source.name} → {dest.name}",
                dest=dest,
            )
            self._jobs[job.id] = job
        threading.Thread(
            target=self._run, args=(job, source, dest, selection), daemon=True
        ).start()
        return job

    def _run(self, job: Job, source, dest, selection: Selection) -> None:
        def progress(message: str, done: int, total: int) -> None:
            job.message, job.done, job.total = message, done, total

        cache = _load_cache()
        try:
            job.report = run_transfer(source, dest, selection, progress, cache)
            job.status, job.message = "done", "Finished"
        except Exception as ex:
            log.error("Transfer %s failed:\n%s", job.id, traceback.format_exc())
            job.status, job.error = "failed", friendly_error(ex)
        finally:
            _save_cache(cache)

    def resolve(
        self,
        job: Job,
        choices: dict[int, dict[int, str]],
        pasted: dict[int, set[int]] | None = None,
    ) -> int:
        """Apply the user's picks ({section index: {item index: id}}); ``pasted`` marks
        items whose id came from a link the user pasted. Returns how many."""
        pasted = pasted or {}
        with self._lock:
            if job.status != "done":
                raise RuntimeError("Wait for the transfer to finish first.")
            if self.busy():
                raise RuntimeError("A transfer is running; try again when it's done.")
            for index in choices:
                if not 0 <= index < len(job.report):
                    raise ValueError(f"No report section #{index}")
            cache = _load_cache()
            try:
                for index, picks in choices.items():
                    job.report[index] = apply_choices(
                        job.dest,
                        job.report[index],
                        picks,
                        cache,
                        pasted.get(index, set()),
                    )
            finally:
                _save_cache(cache)
            return sum(len(p) for p in choices.values())
