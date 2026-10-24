"""Private, bounded, retrieval-only evaluation. Never modifies a ProviderRequest."""
import hashlib
import hmac
import importlib.util
import json
import os
import pathlib
import queue
import threading
import time

QUEUE_SIZE = 8
MAX_QUERY_CHARS = 1024
MAX_QUEUE_AGE = 10
QUERY_SECONDS = 0.5
LOG_BYTES = 2 * 1024 * 1024
LOG_FILES = 3
_workers = {}


def _atomic(path, value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Candidate:
    """One connection, created and used exclusively inside the worker thread."""
    def __init__(self, directory):
        directory = pathlib.Path(directory)
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        for relative in ("kb_query.py", "build/naikuai-kb.sqlite"):
            path = directory / relative
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != receipt["sha256"][relative]:
                raise ValueError("candidate integrity")
        spec = importlib.util.spec_from_file_location("naiwa_unified_r3_candidate", directory / "kb_query.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.kb = module.KB(str(directory / "build/naikuai-kb.sqlite"))
        self.kb.c.execute("PRAGMA query_only=ON")
        self.allowed_facts = {r[0] for r in self.kb.c.execute("select id from facts where answerable=1")}
        self.allowed_chunks = {r[0] for r in self.kb.c.execute("select chunk_id from chunks where review_status='active' and sensitive=0")}

    def lookup(self, question):
        deadline = time.monotonic() + QUERY_SECONDS
        self.kb.c.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            result = self.kb.lookup(question)
        finally:
            self.kb.c.set_progress_handler(None, 0)
        facts, chunks = result["facts"], result["chunks"]
        if result["action"] == "clarify" and (facts or chunks):
            raise ValueError("clarify retrieval invariant")
        if any(f["id"] not in self.allowed_facts for f in facts) or any(c["chunk_id"] not in self.allowed_chunks for c in chunks):
            raise ValueError("candidate filter invariant")
        # Do not serialize `query` or `terms.surface`: those contain user input.
        return {
            "action": result["action"],
            "names": list(dict.fromkeys(n for t in result["terms"] for n in t["names"])),
            "clarify_options": [x["options"] for x in result["clarify"]],
            "facts": [{k: f.get(k) for k in ("id", "name", "kind", "version", "text", "truncated")} for f in facts],
            "facts_skipped": result["facts_skipped"],
            "chunks": [{k: c.get(k) for k in ("chunk_id", "doc_id", "title", "section", "source", "version", "review_status", "truncated", "text")} for c in chunks],
            "versions": result["versions"],
            "summary_review_due": result["summary_review_due"],
        }

    def close(self):
        self.kb.c.close()


class Collector:
    def __init__(self, root, groups):
        self.root = pathlib.Path(root)
        self.groups = frozenset(str(g) for g in groups)
        self.config = json.loads((self.root / "unified-shadow-config.json").read_text(encoding="utf-8"))
        self.directory = self.root / "unified-shadow"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.salt = bytes.fromhex(self.config["salt"])
        if len(self.salt) != 32 or not self.groups:
            raise ValueError("shadow configuration")
        self.jobs = queue.Queue(maxsize=QUEUE_SIZE)
        self.stop = threading.Event()
        self.stats = {"submitted": 0, "processed": 0, "errors": 0, "dropped": 0,
                      "clarify": 0, "candidate_only": 0, "legacy_only": 0,
                      "both_empty": 0, "last_ms": 0, "ready": False}
        status = self.directory / "status.json"
        if status.exists():
            old = json.loads(status.read_text(encoding="utf-8"))
            if old.get("run_id") == self.config["run_id"]:
                for k in self.stats:
                    if k not in ("ready", "last_ms") and isinstance(old.get(k), int):
                        self.stats[k] = old[k]
        self.thread = threading.Thread(target=self._run, name="unified-kb-shadow", daemon=True)
        self.thread.start()

    def submit(self, question, group, legacy_game, legacy_wiki):
        if not self.config.get("enabled") or time.time() >= self.config["expires_at"] or self.stop.is_set():
            return False
        if str(group) not in self.groups or not isinstance(question, str) or not question.strip() or len(question) > MAX_QUERY_CHARS:
            return False
        # Only immutable copies of KB output enter the queue; no event, sender,
        # nickname, image URL, context or provider credentials are retained.
        job = (time.monotonic(), time.time(), question, str(group),
               tuple(x[:1600] for x in legacy_game[:3] if isinstance(x, str)),
               tuple(x[:1600] for x in legacy_wiki[:4] if isinstance(x, str)))
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            self.stats["dropped"] += 1
            return False
        self.stats["submitted"] += 1
        return True

    def _ref(self, value):
        return hmac.new(self.salt, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _status(self):
        _atomic(self.directory / "status.json", {
            **self.stats, "run_id": self.config["run_id"], "pid": os.getpid(),
            "enabled": bool(self.config.get("enabled")) and time.time() < self.config["expires_at"],
            "expires_at": self.config["expires_at"], "delete_after": self.config["delete_after"],
            "queue_depth": self.jobs.qsize(), "updated_at": int(time.time()),
        })

    def _append(self, record):
        path = self.directory / "comparisons.jsonl"
        data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(data) > LOG_BYTES:
            raise ValueError("shadow record budget")
        if path.exists() and path.stat().st_size + len(data) > LOG_BYTES:
            for index in range(LOG_FILES - 1, 0, -1):
                old = path if index == 1 else path.with_name(path.name + "." + str(index - 1))
                new = path.with_name(path.name + "." + str(index))
                if old.exists():
                    os.replace(old, new)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as stream:
            os.chmod(path, 0o600)
            stream.write(data)

    def _run(self):
        candidate = None
        try:
            if not self.config.get("enabled"):
                self._status()
                return
            candidate = Candidate(self.root / "unified-r3")
            self.stats["ready"] = True
            self._status()
            while not self.stop.is_set():
                if time.time() >= self.config["delete_after"]:
                    for path in self.directory.glob("comparisons.jsonl*"):
                        path.unlink(missing_ok=True)
                    self.stats["ready"] = False
                    self._status()
                    return
                try:
                    job = self.jobs.get(timeout=5)
                except queue.Empty:
                    self._status()
                    continue
                try:
                    queued, stamp, question, group, game, wiki = job
                    if time.monotonic() - queued > MAX_QUEUE_AGE or time.time() >= self.config["expires_at"]:
                        self.stats["dropped"] += 1
                        continue
                    started = time.monotonic()
                    result = candidate.lookup(question)
                    elapsed = round((time.monotonic() - started) * 1000, 2)
                    new_hit, old_hit = bool(result["facts"] or result["chunks"]), bool(game or wiki)
                    self._append({
                        "ts": stamp, "run_id": self.config["run_id"], "scope": "authorized_group",
                        "group_ref": self._ref("group:" + group), "question_ref": self._ref("question:" + question),
                        "question_chars": len(question), "legacy": {"game": game, "wiki": wiki},
                        "candidate": result, "elapsed_ms": elapsed,
                    })
                    self.stats["processed"] += 1
                    self.stats["clarify"] += result["action"] == "clarify"
                    self.stats["candidate_only"] += new_hit and not old_hit
                    self.stats["legacy_only"] += old_hit and not new_hit
                    self.stats["both_empty"] += not new_hit and not old_hit
                    self.stats["last_ms"] = elapsed
                except Exception:
                    # No exception text or traceback: SQLite/library errors can
                    # contain fragments of the query or private file paths.
                    self.stats["errors"] += 1
                finally:
                    self.jobs.task_done()
                    job = None
                    question = None
                    self._status()
        except Exception:
            self.stats["errors"] += 1
            self.stats["ready"] = False
            try:
                self._status()
            except Exception:
                pass
        finally:
            if candidate is not None:
                candidate.close()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=6)


def start(root, groups):
    key = str(pathlib.Path(root).resolve())
    if key not in _workers:
        _workers[key] = Collector(root, groups)
    return _workers[key]


def submit(root, question, group, legacy_game, legacy_wiki):
    worker = _workers.get(str(pathlib.Path(root).resolve()))
    if worker is None:
        return False
    return worker.submit(question, group, legacy_game, legacy_wiki)
