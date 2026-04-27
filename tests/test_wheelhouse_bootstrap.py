from pathlib import Path


def test_bootstrap_wheelhouse_includes_django_runtime_deps():
    script = Path("sh/bootstrap_minisandbox_wheelhouse.sh").read_text(encoding="utf-8")

    assert "asgiref" in script
    assert "sqlparse" in script
    assert "pytz" in script
    assert "chardet" in script
