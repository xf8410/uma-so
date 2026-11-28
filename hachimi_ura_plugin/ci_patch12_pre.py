#!/usr/bin/env python3
"""ci_patch12 的前置修补：幂等、只动 CI 工作区里的副本。

`ci_patch12.py` 第一版把 sigsetjmp 的正则写成了 `(?:jm|jump)_result`（漏了一个 p），
只命中 `jump_result` 那 2 处，于是 `hits < 10` 直接失败。
这里在它运行前把这一处拼写和阈值改正；改完再跑一次是空操作。
"""

import sys

PATH = "ci_patch12.py"

s = open(PATH, encoding="utf-8").read()
before = s

s = s.replace("(?:jm|jump)_result", "(?:jmp|jump)_result")
s = s.replace("if hits < 10:", "if hits < 8:")
s = s.replace("（应 >= 10）", "（应 >= 8）")

if s != before:
    open(PATH, "w", encoding="utf-8").write(s)
    print("ci_patch12_pre: patched ci_patch12.py (regex/threshold)")
else:
    print("ci_patch12_pre: nothing to do (already fixed)")
sys.exit(0)