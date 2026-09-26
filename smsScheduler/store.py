"""jobs.json storage, shared by the GUI and the launchd runner."""
import fcntl
import json
from contextlib import contextmanager
from runtime_paths import data_home

HOME = data_home()
JOBS = HOME / "jobs.json"
LOCK = HOME / ".jobs.lock"


@contextmanager
def locked():
    """Yield the job list under an exclusive lock; changes are saved on exit."""
    HOME.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        jobs = json.loads(JOBS.read_text()) if JOBS.exists() else []
        yield jobs
        tmp = JOBS.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, indent=2))
        tmp.replace(JOBS)


def load():
    with locked() as jobs:
        return [dict(j) for j in jobs]


def find(jobs, job_id):
    return next((j for j in jobs if j["id"] == job_id), None)
