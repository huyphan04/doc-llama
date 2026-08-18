import pytest

from tools.filesystem import FilesystemTools


@pytest.fixture
def fs(tmp_path):
    return FilesystemTools(workspace_root=tmp_path / "ws", denied_names=[".env"], max_file_size_kb=200)


def test_write_then_read(fs):
    res = fs.write_file("src/app.py", "print('hi')")
    assert res.ok
    res2 = fs.read_file("src/app.py")
    assert res2.ok
    assert res2.data["content"] == "print('hi')"


def test_write_no_overwrite_conflict(fs):
    fs.write_file("a.txt", "1")
    res = fs.write_file("a.txt", "2", overwrite=False)
    assert not res.ok
    assert "already exists" in res.error


def test_read_missing_file(fs):
    res = fs.read_file("nope.txt")
    assert not res.ok


def test_edit_file_unique_match(fs):
    fs.write_file("a.py", "def foo():\n    return 1\n")
    res = fs.edit_file("a.py", "return 1", "return 2")
    assert res.ok
    assert fs.read_file("a.py").data["content"] == "def foo():\n    return 2\n"


def test_edit_file_ambiguous_match_rejected(fs):
    fs.write_file("a.py", "x = 1\nx = 1\n")
    res = fs.edit_file("a.py", "x = 1", "x = 2")
    assert not res.ok
    assert "not unique" in res.error


def test_edit_file_no_match(fs):
    fs.write_file("a.py", "x = 1\n")
    res = fs.edit_file("a.py", "y = 2", "y = 3")
    assert not res.ok
    assert "not found" in res.error


def test_delete_file(fs):
    fs.write_file("a.py", "x")
    res = fs.delete_file("a.py")
    assert res.ok
    assert not fs.read_file("a.py").ok


def test_list_files(fs):
    fs.write_file("src/a.py", "1")
    fs.write_file("src/b.py", "2")
    fs.write_file("README.md", "readme")
    res = fs.list_files(".")
    assert res.ok
    assert "README.md" in res.data
    assert "src/a.py" in res.data


def test_search_code(fs):
    fs.write_file("src/a.py", "def handler():\n    return TODO\n")
    fs.write_file("src/b.py", "def other():\n    return 1\n")
    res = fs.search_code("TODO")
    assert res.ok
    assert len(res.data) == 1
    assert res.data[0]["file"] == "src/a.py"


def test_denied_name_via_tool(fs):
    res = fs.write_file(".env", "SECRET=1")
    assert not res.ok
    assert "denied" in res.error.lower()


def test_path_escape_via_tool(fs):
    res = fs.read_file("../../etc/passwd")
    assert not res.ok
