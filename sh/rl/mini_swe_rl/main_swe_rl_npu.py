"""NPU entry for SWE-MiniSandbox RL = SkyRL BasePPOExp + our adapted MiniSweAgentGenerator.

Adapted from SkyRL examples/mini_swe_agent/main_mini_swe.py, with:
- NPU activation (npu_support.patch_cuda) imported FIRST (cuda->npu, nccl->hccl, ray gpu->npu).
- our generator (mini_swe_generator in sh/rl/mini_swe_rl/, with container-free env + fixed scorer).

Run via run_swe_rl_npu.sh (sets PYTHONPATH so `mini_swe_generator` + deployment stack import).
"""
import npu_support.patch_cuda  # noqa: F401  MUST be first — patches cuda->npu before torch loads
npu_support.patch_cuda.ensure_patched()

import hydra
import ray
from omegaconf import DictConfig, OmegaConf
from skyrl_train.entrypoints.main_base import BasePPOExp, config_dir, validate_cfg
from skyrl_train.utils import initialize_ray

from mini_swe_generator import MiniSweAgentGenerator


class SWERLPPOExp(BasePPOExp):
    def get_generator(self, cfg, tokenizer, inference_engine_client):
        return MiniSweAgentGenerator(
            generator_cfg=cfg.generator,
            skyrl_gym_cfg=OmegaConf.create({"max_env_workers": int(cfg.generator.get("max_env_workers", 4))}),
            inference_engine_client=inference_engine_client,
            tokenizer=tokenizer,
            model_name=self.cfg.trainer.policy.model.path,
        )


@ray.remote(num_cpus=1)
def skyrl_entrypoint(cfg: DictConfig):
    exp = SWERLPPOExp(cfg)
    exp.run()


@hydra.main(config_path=config_dir, config_name="ppo_base_config", version_base=None)
def main(cfg: DictConfig) -> None:
    validate_cfg(cfg)
    initialize_ray(cfg)
    ray.get(skyrl_entrypoint.remote(cfg))


if __name__ == "__main__":
    main()
