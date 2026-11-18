import pytest

from harness.backends import ROOT, git_commit, git_uncommitted
from harness.r2 import load_r2_config, plan, run_episode
from tests.test_round2 import ctx


def test_uncommitted_files_name_what_makes_a_commit_dirty():
    if git_uncommitted() is None:
        pytest.skip("git is not available")
    probe = ROOT / "harness" / "_provenance_probe.py"
    try:
        probe.write_text("# temporary file written by tests/test_provenance.py\n")
        assert "?? harness/_provenance_probe.py" in git_uncommitted()
        assert git_commit().endswith("-dirty")
    finally:
        probe.unlink(missing_ok=True)
    assert "?? harness/_provenance_probe.py" not in git_uncommitted()


def test_episode_records_carry_the_uncommitted_files(params):
    spec = plan(load_r2_config(), smoke=True, experiments=["AB"])[0]
    c = ctx(params)
    assert run_episode(spec, c)["uncommitted_files"] is None  # a context without the field
    c["uncommitted_files"] = ["?? harness/r4_report.py"]
    assert run_episode(spec, c)["uncommitted_files"] == ["?? harness/r4_report.py"]
