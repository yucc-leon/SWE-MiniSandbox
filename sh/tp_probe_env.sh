#!/usr/bin/env bash
# Non-destructive environment probe for a freshly-launched tp task machine.
# Verifies everything the chroot smoke (serve -> generation -> scoring) depends on:
# NPU visibility, shared NFS + key paths, chroot capability (unshare --mount),
# the swe-sandbox eval env imports, and the vllm-ascend serving env import.
# Prints a PASS/FAIL line per check and a final summary. Never mutates state.

MINIFORGE_ROOT=${MINIFORGE_ROOT:-/path/to/workspace/miniforge3}
SWE_ENV=${SWE_ENV:-${MINIFORGE_ROOT}/envs/swe-sandbox}
# 8.5 serving candidates, newest first (v013 is known-dirty, excluded). The probe
# tries each and reports which imports vllm so serve_qwen_ascend.sh can use it via VLLM_ENV_NAME.
VLLM_ENV_CANDIDATES=${VLLM_ENV_CANDIDATES:-"vllm-ascend-cann8.5-v017 vllm-ascend-cann8.5"}
SWIFT_ENV=${SWIFT_ENV:-${MINIFORGE_ROOT}/envs/swift-npu-85}
MODEL_PATH=${MODEL_PATH:-/path/to/workspace/models/Qwen3-4B-Instruct-2507}
ASCEND_TOOLKIT_ENV=${ASCEND_TOOLKIT_ENV:-/usr/local/Ascend/ascend-toolkit/set_env.sh}
ASCEND_NNAL_ENV=${ASCEND_NNAL_ENV:-/usr/local/Ascend/nnal/atb/set_env.sh}
VLLM_IMPORT_TIMEOUT=${VLLM_IMPORT_TIMEOUT:-90}

pass=0; fail=0
ok()   { echo "PASS  $*"; pass=$((pass+1)); }
no()   { echo "FAIL  $*"; fail=$((fail+1)); }
info() { echo "....  $*"; }

echo "================ tp env probe ================"
echo "host=$(hostname)  user=$(whoami)  date=$(date -u +%FT%TZ)"
echo "image CANN hint:"; (cat /usr/local/Ascend/ascend-toolkit/latest/version.cfg 2>/dev/null || cat /usr/local/Ascend/version.info 2>/dev/null || echo "  (no version file found)") | sed 's/^/  /'
echo "----------------------------------------------"

# 1. NPU visibility
if command -v npu-smi >/dev/null 2>&1; then
  n=$(npu-smi info -l 2>/dev/null | grep -ciE 'NPU ID' || true)
  npu-smi info 2>&1 | head -20 | sed 's/^/  /'
  if npu-smi info >/dev/null 2>&1; then ok "npu-smi works (reported NPU-ID lines: ${n})"; else no "npu-smi present but failed to run"; fi
else
  no "npu-smi not found on PATH"
fi
echo "----------------------------------------------"

# 2. shared NFS + key paths
mount 2>/dev/null | grep -q ' /sharedata ' && ok "/sharedata is mounted" || no "/sharedata NOT mounted"
for p in "$MINIFORGE_ROOT" "$SWE_ENV" "$MODEL_PATH"; do
  [ -e "$p" ] && ok "path exists: $p" || no "path MISSING: $p"
done
echo "----------------------------------------------"

# 3. chroot capability (mirrors sandbox_deployment.py probe exactly)
if command -v unshare >/dev/null 2>&1; then
  if out=$(unshare --mount echo ok 2>&1) && [ "$out" = "ok" ]; then
    ok "unshare --mount works -> use_chroot:true is viable"
  else
    no "unshare --mount FAILED ('${out}') -> would fall back to no-chroot"
  fi
else
  no "unshare not found -> would fall back to no-chroot"
fi
echo "----------------------------------------------"

# 4. swe-sandbox eval env imports
if [ -x "${SWE_ENV}/bin/python" ]; then
  if out=$(cd /path/to/SWE-MiniSandbox && \
           PYTHONPATH=$PWD/sandboxdev:$PWD/SWE-agent:$PWD/SWE-ReX:${PYTHONPATH:-} \
           "${SWE_ENV}/bin/python" -c 'import sweagent, swerex, swesandbox; print("imports-ok")' 2>&1); then
    ok "swe-sandbox imports sweagent/swerex/swesandbox"
  else
    no "swe-sandbox import failed:"; echo "$out" | tail -5 | sed 's/^/      /'
  fi
else
  no "swe-sandbox python missing: ${SWE_ENV}/bin/python"
fi
echo "----------------------------------------------"

# 5. Ascend toolkit env files (CANN match indicator)
[ -f "$ASCEND_TOOLKIT_ENV" ] && ok "Ascend toolkit set_env present: $ASCEND_TOOLKIT_ENV" || no "Ascend toolkit set_env MISSING: $ASCEND_TOOLKIT_ENV"
[ -f "$ASCEND_NNAL_ENV" ] && ok "Ascend NNAL set_env present: $ASCEND_NNAL_ENV" || info "Ascend NNAL set_env absent (may be optional): $ASCEND_NNAL_ENV"
echo "----------------------------------------------"

# 6. 8.5 vllm-ascend serving envs: try each candidate, report which one imports vllm
info "sourcing Ascend env (8.5 image) for vllm import test..."
[ -f "$ASCEND_TOOLKIT_ENV" ] && source "$ASCEND_TOOLKIT_ENV" >/dev/null 2>&1
[ -f "$ASCEND_NNAL_ENV" ] && source "$ASCEND_NNAL_ENV" >/dev/null 2>&1
serving_ok=""
for cand in $VLLM_ENV_CANDIDATES; do
  py="${MINIFORGE_ROOT}/envs/${cand}/bin/python"
  if [ ! -x "$py" ]; then info "skip ${cand}: python missing"; continue; fi
  info "testing ${cand} (import slow ~30s, timeout ${VLLM_IMPORT_TIMEOUT}s)..."
  if out=$(timeout "$VLLM_IMPORT_TIMEOUT" "$py" -c 'import vllm, vllm_ascend; print(vllm.__version__)' 2>&1); then
    ok "serving env USABLE: ${cand}  (vllm ${out##*$'\n'})"
    [ -z "$serving_ok" ] && serving_ok="$cand"
  else
    no "serving env ${cand} failed to import vllm:"; echo "$out" | tail -4 | sed 's/^/      /'
  fi
done
[ -n "$serving_ok" ] && echo ">>> USE: VLLM_ENV_NAME=${serving_ok} for serve_qwen_ascend.sh" \
  || no "no 8.5 vllm-ascend env imported cleanly — consider sglang-cann8.5 or rebuild"
echo "----------------------------------------------"

# 7. swift NPU training env present (SFT side)
[ -x "${SWIFT_ENV}/bin/python" ] && ok "swift training env present: ${SWIFT_ENV}" || info "swift env absent: ${SWIFT_ENV} (SFT side, not needed for eval smoke)"

echo "================ summary ================"
echo "PASS=${pass}  FAIL=${fail}"
[ "$fail" -eq 0 ] && echo "RESULT: env ready for chroot smoke" || echo "RESULT: ${fail} blocker(s) — see FAIL lines above"
