#!/usr/bin/env bash
echo "=== allocation env vars ==="
env | grep -iE 'ASCEND|VISIBLE|DEVICE|NPU|RT_VISIBLE' | sort
echo "=== npu-smi: how many NPUs visible + per-device HBM usage ==="
npu-smi info 2>&1 | grep -iE 'NPU|HBM|Process' | head -40
echo "=== mapping (which physical IDs) ==="
npu-smi info -l 2>&1 | head -30
echo "=== per-device free via npu-smi (used/total MB) ==="
npu-smi info 2>&1 | grep -E '/ 65536|/ [0-9]+' | head
