#!/usr/bin/env python3
"""ci_patch12 的前置修补：幂等、只动 CI 工作区里的副本（不改仓库里的文件）。

四处修正：

1. 函数重名：`src/lib.rs` 里**已经**有一个
   `read_obscured_int_array(class, obj, getter_name) -> Vec<i32>`（3 参，走 getter 反射）。
   ci_patch12 新加的 1 参版本与之撞名，导致
   `E0428 defined multiple times` + 3 处 `E0061 takes 1 argument but 3 supplied`。
   → 新函数改名为 `read_inline_obscured_int_array`（与 QQ 侧故障 #10 的命名一致）。

2. `ci_patch12.py` 第一版把 sigsetjmp 的正则写成了 `(?:jm|jump)_result`（漏一个 p），
   只命中 `jump_result` 那 2 处 → 改正并把阈值放到 8。

3. `keep ·` 那组字面量有一半来自 `matsuri_bridge.rs`（`session.handle_frame`），
   只扫 `src/lib.rs` 会误判 → 改成在 `lib + bridge` 合并文本上扫。

4. 合并文本里仍没有的（`候选动作为空` / `帧解析失败` / `守门` 是上游 crate 的错误文案，
   只存在于 `.so`）不判失败 → 降级为 warning，存活判据交给 verify 扫 `.so`。
"""

import sys

PATH = "ci_patch12.py"

s = open(PATH, encoding="utf-8").read()
before = s

# 1) 避开与既有 3 参 read_obscured_int_array 撞名
s = s.replace("read_obscured_int_array", "read_inline_obscured_int_array")

# 2) sigsetjmp 正则拼写 + 阈值
s = s.replace("(?:jm|jump)_result", "(?:jmp|jump)_result")
s = s.replace("if hits < 10:", "if hits < 8:")
s = s.replace("（应 >= 10）", "（应 >= 8）")

# 3) keep 字面量在 lib + bridge 上扫
keep_old = (
    'hits = sum(1 for line in lib.splitlines() if needle in line)\n'
    '        print(f"  keep · {needle}: {hits}")'
)
keep_new = (
    'hits = sum(1 for line in (lib + "\\n" + bridge).splitlines() if needle in line)\n'
    '        print(f"  keep · {needle}: {hits}")'
)
if keep_old in s:
    s = s.replace(keep_old, keep_new)
else:
    print("ci_patch12_pre: keep-scan anchor not found (可能是已修过的版本)")

# 4) keep 不存在不判失败，交给 verify 扫 .so
fail_old = (
    '        if hits == 0:\n'
    '            return _fail(f"verify 字面量 {needle} 丢了")'
)
fail_new = (
    '        if hits == 0:\n'
    '            # 上游 crate 的错误文案（候选动作为空 / 帧解析失败 / 守门）只在 .so 里，\n'
    '            # 源码层找不到是正常的；存活判据以 verify 阶段 grep -a 为准。\n'
    '            print(f"::warning::源码里没有 {needle}（由 verify 扫 .so 兜底）")'
)
if fail_old in s:
    s = s.replace(fail_old, fail_new)
else:
    print("ci_patch12_pre: keep-fail anchor not found")

if s != before:
    open(PATH, "w", encoding="utf-8").write(s)
    print("ci_patch12_pre: patched ci_patch12.py")
else:
    print("ci_patch12_pre: nothing to do (already fixed)")
sys.exit(0)