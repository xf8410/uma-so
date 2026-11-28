# 赛马娘 SO 插件 · 实测新增故障记录（持续追加）

> 说明：本文件记录在静态审计报告（[STABILITY_AUDIT_REPORT.md](./STABILITY_AUDIT_REPORT.md)，故障 #1–#7）之外，**动态实测阶段新抓到的运行时故障、取证证据与对应修复**。后续新发现的故障均按编号继续向本文档末尾追加。

---

## 故障总览表

| 编号 | 发现日期 | 级别 | 现象摘要 | 根因一句话 | 修复补丁 / Commit | 验证状态 |
|---|---|---|---|---|---|---|
| **#8** | 2026-11-27 | **P0** | 非插件 Java 线程触发 `SIGSEGV` 时进程直接被杀 | `init_crash_handler()` 用 `signal()` 覆盖 ART 处理器且未链式转发旧 `sigaction` | `ci_patch13.py` (`782a6d1`) | ✅ 雷电实测通过（`PID 5180` 零闪退） |
| **#9** | 2026-11-27 | **P0** | 进入标题画面约 50s 后 `Adjust-pool` 线程在 `libart.so` 发生 `SEGV_ACCERR` 闪退 | 启动时安装 14 个未用诊断 hook 撑满 Dobby 4KB trampoline 页，页尾 `CutIn.CreateModelList` 跨页 `mprotect(0x2000)` 把邻页 ART `LocalReferenceTable` 由 `rw-` 改成 `r--` | `ci_patch13.py` (`782a6d1`) | ✅ 雷电实测通过（无新增 tombstone） |
| **#10** | 2026-11-28 | **P0** | 育成中马娘带病种/状态（`charaEffects=[47]`）时调用 `/summary` 或 `/debug/turn_probe` 触发 `SIGSEGV`（本次被 `siglongjmp` 恢复） | `ci_patch12.py` 仅修复了 `build_matsuri_frame()` 中 `+1080`（`_charaEffectIdArray`）的读取，漏修 `read_summary_inner()`（`src/lib.rs:6068-6070`），其仍调用 `read_il2cpp_int_list()` 把内联 `ObscuredInt` 当 8 字节指针解引用 | `ci_patch14.py` (`f69f535`) | ✅ 雷电实测通过（`PID 6684`，`chara_effect_ids=[45,47]` 零 `SIGSEGV`） |
| **#11** | 2026-11-28 | **P1** | 出道后连续 11 个回合（`T=13..23`）游戏几乎未按 `matsuri` 推荐执行，实质退回原版“稽古 `200` 优先” | `festa.acts` 缺省导致出道后普通训练估值偏低（甚至为负），`matsuri` 连续输出 `Race(3000)/Rest(3001)`；`ci_patch15` 遇非训练首选将 `150101..150105` 全写 `0` 并放行稽古 `200`（且未接管比赛/休息权重），导致 AI 意图无法传达、权重架空 | `ci_patch16.py` (`51ddfe2`) + `ci_patch17.py` (`f7f2123`) | ✅ Run #4 雷电实测通过（`PID 9588`，78 回合零闪退，15 槽接管生效） |
| **#12** | 2026-11-28 | **P0** | Run #4 开启 `Search(n=128)` 后前 26 回合正常搜索（`~200ms` / 预期 `~71k`），但 `T=27..76` 共 50 回合耗时骤降至 `2–4ms`、0 体力硬点训练/稽古、智力跌至 `935` | `build_matsuri_frame()` 未输出 `baseGame.raceHistory`（默认空数组），无声铃鹿自选赛窗口 `freeRaces=[12..26, count=1]` 过期后 `restored_career_failed(turn)` 恒为 `true`，导致所有 MCTS rollout 刚进第一步即提前终止、彻底丧失多步前瞻 | `ci_patch18.py` (`b223a5dd`) | ✅ Run #5 雷电实测通过（`PID 11636`，78 回合 `decided=78 / skipped=0 / failed=0`，五维总和暴涨至 `7,766`、SP `7,282`、智 `1,508`） |

---

## 详细取证与修复记录

### 故障 #8（P0 · 破坏 Android ART / Crashlytics 信号链）

- **发现时间**：2026-11-27 22:11（雷电 14 + `libhachimi_ura-3.29.1-b2d87cb3.so` 实测期间代码联查）
- **影响范围**：Android 真机与模拟器通用（所有运行在 ART 上的 `jp.co.cygames.umamusume` 进程）
- **代码位置**：`hachimi_ura_plugin/src/lib.rs` `init_crash_handler()` 与 `crash_signal_handler()`
- **根因证据**：
  1. 原实现用传统 `sys_signal(11, crash_signal_handler)`、`sys_signal(6/7/8, ...)` 直接覆盖进程信号处理器，未保存原有 `struct sigaction`。
  2. 在 `crash_signal_handler()` 中，只要不在本插件的 `SIGSEGV_RECOVERY` 窗口内，便直接执行 `sys_signal(sig, 0)`（重置为 `SIG_DFL`）+ `sys_raise(sig)`，**未将信号链式转发给 Android ART 的 `FaultManager` 或 Firebase Crashlytics 原先注册的处理器**。
  3. Android ART 在正常运行期间会主动依赖 `SIGSEGV`（例如隐式空指针检查、栈溢出保护页、GC 屏障等）；破坏信号链会导致原本可由 ART 自行处理的信号直接杀死游戏进程。
- **修复方案**（`ci_patch13.py`）：
  1. 改用标准 `libc::sigaction`（带 `SA_SIGINFO | SA_ONSTACK`），仅接管 `SIGSEGV` 与 `SIGBUS`，不再劫持 `SIGABRT` / `SIGFPE`，并将原处理器完整保存至 `OLD_SIGSEGV_ACT` / `OLD_SIGBUS_ACT`。
  2. 信号到来时仅在 `SIGSEGV_RECOVERY == true && SIGSEGV_RECOVERY_TID == gettid()` 成立时才记日志并 `siglongjmp`；其余一切情况按原 `sa_flags` 链式调用旧的 `sa_sigaction` / `sa_handler`，仅当原处理器为 `SIG_DFL`/`SIG_IGN` 时才恢复默认并重抛。

---

### 故障 #9（P0 · 过量无用 hook 撑爆 Dobby 4KB trampoline 页，踩坏邻页 ART 内存权限）

- **发现时间**：2026-11-27 22:11（`jp.co.cygames.umamusume` 2.33.0，`PID 4095`，启动后约 51 秒）
- **复现现象**：游戏正常渲染出标题画面并启动 `:18765` 端口，点击进入约数秒后直接闪退回桌面。
- **现场取证（`/data/tombstones/tombstone_04`）**：
  ```text
  pid: 4095, tid: 4264, name: Adjust-pool-24-  >>> jp.co.cygames.umamusume <<<
  signal 11 (SIGSEGV), code 2 (SEGV_ACCERR), fault addr 0x000078d0e3efb000
  backtrace:
    #00 pc 00000000006d3e57 /apex/com.android.art/lib64/libart.so
        (art::jni::LocalReferenceTable::Add(...)+87)
    #01 pc 00000000004321d7 /apex/com.android.art/lib64/libart.so
        (art::JNIEnvExt::AddLocalReference+55)
    #02 ... java.lang.Class.forName (com.adjust.sdk.Reflection.forName)
  ```
  故障地址所在 VMA 与相邻字节：
  ```text
  memory map:
    3764: 000078d0'e3ef9000-000078d0'e3ef9fff r--  0  1000  /dev/ashmem/... (deleted)
    3765: --->000078d0'e3efa000-000078d0'e3efbfff r--  0  2000
    3766: 000078d0'e3efc000-000078d0'e3f03fff rw-  0  8000

  memory near rax (0x78d0e3efb000):
    000078d0e3efafe0 d10343ff00004000 a9086ffca9077bfd
    000078d0e3efaff0 d61f022058000051 000040002bdacd70   <-- CutIn.CreateModelList + 12 (0x40002bdacd64+0xc)
    000078d0e3efb000 12f80c8813ef34d8 12f80cb812f80ca0   <-- ART LocalReferenceTable 数据区！
  ```
- **根因分析**：
  1. 插件开机时在 `push_loop` / `on_game_initialized` 中自动安装了 **14 个当前未使用的诊断/辅助 hook**：`install_cutin_trace()`（4 个，交接文档已标注 `未使用`）、`install_api_sniff_hooks()`（6 个）、`rng_consumption_probe::install()`（2 个）、`install_ui_text_hook()`（2 个）。
  2. 加上 Hachimi-Edge 自身几十个 hook，所有 inline hook trampoline 塞满了 `0x78d0e3efa000` 这张 4KB 页，导致最后装上的 `CutIn.CreateModelList` trampoline 恰好贴着页尾 `+0xfe4..+0xfff`。
  3. 更改代码页权限时 `mprotect` 覆盖了跨页的 2 页（`0x2000`：`0x78d0e3efa000–0x78d0e3efbfff`），把紧挨在 `0x78d0e3efb000` 的 ART `LocalReferenceTable` 可写页从 `rw-` 错改成了只读 `r--`。随后任意 Java 线程（如 Adjust SDK / Firebase / 游戏主线程）执行 `JNIEnvExt::AddLocalReference` 写入该页即立即 `SEGV_ACCERR` 崩溃。
- **历史背景 / 妥协注明（DSH / 逆向观测阶段包袱）**：
  - 上述 14 个非核心 hook（API 抓包、UI 文本采集/中文替换、RNG 探针、CutIn 过场追踪）是早期配合 DSH 联调、逆向采集与辅助功能逐步叠加进开机路径的历史产物。精简为 6 个核心 hook 后彻底消除越页踩踏，代价是 UI 中文替换与抓包/探针不再开机自动加载（若后续需要个别辅助项，应改为按需开启而非开机全量安装）。
- **修复方案**（`ci_patch13.py`）：
  1. 精简 `on_game_initialized()` 与 `push_loop` 启动路径，移除 14 个非核心自动 hook（`install_cutin_trace` / `install_api_sniff_hooks` / `rng_consumption_probe::install` / `install_ui_text_hook`）。
  2. 启动时仅安装 6 个育成与 matsuri 决策核心 hook：`install_weight_hook`（`GetBaseWeight`）、`install_selector_hooks`（`SelectTraining`）、`install_training_hook`、`install_exec_training_hook`、`install_failure_rate_hook`、`install_event_choice_hook`。
- **验证判据**：
  - 装上 `ci_patch13` 产物后，连续进入游戏并停留在标题/下载/主界面超过 5 分钟，不再出现 `libart.so LocalReferenceTable::Add` 的 `SEGV_ACCERR` 崩溃，`/data/tombstones/` 无新增文件。

---

---

### 故障 #10（P0 · `read_summary_inner()` 漏修 `_charaEffectIdArray`，马娘带状态/病种时触发 `SIGSEGV`）

- **发现时间**：2026-11-28 00:06（雷电 14，`jp.co.cygames.umamusume` 2.33.2 + `libhachimi_ura-3.29.1-782a6d18.so` / `ci_patch13`，`PID 1502` 育成局内）
- **复现现象**：
  - 马娘（`umaId=100201`，turn 16）处于带病种/状态（`/api/ai/frame` 实测解码为 `"isIll":true, "charaEffects":[47]`）时，巡检请求 `/debug/turn_probe` 与 `/summary`（均调用 `read_summary()`），`/debug/turn_probe` 返回 `raw_total_turn_num:-1`，`/summary` 返回 `{"error":"sigsegv_cooldown","retry_after":180}`。
  - 游戏进程 `PID 1502` **未闪退**（得益于 `ci_patch12` 的 `AlignedJmpBuf` + `SIGSEGV_RECOVERY_TID` 与 `ci_patch13` 的 `sigaction` 成功接管并 `siglongjmp` 恢复），`/data/tombstones/` 无游戏进程的 tombstone（注：同期 `tombstone_05` 为巡检脚本 `toybox grep -iE` 自身在 `libc.so regexec` 的崩溃，与游戏无关）。
- **现场取证**：
  1. `crash_sig.log`（`/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log`，`2026-11-28 00:06`）：
     ```text
     [194] S:before_read_speed
     [194] S:before_read_speed
     [195] S:after_read_speed
     [195] S:after_read_speed
     CRASH at step 195 sig=11 RECOVERED
     ```
     注：日志前半段 `[10]..[184]` 共 5 次 `read_summary` 均完整跑通到 `S:stats sid=15` ~ `S:json`（当时马娘尚无状态效果，`count == 0`）。
  2. `logcat`（`00:06:15.759`–`00:06:15.768`）：
     ```text
     11-28 00:06:15.759  1502  4536 I Hachimi : hachimi::core::plugin_api: boot: summary_p1
     11-28 00:06:15.768  1502  4536 I Hachimi : hachimi::core::plugin_api: ★ read_summary phase1: chara stats
     ```
  3. `/api/ai/frame` 同期正常返回（证明指针有效且问题隔离在 `read_summary_inner`）：
     ```json
     "baseGame":{"scenarioId":15,"umaId":100201,"turn":16,...,"isIll":true,...,"charaEffects":[47]}
     ```
- **根因分析**：
  1. `ci_patch12.py` 在修复故障 #1（`WorkSingleModeCharaData +1080` `_charaEffectIdArray` 为 20 字节内联 `ObscuredInt[]`）时，仅替换了 `build_matsuri_frame()` 中的 `eff_arr` 读取（改调 `read_inline_obscured_int_array`），**遗漏了 `read_summary_inner()` 中的同一字段读取**（`hachimi_ura_plugin/src/lib.rs:6068-6070`，位于 `S:after_read_speed` 与 `S:stats sid={}` 之间）：
     ```rust
     let chara_effect_ids_arr = read_ptr_at(chara_obj, 1080); // _charaEffectIdArray
     let chara_effect_ids: Vec<i32> = if !chara_effect_ids_arr.is_null() {
         read_il2cpp_int_list(chara_effect_ids_arr)
     } else {
         Vec::new()
     };
     ```
  2. 马娘无状态效果（`count == 0`）时不进入循环因此正常；一旦获得任意状态/病种（本例 `charaEffects=[47]`，`count == 1`），`read_il2cpp_int_list()` 将第 0 个内联 `ObscuredInt` 的 `key/hidden` 8 字节当成指针解引用，立即触发 `SIGSEGV`（`sig=11`）。
  3. 同步核查 `src/lib.rs` 全文，确认 `6068-6070` 为最后一处对 `+1080` 调用 `read_il2cpp_int_list()` 的遗漏点。
- **修复方案（已合入 `ci_patch14.py`，commit `f69f535`）**：
  - 将 `src/lib.rs:6068-6073` 中 `read_il2cpp_int_list(chara_effect_ids_arr)` 替换为 `read_inline_obscured_int_array(chara_effect_ids_arr)`（并用 `is_valid_user_ptr(chara_effect_ids_arr)` 判空）。
  - 同期 `ci_patch14.py` + `src/matsuri_bridge.rs` 补全了 `build_matsuri_frame()` 的 `playing_state: 1`、`source: "command"`、`cardId`（6 卡）、`persons`（羁绊+红点）、`personDistribution`（5×5 矩阵）、`isIll`（仅 1–6 病种为 true）以及 `mt_recompute()` skip 退回保护与权重拉开（首选 `1000` / 次选 `10..200`）。

---

### 故障 #11（P1 · 出道后 `matsuri` 连续推荐 `Race/Rest` 且权重映射全清 `0`，导致 AI 训练决策被架空）

- **发现时间**：2026-11-28 12:46–12:49（雷电 14，`jp.co.cygames.umamusume` 2.33.2 + `ci_patch15` / `1a5f42ca`，清空 `weight_override.txt` 后纯 AI 实时逐回合监控）
- **复现现象**：
  - **出道前（`T=5..11`）有效**：清空手工覆盖后，`matsuri` 正常推荐普通训练（`chosen_tag=1004` 智，权重 `[速=30, 耐=27, 力=10, 根=25, 智=170]`），`GetBaseWeight` 实测返回 `150105->170`，游戏实打实连点了 4 次智力（`trainLevelCount: [4,0,0,0,0] → [4,0,0,0,4]`，`智 88→189`）。
  - **出道后（`T=13..23` 连续 11 回合）架空**：`matsuri` 连续 11 个回合无一例外全部选择 `3000`（比赛）或 `3001`（休息），`MT_W` 全程输出 `[速=0, 耐=0, 力=0, 根=0, 智=0]`、稽古槽 `-1`（放行原版 `200`）。结果游戏绝大多数回合直接点了原版 `200` 稽古（`T=13,14,15,17,19,20,23`），甚至在 `T=18` 普通训练权重为 `0` 时仍因原版 `omakase_base_pt` 条件加分点了普通速度。
- **现场取证（`T=25` `/api/ai/status` 与 `/api/ai/frame`）**：
  - 当时 `vital=64/108`、智力位已有 2 张满羁绊支援卡 + 3 名 NPC（`personDistribution[4]=[0,1,7,1029,1096]`），但 `matsuri` 候选打分为：
    ```json
    {"tag":3000,"score":480.74},  // 比赛（最高）
    {"tag":3001,"score":419.20},  // 休息（第二）
    {"tag":1004,"score":279.62},  // 智
    {"tag":3002,"score":174.85},  // 普通外出
    {"tag":1000,"score":36.46},   // 速
    {"tag":1001,"score":-141.52}, // 耐
    {"tag":1002,"score":-202.65}, // 力
    {"tag":1003,"score":-229.78}  // 根
    ```
- **根因分析**：
  1. 出道前（`T<12`）无比赛候选且拉羁绊估值高，普通训练可胜出；出道后（`T>=13`）全队羁绊满 100 且因 2.33.2 `festa.acts` 缺失（`dataset_missing`），`matsuri` 计算训练收益时缺失演目/稽古估值，导致 5 项普通训练得分偏低甚至为负，持续被固定高估的 `Race(3000)` / `Rest(3001)` 压过。
  2. `ci_patch15.py` 中当 `chosen_tag` 不是普通训练时，直接把 `next_w[0..5]` 置 `0`、稽古槽 `5..10` 置 `-1`（放行原版 `200`），且未接管比赛/休息槽（`150106..150110`），导致：
     - 游戏收不到 AI 想比赛/休息的信号（原版稽古 `200` 远高于休息/比赛 `0/10/20`）；
     - 同时连“5 项训练内部的相对排序（如 `T=25` 智 `279.6` 远高于速 `36.5` / 根 `-229.8`）”也被一刀切清零丢弃。
- **修复方向**：
  1. 短期：即便 `matsuri` 首选为 `Race/Rest`，在未接管 `150106..150110` 前，不应将 `150101..150105` 一刀切全写 `0`，而是保留 5 项普通训练的相对打分排序（或仅在 `Rest` 且低体力时压低训练）；同时补齐 `150106..150110` 编号映射。
  2. 根本：补齐 2.33.2 真实 `festa.acts`（走 `ScenarioFesta.*` Entity 层，或通过安全 hook 捕获 `ActListEntity`），让 `matsuri` 能正确评估演目与稽古收益，避免训练估值长期为负被比赛/休息压死。

---

### 故障 #12（P0 · `baseGame.raceHistory` 缺失触发 `career_failed = true`，导致后 50 回合 MCTS 前瞻完全失效）

- **发现时间**：2026-11-28 14:15（雷电 14，`jp.co.cygames.umamusume` 2.33.2 + `f7f21237` / `ci_patch16+17`，Run #4 复盘）
- **复现现象**：
  - Run #4 全程 78 回合零闪退、零 `SIGSEGV`，3 次発表会全部大成功（`[2,2,2]`）、4 项演目升至 Lv10，最终评分 `50,746 (UB4)`（较 Run #3 `49,673` 提升 `+1,073`），但远低于开启 `Search(n=128)` 的理论提分预期，且终局智力仅 `935`（比 Run #3 反而低 `-433`）。
  - 逐回合搜索日志显示 `T=26` 与 `T=33` 之间发生断崖式退化：
    - **`T=13..26`（正常 MCTS）**：单步耗时 `162–274 ms`，候选预期终局分 **`70,011 – 71,599`**，行为合理（按需比赛、拉友人出行、均衡稽古）。
    - **`T=33..76`（全部退化）**：单步耗时骤降至 **`2–4 ms`**，候选得分从 `70,011` 断崖跌至当前瞬时静态分（`T=33: 11,118` → `T=40: 14,977` → `T=45: 18,247` → `T=57: 27,649` → `T=70: 42,294`）。
    - `T=57 (vit=5/108)` 与 `T=70 (vit=0/108)` 出现 **0 体力强行点击训练/稽古** 的反常决策，直接拖垮智力与后半程节奏。
- **根因分析**：
  1. 无声铃鹿（`100201`）在 `umaDB.json` 中存在自选比赛目标：`freeRaces: [{"startTurn": 12, "endTurn": 26, "count": 1}]`。
  2. `build_matsuri_frame()` 从未输出 `baseGame.raceHistory`（反序列化默认为空 `[]`），导致 `uma.win_races` 始终为空位图，`count_free_race(free)` 恒为 `0`。
  3. 在 `matsuri-core/src/game/restore.rs:679` 中：
     ```rust
     fn restored_career_failed(&self, turn: i32) -> bool {
         self.base.uma.free_races.iter().any(|free| {
             (free.end_turn as i32) < turn && self.base.uma.count_free_race(free) < free.count
         })
     }
     ```
     只要 `turn > 26`（即 `T=27..76` 整整 50 个回合），`game.career_failed` 便被置为 `true`！
  4. `matsuri-core/src/game/traits.rs:105` 检测到 `self.career_failed == true` 后立即终止 rollout（`return false`），导致这 50 个回合里 **128 条 MCTS 模拟全都在第 1 步刚执行完就直接结算当前面板分，完全没有模拟后续任何回合**——因此：
     - 未来回合的体力价值为 0（0 体力直接盲目点高面板训练/稽古，不回体、不点智）；
     - 后期演目 Lv、合宿、発表会、属性溢出规划全部失效；
     - 此外在 `T=12..26` 窗口内，即使实际已跑完 1 场自选赛，因 `raceHistory` 为空，AI 仍可能误以为未达标而多跑无用比赛。
- **附带小问题（T2 瞬态 `failed=4`）**：
  - `T=2` 刚进入演目教诲附着弹窗期间，5 项 `acts[].adviceId` 短暂全为 `0`，导致该瞬间触发的 4 次搜索 rollout 报错（`failed=4`），约 2 秒后弹窗确认即恢复正常。
- **修复方案与实测验证（已合入 `ci_patch18.py`，commit `b223a5dd` / `v3.29.1-p18`）**：
  1. 在 `build_matsuri_frame()` 中读取 `WorkSingleModeData + 208 (0xd0)` `_raceHistoryInfoList`（`List<RaceHistoryInfo>`），逐项读取 `RaceHistoryInfo + 16` `_turn`（1-based `1..=78`，减 1 转 0-based），去重升序后输出 `"raceHistory":[...]`。
  2. 对 `turn >= 2 && acts.all(|a| a.advice_id == 0)` 的教诲附着弹窗过渡帧标记 `"advice_pending"` 并在 `mt_recompute()` 中跳过推演。
  3. **Run #5 雷电 14 实测（`PID 11636`）完全通过**：
     - 全程 `raceHistory` 实测读出 `[11, 13, 18, 28, 41, 52, 59, 66, 67, 73, 75, 77]`（0-based 完全精确）；
     - T27 之后 MCTS 不再截断：单步耗时随剩余回合自然递减（T33=`136ms` → T40=`106ms` → T47=`96ms` → T51=`74ms` → T57=`46ms` → T61=`34ms` → T74=`6ms`），全程 `search_backed=true`；
     - 整局 78 回合达成 **`decided=78 / skipped=0 / failed=0 / sync_waits=0 / tombstones=0`**；
     - 终局五维暴涨至 **`2133 / 1422 / 1335 / 1368 / 1508`**（总和 **`7,766`**，较 Run #4 `+372`、较基线 `+511`；智力由 `935` 暴涨 `+573` 至 `1,508`），终局 SP **`7,282`**（较 Run #4 `+635`、较基线 `+767`）。

---

<!-- 后续新增故障请按 #13 ... 顺序追加在下方 -->
