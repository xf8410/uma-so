# SO 版本合并与仓库清理判读（2026-11-25）

## 1. 现场事实

| 项 | 值 |
|---|---|
| 公开仓 | `uma-shiyanshi/hlpatch`（旧名 xf8410/hlpatch，已重定向） |
| 分支 | **74** 条（README 写 52，实际 74） |
| release | **100** 个（v3.24.22 ~ v3.28.15，245 个资产） |
| main 头 | `4b5e06d8`（v3.28.15，2026-10-20） |
| 装机 SO | 3.29.8（游戏 2.33.0，桥 18765 正常） |
| 本地产物 | 3.29.12（`/sdcard/Download/libhachimi_ura.so`，sha af53f07b…） |

## 2. 分支判读（compare main）

**纯领先 main（behind=0，功能未合并）只有 3 条：**

| 分支 | ahead | 内容 | 处置 |
|---|---|---|---|
| `workbench/resource-hooks-and-strip-factor` | 7 | feat(3.28.16)：剧本15 支持 / 调试端点 / summary 落文件；lib.rs +1962 行 | 并入 L1（已存 `vendor/`） |
| `workbench/factor-select-badge` | 6 | 因子選択屏编号 + push_loop 安装顺序修复（`factor_index_badge.rs` +122 行） | 并入 L1 |
| `workbench/rng-consumption-probe-3.29.1` | 3 | RNG 消费点插桩 + 登录器认证观测 | 已作为 L1 基线 |

其余 71 条：`diverged` 的历史发布线（release-v3-27-x / publish-* / fix-*-release）、诊断线
（diag/*、grep-ui-hooks）、备份线（backup/cloud-*）、以及 `behind` 的过期线。
提交绝大多数已进入 main 或被后续版本覆盖，不再逐条合并，只归档 head sha。

## 3. 缺口：3.29.8 / 3.29.12 源码不在任何公开分支

公开仓最新 commit 2026-10-20（3.28.15），tag 只到 3.28.15。装机 3.29.8 与本地 3.29.12 是未回推构建。
处置：回收源码后覆盖 L1；回收前 L1 以 3.29.1 为基线。

## 4. 合并矩阵

```
L1(3.29.1) × vendor(3.28.16)      → merge-lines.yml，冲突预计在 lib.rs 端点表与 /summary 落文件
L1         × factor-select-badge   → 仅 factor_index_badge.rs，可直接叠
L1         × build-3.29.x(回收后)  → 以 3.29.x 为准，反向吸收 3.28.16 端点
```

## 5. Bug 检查清单（合并后必过）

1. `cargo check --target aarch64-linux-android`（CI 门禁）
2. `/debug/selftest` 全绿；`/health` 版本号与构建一致
3. 端点去重：`/api/*`、`/debug/*` 与 `hlpatch_endpoints.txt` 对账，禁止重复注册
4. hook 同址冲突：与 Hachimi-Edge 178 个 umamusume hook 比对（`MasterDataManager` / `MasterSingleModeTurn` / `TimeUtil` 禁碰）
5. SIGSEGV guard：v3.27.23 三连修复必须保留（冷却 600s 版本）
6. 育成 10 局回归：不崩、不误选、训练日志 command_id 连续

## 6. 清理清单（执行前归档 head sha / tag）

### release：保留 5，删 95
保留 `v3.28.15` `v3.28.14` `v3.28.13` `v3.28.12` `v3.28.11`；其余 95 个删除。

### 分支：保留 5，删 69
保留 `main`、`workbench/resource-hooks-and-strip-factor`、`workbench/rng-consumption-probe-3.29.1`、
`workbench/factor-select-badge`、`workbench/slim`；其余 69 条（head sha 记入 `docs/ARCHIVE_BRANCHES.md`）。

### 仓库：候选 14（确认后删）
`uma-shiyanshi/hlpatch-lite`、`hlpatch-so-recovery`、`hlpatch-reasoning-log`、`hlpatch-readall-spec`、
`hlpatch-matsuri-panorama`、`uma-so-reforge`、`so-chonggou`、`umahachimios`、`uma-liuchang-hachimiso`、
`uma-mech-probe`、`uma-sim-residual`、`hachimi-template-gala`、`hachimi-edge-gala`、`xf8410/so-history-backup`

保留：`hlpatch`、`Hachimi-Edge`、`uma-hachimi-mod`、`hachimi-edge-cn`、`uma-il2cpp-metadata`、
`xf8410/Dsh-so-Agora`、本仓。
