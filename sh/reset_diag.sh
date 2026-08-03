#!/bin/bash
OUT=/path/to/SWE-MiniSandbox/vendor/reset_diag.out
exec > "$OUT" 2>&1
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
B="PyCQA__flake8.cf1542ce.func_basic__3ojrsgwx"
R="https://github.com/swesmith/PyCQA__flake8.cf1542ce"
cd /tmp; rm -rf rd; mkdir rd; cd rd
echo "=== fetch 代码的方式 ==="
git init -q; git remote add origin "$R"
git fetch --depth 1 origin "$B" 2>&1 | tail -2; echo "fetch exit=$?"
git checkout FETCH_HEAD 2>&1 | tail -2; echo "checkout FETCH_HEAD exit=$?"
echo "=== 逐条 reset(每条单独看 exit + stderr)==="
git status 2>&1 | head -2; echo "status exit=$?"
git restore . 2>&1 | head -2; echo "restore exit=$?"
git reset --hard 2>&1 | head -2; echo "reset --hard exit=$?"
git checkout "$B" 2>&1 | head -2; echo "checkout BRANCH exit=$?"
echo "--- git clean -fdq(关键)---"
git clean -fdq; echo "clean exit=$?"
git clean -fdqn 2>&1 | head -8; echo "(dry-run 看要删啥)"
echo "=== 嵌套 .git / 只读文件 ==="
find . -name .git -maxdepth 3 2>/dev/null | head
find . -maxdepth 2 ! -writable -type f 2>/dev/null | head -3
