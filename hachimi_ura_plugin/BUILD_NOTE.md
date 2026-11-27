# 构建触发记录

本文件只用来触发 `build-so` workflow（它的 `paths` 过滤只监听 `hachimi_ura_plugin/**`），
不参与编译，可随时增删。

## 2026-11-27 · ci_patch5（GetBaseWeight 直连 matsuri）

触发原因：`ci_patch5.py` 与 workflow（`99d7a598`）提交后没有生成对应 run
（release 里最新仍是 `36d8bd04`，大小 14,362,576，与 `180764bd` 一致，
说明 patch5 还没被应用）。这里补一次改动让 workflow 重新排队。

本轮要验证的三件事：

1. `/api/ai/matsuri` 端点存在，`state` 从 0 变 1
2. `weights[]` 出现 matsuri 给的归一化分（10~200），未给出的位为 0
3. 自动育成的选择跟着 matsuri 走，而不是原版 omakase

## 已确认的映射（游戏 2.33.2 / setting 1501）

```
150101 → 速度    Train(0)
150102 → 耐力    Train(1)
150103 → 力量    Train(2)
150104 → 根性    Train(3)   排除法
150105 → 智力    Train(4)
150120..150124 → ActTrain(0..4)  演目下标，与 acts[].train 一致
```