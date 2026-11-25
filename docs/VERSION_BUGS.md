# SO 插件版本编年史：Bug 与编译问题全记录

来源：`uma-shiyanshi/hlpatch` 319 条 commit（其中 140 条 fix/build 类）、34 份 docs 诊断文件、
232 个 release 的构建线（2026-07 ~ 2026-10，v3.24.2 → v3.28.15）。
用途：合并 L1/L2 与回收 3.29.x 时逐条对照，防止旧坑重踩。

---

## 一、运行时崩溃类（最高优先级）

| 版本 | 症状 | 根因 | 修法 / commit |
|---|---|---|---|
| v3.24.2 | 进事件即崩 | `StoryManager.SetStory` hook 签名与实参不匹配 | 0a51d4c7 |
| v3.24.3~4 | 随机闪退 | `CommandInfoArray` 用 getter 调用取引用，触发 GC 边界 | 回退 getter，改纯内存读（a7413ed4 / f380c1ea） |
| v3.24.7 | 无 I-Cache 刷新导致跳飞 | inline hook 改了代码段却没 flush | `catch_unwind` + I-Cache flush（3ee02270） |
| v3.24.48 | 开机崩 | `libc open/openat/pread` inline hook 与 Unity 加载器冲突 | 移除，改 hook-free 方案（9756ac40） |
| v3.24.49 | 开机崩 | `prepare_v2/exec` SQL hook 安装时机过早 | 二分关闭，确认 v3.24.46 可启动（97563764） |
| v3.24.52 | **开机 ANR** | 崩溃日志上传在初始化线程里跑阻塞 curl（无 timeout） | 上传移出主线程 + 超时（9c898af1） |
| v3.25.x | `get_EquipSupportCardArray` 重复 invoke → SIGSEGV | 同帧两次拿同一数组，第二次引用已失效 | 去重（9a85169b） |
| v3.27.23 | 裸内存读段错误带走进程 | 无 SIGSEGV 兜底 | 三连：v1 原始 → v2 wrapper-rename（零函数体假设）→ v3 rename-first（修 v2 count=2）（eb87572a / 1ea40b2c / b8d0ae42） |
| v3.28.11 | **进育成闪退** | factor-badge detour 里 `usize` 当 `u128` 用（ABI 不匹配） | 4b5e06d8 |
| v3.28.15 | 主界面反复崩（CRASH at step 209/189） | 后台 summary 在非育成态读到已释放的 `WorkSingleModeData/Character`，siglongjmp 兜底变自杀循环 | 加两道门禁（klass 校验 + `_totalTurnNum>0`），SIGSEGV 冷却 60s→600s |

**通用规则**：任何 hook 进 il2cpp 对象读取前，先校验 `klass` 与关键计数字段；任何 hook 回调必须
`catch_unwind` 且失败时返回原值；任何 SIGSEGV 兜底必须带分钟级冷却。

---

## 二、IL2CPP 字段读错类（数据虚高/全 0/错位）

| 版本 | 症状 | 根因 | 修法 / commit |
|---|---|---|---|
| v3.24.5~8 | Ramen gains 全 0 | `ObscuredInt` 偏移错、`EvaluationList` 是 `List` 不是 `Array` | 0f282760 / 1cd57c85 / 41d08987 / 48e308fa |
| v3.24.11 | 羁绊读不到 | 支援卡 `partner_id` 与 `support_position` 类型混用（后者是 `String` 不是 `Option<i32>`） | a6ae346c |
| v3.24.x | 训练收益错位 | 明文与混淆两套 params 偏移混用 | 93c78af9 |
| v3.24.61+ | command_id 语义反了 | UI 顺序（速耐力根智）与游戏内部 id 顺序相反：105=耐力、102=力量 | 109110c7 |
| v3.24.6x | TargetType 标签互换 | 3=Power、4=Guts 与文档相反 | 994bb7c7 |
| v3.27.x | Ramen 601-605 全错 | 凭 UI 猜；实证按 MDB `base_command_id`：602=耐力 603=力量 604=根性 | 1f4002d4 |
| v3.28.2 | params_inc_dec 元素布局错 | 实为双 `ObscuredInt`：`TargetType@0x10` / `Value@0x24` | 74bc64f9 |
| v3.28.10 | 因子序号显示错 | 双 hook 目标选错（`OFF_PARTS_LIST_CAROUSEL` 常量写错） | 3dedcd0f + grep 修正 |
| v3.24.9 | `month/half` 读不到 | 它们是计算属性（无字段偏移） | 恢复 getter（66757e64） |
| v3.24.17 | RNG 观察为空 | 观测点取到无效 tuple；完整转移需同步捕获 | 73aa2d11 / 75bf0914 |

**通用规则**：偏移与语义一律以「同版本 MDB + 内存实测」为准，不抄上一版；字符串/集合/结构体
先确认 `klass` 与泛型实参再读。

---

## 三、Rust 编译错误类（CI 红叉主因）

| 错误码 / 现象 | 场景 | 修法 / commit |
|---|---|---|
| `E0308` | `handle_http` 返回 `()` 却在分支里 `return String` | 改 if/else 表达式（48f85ac3） |
| `E0425` | 删字段后仍引用 `cmd_elem_class` / `pid_class` | 清理引用（e8b704cb） |
| `E0133` | `ura_log` 在 `catch_unwind` 闭包外调用需 `unsafe` | 加 unsafe 块（fb02aa1b） |
| 未闭合定界符 | 手改 `catch_unwind` 括号 + `asm!` cfg 守卫 | ee864eaa |
| `__builtin___clear_cache` 不存在 | 交叉编译到 aarch64 | 改 inline asm + 括号修复（902dbdd2） |
| `column_count` 类型不匹配 | 返回 `usize` 却 `unwrap_or`；`null`→`null_mut` | 1f6ee3c3 |
| `read_inherit_compat` 编译错 | 字段类型假设错误 | 5d994d9f |
| `--locked` 与 Cargo.lock 不一致 | 发布链强制 `--locked`，新增依赖（sha2/digest）没同步 lock | 先无 `--locked` 生成再锁定（b967356e / 8904dfbe / 25c92e64） |

**通用规则**：`hachimi_ura_plugin` 是 `cdylib`，交叉编译目标 `aarch64-linux-android`；
任何改 `Cargo.toml` 的提交必须同时提交 `Cargo.lock`，否则发布链必红。

---

## 四、CI / 发布链问题

| 现象 | 根因 | 修法 / commit |
|---|---|---|
| `HTTP 403: Resource not accessible by integration` | workflow 缺 `contents: write`，release 创建被拒 | 加 permissions（V32711_RELEASE_DIAGNOSTICS.txt） |
| 发布成功但资产没更新 | 同名 asset 已存在，未替换 | 先删后传（be03d6da） |
| `Prepare 产出 BUILD-MANIFEST.txt` 引用漏后缀 | 文件名硬编码 | e1c0302d |
| `cargo.log` 路径错 | build 步 `working-directory` 是子目录，引用要补全路径 | 721c0862 |
| action-gh-release v1 偶发 500 | v1 无重试 | 升 v3（带重试 + Node24）（85601b8f） |
| 分支构建污染 release | 分支 push 也会跑发布 | 分支构建禁止改 release（f93486d7） |
| qualification grep 恒 false | 脚本产物是 `u64`，workflow 写 `i64` | 7db60f78 |
| apply 脚本被覆盖导致断链 | 两个 apply 脚本必须保持 a8ed58c 原版 | 5c3b0f0e / 98dcdb4d |
| 版本号永远"有更新" | `current_ver` 与 `PLUGIN_VERSION` 不同步 | 51a27b19 |
| 版本号双源 | 手动 bump 易漏 | 单源 + 幂等 bump 脚本（b6411248） |
| **Actions 存储配额满** | artifact + logs 堆积（1032 runs / 30 artifacts） | 本文档落库后清 runs，CI 不再 upload-artifact 存 so |
| **LFS smudge 失败 128** | 上游 `reverse/` 走 LFS，runner 没装 lfs | `GIT_LFS_SKIP_SMUDGE=1`（本次合并线新增） |

**通用规则**：发布链只允许「不可变 release + 显式 SHA」；workflow 里禁止 `--locked` 与
未同步 lock 同时出现；任何 grep 断言都要打印实际值，避免静默 false。

---

## 五、协议 / 观测层踩坑

| 版本 | 问题 | 修法 |
|---|---|---|
| v3.25.x | 把 `wire` 当"不可解析"而放弃 | 明确 wire 只是来源标签，仍可提 endpoint/类名（PROTOCOL_LAYERED_DECODE_CONTRACT.md） |
| v3.26.x | sniff v2 六项能力在 release 里失效 | 单独 patch 脚本 + 校验分支（c5b55bc7 / 1d3ddb47） |
| v3.27.x | md5log 路由被 boot gate 挡 | 加白名单 + toggle 时置 `GAME_INITIALIZED`（9e7c83fc） |
| v3.27.x | `read_string` 返回作用域错 | e5a55130 |
| v3.27.x | `runtime_invoke` 值参数打包错 | 8e390cc5 / f43a47d5 / 9606d04c |

---

## 六、合并 L1 × L2 时必须复查的十条

1. `Cargo.lock` 与 `Cargo.toml` 同步（冲突文件已实测为这两个 + lib.rs）
2. 端点表去重：两版都注册 `/debug/*` 时只留一份，且 `hlpatch_endpoints.txt` 对账
3. SIGSEGV guard 保留 v3.27.23-v3（rename-first）与 600s 冷却
4. `catch_unwind` 包裹所有新 hook；失败返回原值
5. `usize`↔`u128` ABI 一一对应（v3.28.11 教训）
6. `ObscuredInt` 偏移按当前游戏 2.33.0 实测，不继承 3.28.x 常量
7. 非育成态门禁：`klass == WorkSingleModeData` 且 `_totalTurnNum > 0`
8. 版本号单源，`current_ver` 与 `/health` 一致
9. CI 不再 `upload-artifact` 存 so
10. 上游 LFS 目录一律 `GIT_LFS_SKIP_SMUDGE=1`
