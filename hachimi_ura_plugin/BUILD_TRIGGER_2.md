# 构建触发（2026-11-28 13:26）

触发 `build-so`：验证「函数改名 + verify 判据换成真实字面量」这一轮能否变绿。

前情：

- `ci_patch12.py` 的 7 项修复已全部落地，`cargo build` 上一轮已通过（`so_size=14,387,680`，2m01s）。
- 连续两轮红都是**判据**问题，不是代码：
  1. 源码锚点检查里仍写 `fn read_obscured_int_array`，但函数已改名 `read_inline_obscured_int_array`
     （避开 `src/lib.rs:2512` 已有的 3 参同名函数：`E0428` + 3×`E0061`）。
  2. verify 里新增的四项是**符号名**，LTO 内联后不会以字符串留在 `.so` → 换成真实字面量
     `il2cpp_thread_attach` / `push_read.txt` / `uma_so/matsuri_data`。

绿了之后：产物在 release tag `builds`，文件名 `libhachimi_ura-<VER>-<sha>.so`。