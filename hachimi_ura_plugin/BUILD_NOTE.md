# 构建触发记录

本文件只用来触发 `build-so` workflow（它的 `paths` 过滤只监听 `hachimi_ura_plugin/**`），
不参与编译，可随时增删。

## 2026-11-27 · ci_patch6（读马娘 + 支援卡）

matsuri 初始化必须要有 `uma` 和 6 张支援卡，否则卡在编组门禁 E4。
之前 `support_cards` 一直空，是因为把值类型元素当引用取（`ptr: null`）。

本轮改动：

- `mt_read_identity()`：`CharaData +160` 值类型数组，元素内联 128 字节，
  `+16 position / +20 supportCardId / +24 limitBreakCount`；
  卡号按 `ai_config.toml` 口径组合成 `supportCardId * 10 + limitBreak`
- `mt_recompute()` 用真实身份初始化，身份指纹变化才重建 session
- `/api/ai/deck` 输出 uma / cards / 每个元素的原始三元组

## 已确认的映射（游戏 2.33.2 / setting 1501）

```
150101 → 速度    Train(0)
150102 → 耐力    Train(1)
150103 → 力量    Train(2)
150104 → 根性    Train(3)   排除法
150105 → 智力    Train(4)
150120..150124 → ActTrain(0..4)  演目下标，与 acts[].train 一致
```

## 现场核对顺序

```
/api/ai/deck       uma / cards / raw 三元组（先确认卡组读对了）
/api/ai/matsuri    state=1 且 weights 非全 0（matsuri 真的在出分）
/api/ai/weightcalls   hits 涨、ret 跟着 matsuri（不是原版 10/200）
```