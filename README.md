# uma-so-private

URA / Hachimi SO 插件的**明文源码唯一真源**与育成数据落库点。

## 为什么有这个仓

原 `uma-shiyanshi/hlpatch` 累计 74 条分支、100 个 release（v3.24.22 → v3.28.15），
大部分是历史发布线、诊断线、备份线，源码与二进制混在一起，翻不动也合不动。
本仓把**能跑的源码明文**收进来，版本线收敛为三条，历史版本只留归档引用。

## 目录

```
hachimi_ura_plugin/     现役插件源码（基线 = 3.29.1 分支）
vendor/                 对照基线（3.28.16 全功能版 lib.rs，含未合并功能）
docs/                   合并判读、清理记录、端点契约
tools/                  导出/归档脚本与契约
data/runs/              对局数据（育成结束自动落库，蒸馏原料）
data/distill/           蒸馏集（由 runs 汇总）
.github/workflows/      合并线、数据汇总、构建
```

## 版本线（收敛后只留三条）

| 线 | 源码 | 状态 |
|---|---|---|
| L1 `main-3.28` | `hachimi_ura_plugin/`（3.29.1 基线） | 可构建，已明文入库 |
| L2 `feat-3.28.16` | `vendor/lib_3.28.16_reshooks.rs` | 剧本15 / 调试端点 / summary 落文件，待并入 L1 |
| L3 `build-3.29.x` | **待回收** | 装机 3.29.8 / 本地 3.29.12 的源码不在公开仓任何分支，回收后覆盖 L1 |

## 合并规则

1. 只用 `merge-lines.yml` 做三方合并，冲突文件作为 artifact 上传，人工解决后 PR。
2. 合并后必须过三道门：`cargo check`（CI）→ 设备 selftest（`/debug/selftest`）→ 育成 10 局不崩。
3. 任何 hook 新增前，核对 Hachimi-Edge 的 178 个 umamusume hook，禁止与 `MasterDataManager` / `MasterSingleModeTurn` / `TimeUtil` 同址。

## 数据管线

育成结束 → SO 导出 `run.json` → `data/runs/<YYYY-MM>/<run_id>.json` → 每日汇总 `data/distill/<date>.jsonl` → matsuri 离线蒸馏。
契约见 `tools/export_runs.md`。
