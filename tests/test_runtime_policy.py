from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_policy_documents_eval_fallback_and_training_chroot():
    text = (ROOT / "docs/guide/runtime-policy.md").read_text(encoding="utf-8")

    assert "evaluation fallback" in text
    assert "RL training" in text
    assert "use_chroot: false" in text
    assert "use_chroot=true" in text
    assert "require_chroot_training.sh" in text


def test_eval_configs_remain_no_chroot_fallbacks():
    eval_config = (ROOT / "config/sweagent_infer_ascend_officiallike.yaml").read_text(
        encoding="utf-8"
    )
    score_config = (ROOT / "config/sweagent_score_ascend.yaml").read_text(
        encoding="utf-8"
    )

    assert "use_chroot: false" in eval_config
    assert "use_chroot: false" in score_config


def test_skyrl_sandbox_training_requires_chroot():
    one_node = (ROOT / "SkyRL/skyrl-train/examples/swe_agent/run_swe_3B_sandbox.sh").read_text(
        encoding="utf-8"
    )
    two_node = (ROOT / "SkyRL/skyrl-train/examples/swe_agent/2node-3b-16bcz-32n.sh").read_text(
        encoding="utf-8"
    )
    smith_config = (ROOT / "SkyRL/skyrl-train/examples/swe_agent/smith.yaml").read_text(
        encoding="utf-8"
    )

    for script in (one_node, two_node):
        assert "sh/require_chroot_training.sh" in script
        assert "+generator.sweagent.instances.deployment.use_chroot=true" in script

    assert "use_chroot: true" in smith_config


def test_chroot_training_preflight_is_hard_failure():
    script = (ROOT / "sh/require_chroot_training.sh").read_text(encoding="utf-8")

    assert "unshare --mount true" in script
    assert "No-chroot MiniSandbox is supported only as an evaluation fallback" in script
    assert "exit 1" in script
