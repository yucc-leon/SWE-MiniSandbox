from swesandbox.prep_plan import build_bucket
from swesandbox.prep_plan import summarize_instances
from swesandbox.prep_run import to_simple_batch_instance


def test_build_bucket_prefers_explicit_python_version():
    item = {
        "instance_id": "django__django-1",
        "repo": "django/django",
        "python_version": "3.6",
        "image_name": "django-py36",
    }
    bucket = build_bucket(item, data_type="swesmith")
    assert bucket.repo_id == "django/django"
    assert bucket.python_version == "3.6"
    assert bucket.image_name == "django-py36"


def test_summarize_instances_groups_by_repo_python_image():
    instances = [
        {
            "instance_id": "a",
            "repo": "sympy/sympy",
            "python_version": "3.9",
            "image_name": "sympy-py39",
        },
        {
            "instance_id": "b",
            "repo": "sympy/sympy",
            "python_version": "3.9",
            "image_name": "sympy-py39",
        },
        {
            "instance_id": "c",
            "repo": "django/django",
            "python_version": "3.6",
            "image_name": "django-py36",
        },
    ]
    summary, prewarm = summarize_instances(instances, data_type="swesmith")
    assert len(summary) == 2
    assert summary[0]["bucket_key"] == "sympy/sympy/3.9/sympy-py39"
    assert summary[0]["count"] == 2
    assert len(prewarm) == 2
    assert prewarm[0]["_prep_bucket"] == "sympy/sympy/3.9/sympy-py39"


def test_to_simple_batch_instance_uses_dataset_defaults():
    item = {
        "instance_id": "demo",
        "repo": "sympy/sympy",
        "FAIL_TO_PASS": ["tests::test_demo"],
    }
    instance = to_simple_batch_instance(item)
    assert instance["repo_type"] == "github"
    assert instance["image_name"] == "default"
    assert instance["traj_id"] == "demo"
    assert instance["base_commit"] == "demo"
    assert instance["extra_fields"] == {"fail_to_pass": ["tests::test_demo"]}
