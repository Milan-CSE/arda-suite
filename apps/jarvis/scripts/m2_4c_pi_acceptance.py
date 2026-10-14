"""Physical M2.4C PatchWorker acceptance harness for rootless Podman."""
from pathlib import Path
import hashlib
import sys

sys.path.insert(0, "/workspace/source")
from arda.patch_worker import AllowedFile, PatchEdit, PatchStatus, PatchWorker, PatchRequest


ROOT = Path("/workspace/work/fixture")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def request(root: Path, target: Path, *, episode="episode-pi") -> PatchRequest:
    before = target.read_bytes()
    return PatchRequest(
        episode_id=episode,
        patch_id="patch-pi-1",
        explorer_run_id="run-pi-1",
        base_tree_hash=PatchWorker._tree_hash(PatchWorker._snapshot(root)),
        allowed_files=(AllowedFile("module.py", sha(before)),),
        edits=(PatchEdit("replace_exact", "module.py", sha(before), "return 1", "return 2"),),
    )


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    target = ROOT / "module.py"
    target.write_text("def answer():\n    return 1\n", encoding="utf-8")
    worker = PatchWorker()

    first = worker.apply(request(ROOT, target), ROOT)
    check("scoped patch applied", first.status is PatchStatus.APPLIED)
    check("episode linked", first.episode_id == "episode-pi")
    check("diff produced", "--- a/module.py" in first.unified_diff)
    check("hashes produced", len(first.result_tree_hash) == 64 and len(first.file_hashes["module.py"]) == 64)
    check("no verification claim", not hasattr(first, "verification_status"))

    target.write_text("def answer():\n    return 1\n", encoding="utf-8")
    second = worker.apply(request(ROOT, target), ROOT)
    check("same fixture deterministic", first.unified_diff == second.unified_diff and first.file_hashes == second.file_hashes)

    stale_request = request(ROOT, target)
    target.write_text("def answer():\n    return changed\n", encoding="utf-8")
    stale = worker.apply(stale_request, ROOT)
    check("preimage mismatch rejected", stale.status is PatchStatus.PREIMAGE_MISMATCH)

    target.write_text("def answer():\n    return 1\n", encoding="utf-8")
    outside = ROOT / "outside.py"
    outside.write_text("x\n", encoding="utf-8")
    scoped = request(ROOT, target)
    scoped = PatchRequest(**{**scoped.__dict__, "edits": (PatchEdit(
        "replace_exact", "outside.py", sha(outside.read_bytes()), "x", "y"),)})
    result = worker.apply(scoped, ROOT)
    check("out of scope rejected", result.status is PatchStatus.SCOPE_VIOLATION)
    check("out of scope unchanged", outside.read_text(encoding="utf-8") == "x\n")

    link = ROOT / "link.py"
    link.unlink(missing_ok=True)
    link.symlink_to("/etc/passwd")
    linked = request(ROOT, target)
    linked = PatchRequest(**{**linked.__dict__, "edits": (PatchEdit(
        "replace_exact", "link.py", "0" * 64, "root", "tampered"),)})
    result = worker.apply(linked, ROOT)
    check("symlink escape rejected", result.status is PatchStatus.SCOPE_VIOLATION)

    target.write_text("def answer():\n    return 1\n", encoding="utf-8")
    bounded = request(ROOT, target)
    bounded = PatchRequest(**{**bounded.__dict__, "edits": (PatchEdit(
        "replace_exact", "module.py", sha(target.read_bytes()), "return 1", "return 222222"),)})
    bounded = PatchRequest(**{**bounded.__dict__, "limits": bounded.limits.__class__(1, 1, 1)})
    result = worker.apply(bounded, ROOT)
    check("failed patch rolled back", result.status is PatchStatus.REJECTED and "return 1" in target.read_text(encoding="utf-8"))

    try:
        Path("/workspace/source/host-sentinel").write_text("escape", encoding="utf-8")
    except OSError:
        print("PASS source mount host write rejected")
    else:
        raise AssertionError("source mount write escaped")


if __name__ == "__main__":
    main()
