# Publication triage

The current branch contains a real MiniSandbox contribution mixed with active experiments and
embedded upstream projects. It should not be pushed as one unreviewed snapshot.

## Candidate for the user repository

- `sandboxdev/swesandbox/`, root tests, and tests that exercise the no-chroot/chroot boundary;
- the reviewed `sh/`, `config/`, and documentation needed to reproduce the container-free RL path;
- a small number of focused commits from `feat/chroot-delivery`.

The current branch is one commit ahead of the existing `origin/feat/no-sysadmin` line and much further
ahead of `origin/main`, but it has both staged and unstaged work. Create a review branch or bundle
before deciding which experiments become public history.

## Keep out of the public source commit

- `data/`, `outputs/`, `logs/`, `.runtime*`, caches, local images, and benchmark result dumps;
- `SWE-agent/`, `SWE-ReX/`, `SWE-smith/`, `SWE-bench/`, `SkyRL/`, and `R2E-Gym/` unless explicitly
  used as a pinned dependency or represented by a small reviewed patch;
- the nested `mini-swe-agent` submodule content; keep its commit pointer only;
- one-off launch experiments that have no result/README explaining what they establish.

## Preservation rule

Rejected experiments are not deleted. Record their purpose, source branch/commit, outcome, and any
required external image or dataset in an archive manifest. Public documentation should describe the
relevant negative result without carrying raw logs or images.

Before any push, run the focused no-chroot tests and inspect the staged file list rather than using
`git add .`.
