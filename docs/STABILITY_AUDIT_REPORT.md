# 赛马娘 SO 插件稳定性审计报告（7 项根因与修复方案）

- 日期：2026-11-27
- 审计仓库：`github.com/xf8410/uma-so`（`main` 分支 `@ 4b338c2`，含 `ci_patch.py` ~ `ci_patch11.py`）
- 目标版本：`libhachimi_ura.so` 3.29.1（arm64，Android `targetSdk=36`）
- 参照基线：`STABILITY_GUIDE.md`、`FULL_TECH_GUIDE.md`、`docs/HANDOFF_20261126.md`

---

## 0. 结论总览

对当前最新提交（`4b338c2`，即 `ci_patch11`）的 `src/lib.rs`、`src/matsuri_bridge.rs` 及全部 11 个 CI 补丁脚本进行静态审计后，定位到 **7 项明确导致进程崩溃、内存破坏或初始化卡死的代码级根因**。

其中 **前 3 项为 P0 级原生内存/线程安全缺陷**（即使 `ci_patch11` 加上了 worker 活跃门禁，仍会在育成局内或启动阶段触发 SIGSEGV）：

| 编号 | 级别 | 问题摘要 | 涉及文件与位置 |
|---|---|---|---|
| **#1** | **P0** | `_charaEffectIdArray`（内联 `ObscuredInt[]`）被当成指针数组解引用 | `ci_patch.py:123-128`、`src/lib.rs:2464-2484` |
| **#2** | **P0** | `SIGSEGV_JMP_BUF` 仅 200 字节，小于 aarch64 `sigjmp_buf`（264 字节）且跨线程共享 | `src/lib.rs:1529-1614`、`5826`、`10013` 等 |
| **#3** | **P0** | `matsuri-worker` 未 `il2cpp_thread_attach`，且 `mt_read_identity()` 漏掉全部门禁与互斥 | `ci_patch6.py:52-97`、`ci_patch9.py:44-60`、`ci_patch11.py:69-88` |
| **#4** | **P1** | `DATA_ROOT` 写死 `/sdcard/Download/...`，与 `targetSdk=36` 冲突且 `OnceLock` 永久锁死 | `src/matsuri_bridge.rs:32, 109, 126-150` |
| **#5** | **P1** | 两处遗留危险 hook：`install_training_hook` 留着 `write_hook_bytes`；`install_selector_hooks` 仍用错取址 | `src/lib.rs:11235-11243`、`ci_patch.py:321` |
| **#6** | **P1** | `push_loop` 缺默认关闭开关每 2 秒盲扫内存；`install_weight_hook` 未挂入初始化 | `src/lib.rs:7975-8088`、`ci_patch2.py:303-305` |
| **#7** | **P2** | `crash_signal_handler` 日志路径硬编码旧包名且非 async-signal-safe；`MT_W` 写入存在撕裂窗口 | `src/lib.rs:1525, 1588, 1599`、`ci_patch5.py:118-131` |

---

## 1. 7 项稳定性根因详细拆解

### 问题 #1（P0 · 必崩 SIGSEGV）：`_charaEffectIdArray` 内联数组被当成指针数组解引用

**代码位置**
- `hachimi_ura_plugin/ci_patch.py` 第 123–128 行（注入进 `build_matsuri_frame()`）
- `hachimi_ura_plugin/src/lib.rs` 第 2464–2484 行（`read_il2cpp_int_list()`）

**根因证据**
1. 根据实测偏移表（`FULL_TECH_GUIDE.md` §4.1），`WorkSingleModeCharaData +1080` 是 `_charaEffectIdArray`，类型为 **`ObscuredInt[]`**（值类型数组，每个元素 20 字节内联存放于 `+0x20 + i * 20`）。
2. 但 `build_matsuri_frame()` 直接调用了 `read_il2cpp_int_list(eff_arr)`：
   ```rust
   // ci_patch.py:123-128
   let eff_arr = read_ptr_at(chara, 1080);
   let eff_ids: Vec<i32> = if eff_arr.is_null() {
       Vec::new()
   } else {
       read_il2cpp_int_list(eff_arr)
   };
   ```
3. 而 `read_il2cpp_int_list()` 的实现是按 **8 字节指针数组** 读出 `elem_ptr`，再去解引用 `elem_ptr + 0x10`：
   ```rust
   // src/lib.rs:2474-2481
   for i in 0..count {
       let elem = read_obscured_int_at(
           std::ptr::read_unaligned::<*mut c_void>(
               base.add(IL2CPP_LIST_ITEMS_OFF + i * IL2CPP_LIST_ITEM_SIZE) as *const *mut c_void,
           ) as *const c_void,
           IL2CPP_OBSCURED_INT_KEY_OFF as i32,
       );
       result.push(elem);
   }
   ```

**运行时后果**
- 马娘没有状态效果时（`count == 0`）不会触发，容易在早期回合“看似正常”。
- 只要育成中获得**任意 1 个状态或病种**（切れ者、愛嬌、練習上手、偏頭痛、太り気味、夜ふかし気味 等），`count >= 1`，函数会把第 0 个内联 `ObscuredInt` 的前 8 字节（`currentCryptoKey` 与 `hiddenValue`，例如 `0x0006C81D0006C81C`）当成 64 位指针解引用，**100% 立即 SIGSEGV 闪退**。

**修复方案**
- 新增专用读取函数 `read_obscured_int_array(arr)`：校验数组非空、`count <= 64`，按 20 字节步长直接从 `(arr as *const u8).add(0x20 + i * 20)` 读取 `key` 和 `hidden` 做异或（`hidden ^ key`），**不再解引用元素为指针**。
- 顺带核对 `List<ObscuredInt>` 与 `ObscuredInt[]` 区分：若是数组则长度在 `+0x18`、元素起点 `+0x20`；并可通过检查数组 `klass` 名称（含 `[]`）做二次兜底。

---

### 问题 #2（P0 · 内存越界踩踏 + 跨线程栈破坏）：`SIGSEGV_JMP_BUF` 尺寸不足且未绑定线程

**代码位置**
- `hachimi_ura_plugin/src/lib.rs` 第 1528–1614 行（`SIGSEGV_JMP_BUF`、`crash_signal_handler`）
- `hachimi_ura_plugin/src/lib.rs` 第 5826、10013、10027、10042、10057、10072 行（`sys_sigsetjmp` 调用点）

**根因证据**
1. `lib.rs` 中将 `sigjmp_buf` 静态缓冲区定义为了 **200 字节**：
   ```rust
   // src/lib.rs:1528-1530
   // sigsetjmp buffer: 200 bytes is enough for jmp_buf on aarch64 (typically 24 x 8 = 192 bytes)
   static mut SIGSEGV_JMP_BUF: [u8; 200] = [0u8; 200];
   static SIGSEGV_RECOVERY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
   static SIGSEGV_COOLDOWN_UNTIL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
   ```
2. **Android Bionic aarch64（`bionic/libc/include/setjmp.h`）的实际定义**：
   ```c
   #elif defined(__aarch64__)
   #define _JBLEN 32
   typedef long sigjmp_buf[_JBLEN + 1]; // 33 * 8 = 264 字节（8/16 字节对齐）
   ```
   `200 < 264`：每次调用 `sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1)`，都会向 `SIGSEGV_JMP_BUF` 之后**越界写入 64 字节**（含浮点寄存器 `d8–d15` 与信号掩码/checksum），直接覆盖 `.bss` 中紧邻的静态变量。
3. **跨线程误跳风险**：`SIGSEGV_RECOVERY` 是全局 `AtomicBool`，没有记录进入 `sigsetjmp` 的线程 ID（`gettid()`）。当 `push_loop` 或 HTTP 线程置 `SIGSEGV_RECOVERY = true` 期间，若游戏主线程/渲染线程/worker 线程发生信号，handler 会把**另一条线程的上下文 `siglongjmp` 跳回 `SIGSEGV_JMP_BUF` 保存的栈帧**，必然导致栈错乱和进程崩溃。

**修复方案**
1. 将缓冲区扩容并对齐为 `#[repr(C, align(16))] struct JmpBuf([u64; 64]);`（512 字节，远大于 264 字节，且满足 16 字节对齐）。
2. 增加 `static SIGSEGV_RECOVERY_TID: AtomicI32 = AtomicI32::new(0)`：进入保护区前写入当前 `gettid()`，退出时清 0；`crash_signal_handler` 仅在 `SIGSEGV_RECOVERY == true && SIGSEGV_RECOVERY_TID == gettid()` 时才允许 `siglongjmp`，否则恢复默认信号处理并重抛，避免吞掉其他线程的真实崩溃或跨线程跳转。

---

### 问题 #3（P0 · 高危崩溃）：`matsuri-worker` 未 `il2cpp_thread_attach`，且 `mt_read_identity()` 漏掉全部门禁

**代码位置**
- `hachimi_ura_plugin/ci_patch9.py` 第 44–60 行（`mt_start_worker`）
- `hachimi_ura_plugin/ci_patch6.py` 第 52–97 行 + `ci_patch8.py`（`mt_read_identity`）
- `hachimi_ura_plugin/ci_patch11.py` 第 69–88 行（仅修补了 `build_matsuri_frame`）

**根因证据**
1. **缺少 `il2cpp_thread_attach`**：全局检索 `src/lib.rs` 确认 `il2cpp_thread_attach` 出现次数为 **0**。`matsuri-worker` 是通过 `std::thread::Builder::spawn` 创建的原生线程，却直接调用 `call_getter_ref`（内部走 `il2cpp_runtime_invoke` 调用 `get_SingleMode` / `get_Character`）。Unity IL2CPP 明确要求非 Unity 线程在调用托管运行时 API 前必须先执行 `il2cpp_thread_attach(il2cpp_domain_get())`，否则容易触发 Boehm GC / 线程本地状态空指针崩溃。
2. **`mt_read_identity()` 漏掉门禁与校验**：`mt_recompute()` 的执行顺序是 **先调 `mt_read_identity()`，再调 `build_matsuri_frame()`**。但 `ci_patch11.py` 只给 `build_matsuri_frame()` 加了 `WorkSingleModeCharaData` 类校验，**没有保护 `mt_read_identity()`**：
   - `mt_read_identity()` 既未检查 `WorkSingleModeData +68 (_totalTurnNum) > 0`；
   - 也未校验 `chara` 的 `klass` 指针是否为 `WorkSingleModeCharaData`；
   - 也未校验 `sm` 的 `klass` 指针与 `arr` 的合理长度；
   - 同时 `mt_recompute()` 与 `push_loop` / HTTP 线程之间**没有共持 `READ_MUTEX`**，也没有包裹 `sigsetjmp` 保护。

**修复方案**
1. 在 `matsuri-worker` 线程入口（以及 HTTP/push 首次调用 IL2CPP 前）通过 `resolve_il2cpp_symbol("il2cpp_domain_get")` + `resolve_il2cpp_symbol("il2cpp_thread_attach")` 执行一次线程附加（并可按需在退出前 `il2cpp_thread_detach`）。
2. 为 `mt_read_identity()` 补齐与 `build_matsuri_frame()` 一致的完整门禁：
   - 校验指针地址落在合法用户态区间（如 `>= 0x10000`）；
   - 先校验 `sm` 的 `klass` 含 `WorkSingleModeData` 且 `_totalTurnNum(+68) > 0`；
   - 再优先直接从 `sm + 88` 读 `<Character>` 字段指针（减少一次非主线程 `il2cpp_runtime_invoke`，仅在为空时回退 getter），读字段前强制校验 `chara` 的 `klass` 含 `WorkSingleModeCharaData`。
3. `mt_recompute()` 内存读取阶段持 `READ_MUTEX` 与其他读取线程互斥，并在读阶段套用线程绑定的 `sigsetjmp` 恢复保护；拿到 `identity` + `frame` 字符串后**立刻释放 `READ_MUTEX`**，再执行耗时的 `matsuri_bridge::decide(&frame)`（100~800 ms），避免阻塞 HTTP 端点。

---

### 问题 #4（P1 · 权限冲突 + 永久锁死）：`matsuri_bridge.rs` 数据目录写死在 `Download`

**代码位置**
- `hachimi_ura_plugin/src/matsuri_bridge.rs` 第 32、109、126–150 行

**根因证据**
1. `FULL_TECH_GUIDE.md` §2.2 已实测证明：游戏 `targetSdk=36`，`READ_EXTERNAL_STORAGE` / `WRITE_EXTERNAL_STORAGE` 对 `/sdcard/Download` 无效，应用自有目录 `/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/` 才无需任何存储权限。
2. 但 `matsuri_bridge.rs` 仍硬编码：
   ```rust
   pub const DATA_ROOT: &str = "/sdcard/Download/uma_so/matsuri_data";
   ```
3. 更严重的是：`DATA_INIT: OnceLock<Result<(), String>>` **只尝试一次**。如果首次访问 `/sdcard/Download/uma_so/matsuri_data` 因权限或目录不可写而失败，错误会被永久缓存在 `OnceLock` 中，整个游戏生命周期内 matsuri 永远无法恢复。
4. `ensure_assets()` 只判断 `constants.json` 是否存在就提前返回 `Ok(())`：若上一次写盘到中途崩溃（例如 5.7 MB 的 `text_data_dict.json` 写了一半），后续启动不会补写，直接导致 `Data::init` 解析失败。

**历史背景 / 妥协注明（DSH / 无 Root 阶段遗留）**
- 早期在无 root 环境下为了方便 DSH、手机文件管理器或人工查看/替换配置与日志，统一把数据目录放进了公共共享目录 `/sdcard/Download/uma_so/`；游戏升至 `targetSdk=36` 后该路径失效。迁至应用自有目录后免权限且 ADB/Shizuku 仍可正常读写，不损失功能。

**修复方案**
1. 将默认 `DATA_ROOT` 改为应用自有目录：`/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/matsuri_data`（完全免权限，且与 `weight_override.txt` 统一目录）。
2. `ensure_assets()` 逐一校验全部 6 个文件是否存在且字节数等于 `body.len()`；不匹配时走「写 `.tmp` 临时文件 → `rename` 原子替换」落盘，防止半截文件残留。
3. 将 `DATA_INIT` 改为 `Mutex<bool>`（仅在成功后置 `true`），允许短暂失败后重试，而非一次失败永久锁死。

---

### 问题 #5（P1 · 残留危险 hook）：`write_hook_bytes` 兜底未删干净 + `SelectTraining` 用错取址函数

**代码位置**
- `hachimi_ura_plugin/src/lib.rs` 第 11220–11244 行（`install_training_hook`）
- `hachimi_ura_plugin/ci_patch.py` 第 321 行（`install_selector_hooks`）

**根因证据**
1. 交接文档与 `FULL_TECH_GUIDE.md` §9.4 明确列为禁用项的 `write_hook_bytes`（无 trampoline 兜底，改写函数头必崩），实际只从 `install_hook_safe()` 中移除了，**`install_training_hook()` 的 `else` 分支仍然保留了它**：
   ```rust
   // src/lib.rs:11230-11243
   } else {
       ura_log(1, "Training hook: interceptor_hook failed, falling back to write_hook_bytes");
       std::ptr::copy_nonoverlapping(method_addr as *const u8, ORIG_ON_SUCCESS_PROLOGUE.as_mut_ptr(), 16);
       write_hook_bytes(method_addr, training_hook_handler as usize);
       TRAINING_HOOK_INSTALLED = true;
   }
   ```
2. `ci_patch4.py` 已经证明 `find_method_addr`（走 `il2cpp_method_get_pointer`）在 2.33.x 会返回落在别的函数中段的错地址，但只替换了 `install_weight_hook` 一处，**`install_selector_hooks()` 仍在调用 `find_method_addr(class, "SelectTraining", 1)`**，存在把 inline hook 装到错误代码地址、破坏无关函数的风险。

**修复方案**
1. 删除 `install_training_hook()` 中 `interceptor_hook` 失败后的 `write_hook_bytes` 兜底分支，失败即记日志返回。
2. 将 `install_selector_hooks()` 中的 `find_method_addr` 替换为 `find_method_entry`（与 `il2cpp_disassemble` 同源读 `MethodInfo + 0`），并在 `/api/ai/hooks` 中同步暴露 `method_pointer` 校验状态。

---

### 问题 #6（P1 · 后台持续轮询干扰 + 权重 hook 不自启）

**代码位置**
- `hachimi_ura_plugin/src/lib.rs` 第 7975–8088 行（`push_loop`）
- `hachimi_ura_plugin/ci_patch2.py` 第 303–305 行（`install_weight_hook` 触发点）

**根因证据**
1. `docs/HANDOFF_20261126.md` §4.2 第 10 条提到新增 `PUSH_READ_ENABLED`（默认关闭）以消除后台轮询导致的 SIGSEGV，但实查当前 `main` 分支 `src/lib.rs`，**`PUSH_READ_ENABLED` 出现次数为 0（并未合入）**。因此插件一加载，`push_loop` 就每 2 秒自动执行一次 `read_summary()`（数十次 `il2cpp_runtime_invoke` + 问题 #2 中越界的 `sigsetjmp`），即使完全没有使用 DSH 推送。
2. `install_weight_hook()`（连带 `mt_start_worker()`）目前**只挂在 `/api/ai/weightcalls` HTTP 路由里**，未挂入 `on_game_initialized` 和 `push_loop` fallback。游戏重启后如果不手动 curl `/api/ai/weightcalls`，权重 hook 和 worker 都处于未安装状态。

**历史背景 / 妥协注明（DSH 阶段遗留）**
- `push_loop` 每 2 秒主动读 `read_summary()` 是早期为了向 DSH（`127.0.0.1:18766/data`）实时推送育成面板数据；`install_weight_hook()` 未自启则是早期配合 DSH / 命令行手动触发单变量实验留下的写法。改为默认关闭后，已保留 `/api/push/read/on` 开关，后续若需重新联动 DSH 可随时恢复推送。

**修复方案**
1. 补齐 `PUSH_READ_ENABLED: AtomicBool`（默认 `false`）及 `/api/push/read`、`/api/push/read/on`、`/api/push/read/off` 开关，彻底切断非必要后台盲扫；同时保留首次 `on_game_initialized` 回调安装 hook。
2. 在 `on_game_initialized()`（游戏主线程初始化完成后）自动调用 `install_weight_hook()`；且在 `GetBaseWeight` 命中前，`matsuri-worker` 受活跃门禁约束只 sleep 不碰内存，兼顾自启动与安全性。

---

### 问题 #7（P2 · 崩溃日志落盘失效 + 信号处理不安全 + 权重表写入撕裂）

**代码位置**
- `hachimi_ura_plugin/src/lib.rs` 第 1525、1588、1599 行（`crash_signal_handler`）
- `hachimi_ura_plugin/ci_patch5.py` 第 118–131 行（`mt_recompute` 写 `MT_W`）

**根因证据**
1. `crash_signal_handler` 里硬编码了旧包名路径 `/data/data/jp.pokemon.pokeuma/files/uma_predict.log`，在 `jp.co.cygames.umamusume` 进程内 `open()` 必然失败返回 `-1`，导致崩溃现场完全无法落盘；同时在 signal handler 内调用了 `std::time::SystemTime::now()`（非 async-signal-safe，极端情况下可死锁或二次崩溃）。
2. `mt_recompute()` 更新权重数组时，先循环把 `MT_W[0..10]` 全部清为 `0`，再逐槽写入新分数。游戏渲染线程在中间窗口读取 `MT_W` 时，可能拿到“部分或全部为 0”的撕裂快照（导致短时间内所有训练权重归 0）。

**修复方案**
1. 崩溃日志路径改为应用自有目录 `/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log`（或 `clock_gettime(CLOCK_REALTIME)` 获取秒数，全程仅用 POSIX async-signal-safe 系统调用）。
2. `mt_recompute()` 先在局部数组 `let mut next = [0i32; 10];` 中算完所有槽位，再用 `Ordering::Release` 写入 `MT_W`（或用双缓冲/版本号快照），`mt_weight_for_arg` 侧用 `Ordering::Acquire` 读取，消除全 0 窗口。

---

## 2. 建议的下一步修复与验证计划

### 代码侧（`ci_patch12.py` + `src/matsuri_bridge.rs`）
按仓库现有 CI 约定，保持 `src/lib.rs` 不手改，新增 `ci_patch12.py` 串联到 `.github/workflows/build-so.yml`，并修改 `src/matsuri_bridge.rs`（问题 #4），一次性收口全部 7 项问题，同时在 CI `verify` 步骤增加新锚点与防回归检查（确认二进制中不再含 `jp.pokemon.pokeuma`、存在 `il2cpp_thread_attach`、`read_obscured_int_array`、`PUSH_READ_ENABLED`、新 `DATA_ROOT`）。

### 真机侧验证说明（附：雷电模拟器实测结果）
本轮已实测在雷电模拟器（LDPlayer 14，`x86_64` + `libhoudini.so` 转译层，Android 14，Root）上部署 `jp.co.cygames.umamusume` 2.33.0 + Hachimi Edge `v0.33.1` + `libhachimi_ura-3.29.1-4b338c25.so`，结果如下：

```text
I Hachimi : hachimi::core::hachimi: Hachimi v0.33.1-3c48a46
I Hachimi : hachimi::core::hachimi: Game region: Japan
I Hachimi : hachimi::android::plugin_loader: Loaded library: .../libhachimi_ura.so
I Hachimi : hachimi::android::hook: API level: 34
I Hachimi : hachimi::android::hook: Hooking __dl__Z9do_dlopenPKciPK17android_dlextinfoPKv at 0x739a1f26bc50
E Hachimi : hachimi::android::hook: Init failed: Hooking failed: hook error
```

- **结论**：`libmain.so` 与 `libhachimi_ura.so` 均已成功映射到进程，但因为 `x86_64` 模拟器的系统链接器（`/system/bin/linker64`）是原生 `x86_64` 机器码，arm64 版 Hachimi-Edge 无法对其 `do_dlopen` 打 arm64 inline hook，初始化直接中止，不会走到 `hachimi_init_v3`。
- **对策**：**最终的运行时注入与长稳实测必须在原生 arm64 Android 设备上进行**（代码静态修复、CI 交叉编译、二进制符号与字符串判据验证均可直接通过 GitHub Actions 完成）。

---

## 3. 验收判据（上机后逐条核对）

1. **静态/CI 判据**：`.so` 体积保持 ~14.3 MB，`verify` 全部通过（决策链未被 LTO 裁剪、7 项修复符号均落地）。
2. **启动与标题画面长稳**：进入游戏停留在标题/主界面 10 分钟以上不闪退，`/api/push/read` 默认关闭，`/api/ai/matsuri` 维持 `state=0`（worker 不乱碰非育成对象）。
3. **局内带病种/状态压力测试**：在马娘获得至少 1 个状态/病种（如愛嬌、練習上手、切れ者、偏頭痛等）的回合，反复请求 `/api/ai/frame` 与 `/api/ai/decide` 均不闪退，且 `charaEffects` 能正确解出状态 ID。
4. **权限与初始化**：`/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/matsuri_data/` 自动展开 6 张表完整无残缺，`ready=true` 稳定达成。
5. **长局稳定性**：开启自动育成跑完完整一局（含训练、休息、外出、比赛、结算切回主界面）零闪退，且 `crash_sig.log` 无新增崩溃记录。
