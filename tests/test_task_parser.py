from agent.task_parser import parse_task_file, parse_task_text


def test_parse_spec_example_task():
    task = parse_task_file("tasks/landing-page.md")
    assert "landing page" in task.description.lower()
    assert len(task.requirements) == 4
    assert "Responsive design required." in task.requirements
    assert len(task.viewports) == 3
    assert {(v.width, v.height) for v in task.viewports} == {(375, 812), (768, 1024), (1440, 900)}
    assert len(task.acceptance_criteria) == 8
    assert "Build passes." in task.acceptance_criteria


def test_parse_minimal_task():
    text = "# Task\n\nDo the thing.\n"
    task = parse_task_text(text, task_id="t1")
    assert task.description == "Do the thing."
    assert task.requirements == []
    assert task.viewports == []


def test_parse_preserves_unknown_sections():
    text = "# Task\n\nDo it.\n\n## Notes\n\nSome extra context.\n"
    task = parse_task_text(text, task_id="t2")
    assert "notes" in task.extra_sections
    assert "extra context" in task.extra_sections["notes"]


def test_numbered_acceptance_criteria():
    text = "# Task\n\nDo it.\n\n## Acceptance Criteria\n\n1. First thing\n2. Second thing\n"
    task = parse_task_text(text, task_id="t3")
    assert task.acceptance_criteria == ["First thing", "Second thing"]
