import pytest

from tools.git_tools import GitSafetyError, GitTools


@pytest.fixture
def repo(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    gt = GitTools(workspace_root=ws, protected_branches=["main", "master"], branch_prefix="ai")
    gt._run("init", "-b", "main")
    gt._run("config", "user.email", "test@test.local")
    gt._run("config", "user.name", "Test")
    (ws / "README.md").write_text("hello")
    gt._run("add", "-A")
    gt._run("commit", "-m", "init")
    return gt


def test_commit_on_main_blocked(repo):
    (repo.root / "file.txt").write_text("x")
    with pytest.raises(GitSafetyError):
        repo.commit("should be blocked")


def test_create_branch_then_commit_works(repo):
    res = repo.create_branch("landing-page")
    assert res.ok
    assert res.output.startswith("ai/landing-page-")
    (repo.root / "file.txt").write_text("x")
    add_res = repo.add_all()
    assert add_res.ok
    commit_res = repo.commit("add file")
    assert commit_res.ok


def test_no_push_method_exists(repo):
    assert not hasattr(repo, "push")


def test_no_reset_hard_method_exists(repo):
    assert not hasattr(repo, "reset_hard")


def test_no_clean_force_method_exists(repo):
    assert not hasattr(repo, "clean_force")


def test_status_and_diff_work_on_protected_branch(repo):
    # read-only ops must work regardless of branch
    assert repo.status().ok
    assert repo.diff().ok
    assert repo.log().ok


def test_init_repo_if_needed_on_fresh_directory(tmp_path):
    ws = tmp_path / "fresh"
    ws.mkdir()
    (ws / "index.html").write_text("<html></html>")
    gt = GitTools(workspace_root=ws, protected_branches=["main", "master"], branch_prefix="ai")
    assert not gt.is_repo()

    res = gt.init_repo_if_needed()
    assert res.ok
    assert gt.is_repo()
    assert gt.current_branch() == "main"
    # the pre-existing file should be committed, not lost
    log_res = gt.log()
    assert log_res.ok
    assert "Initial commit" in log_res.output


def test_init_repo_if_needed_is_noop_on_existing_repo(repo):
    res = repo.init_repo_if_needed()
    assert res.ok
    assert "already a git repository" in res.output
    # must not have created a second commit
    log_res = repo.log(max_count=10)
    assert log_res.output.count("\n") == 0  # only the single "init" commit from the fixture


def test_has_uncommitted_changes_false_on_clean_repo(repo):
    assert repo.has_uncommitted_changes() is False


def test_has_uncommitted_changes_true_when_dirty(repo):
    (repo.root / "untracked.txt").write_text("x")
    assert repo.has_uncommitted_changes() is True
