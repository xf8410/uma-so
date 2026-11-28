#!/usr/bin/env python3
"""CI 侧补丁 12：把 7 个「必崩 / 静默失效」根因一次清掉。

来源：`uma_so/STABILITY_GUIDE.md` §7 与 `uma_so/WEIGHT_OVERRIDE_IMPL.md` §8
（2026-11-28 代码审计，逐条对着源码核过）。

P1  `_charaEffectIdArray`（CharaData +1080）是 `ObscuredInt[]`（元素 20 字节内联），
    却被 `read_il2cpp_int_list()` 按 `List<int>` 读（元素当指针解引用）。
    后果：马娘身上只要挂 1 个状态/病种，就把 currentCryptoKey 当地址读 → 100% 闪退。
    修：新增 `read_obscured_int_array()`，只替换这一处调用；`read_il2cpp_int_list` 不动
    （技能/历史那些调用点仍是真 List<int>）。

P2  `SIGSEGV_JMP_BUF` 只有 200 字节，Bionic aarch64 的 `sigjmp_buf` 是 264 字节
    （33 × 8）→ 每次 sigsetjmp 踩坏 64 字节 .bss。且 handler 不校验线程归属，
    别的线程触发信号会 longjmp 到别人栈上。
    修：缓冲区 320（264 对齐 64），sigsetjmp 后记 tid，handler 只允许 owner 线程跳回。

P3  `matsuri-worker` 未 `il2cpp_thread_attach`；`mt_read_identity()` 无类校验、
    无 `_totalTurnNum > 0` 门禁、也不与 `push_loop` 的 READ_MUTEX 互斥。
    修：worker 线程体开头 attach 一次；身份读取前加类校验 + turn 门禁 + READ_MUTEX。

P4  数据根写死 `/sdcard/Download/uma_so/matsuri_data`（targetSdk=36 读不到）；
    `DATA_INIT: OnceLock` 失败即永久锁死；`ensure_assets` 只看 constants.json。
    修：改到应用自有目录；六张表逐字节比对、原子替换；失败可重试。

P5  `install_training_hook` 仍保留 `write_hook_bytes` 兜底（无 trampoline，装上就废）；
    `install_selector_hooks` 仍用错误的 `find_method_addr`（应 `find_method_entry`）。
    修：删兜底只留日志；取址改 find_method_entry。

P6  `push_loop` 开机就每 2 秒盲扫 `read_summary()`（数十次 runtime_invoke）；
    `install_weight_hook()` 不随启动安装，闭环一半时间没挂上。
    修：启动只探测「IL2CPP 能否解析目标类」（不碰对象），成功即装全部 hook（含 weight）；
    周期读取改为文件开关，默认关闭。

P7  `festa.acts` 空缺省（dataset_missing）时，`MT_W[5..9]` 被写成 0，
    把原版 200 的稽古权重全压掉。
    修：稽古槽在「本轮确实有 ActTrain 候选」时才接管，否则保持 -1（= 不接管，返回原值）。

纪律：所有替换都必须命中唯一锚点；命中数不对就整体失败，不做半套补丁。
"""

import re
import sys

PATH = "src/lib.rs"
PATH_BRIDGE = "src/matsuri_bridge.rs"

# ============================================================
# P1 · ObscuredInt[] 按值类型读
# ============================================================

P1_FN = r'''/// ★ v3.30.1: 读 1D `ObscuredInt[]`（值类型内联数组）。
///
/// 和 `read_il2cpp_int_list` 的区别（本插件最贵的一个坑）：
///   `List<int>` 的 items 是指针数组 → 元素必须解引用；
///   `ObscuredInt[]` 的元素是**内联结构体**（跨度 0x14 = 20 字节：
///   key / hidden / inited / fake / fakeActive 各 4 字节）→ 元素内容**不是地址**。
/// 把后者按前者读，就是拿 currentCryptoKey 当地址解引用 ——
/// 马娘身上只要挂一个状态（切れ者 / 愛嬌 / 偏頭痛 …）立刻 SIGSEGV。
unsafe fn read_obscured_int_array(array_ptr: *const c_void) -> Vec<i32> {
    if array_ptr.is_null() {
        return Vec::new();
    }
    let base = array_ptr as *const u8;
    // IL2CPP array（64 位）：+0x10 bounds（1 维为 null）/ +0x18 max_length / +0x20 data
    let count = std::ptr::read_unaligned::<usize>(base.add(0x18) as *const usize);
    if count == 0 || count > 200 {
        return Vec::new();
    }
    let mut out: Vec<i32> = Vec::with_capacity(count);
    for i in 0..count {
        let elem = base.add(0x20 + i * 0x14);
        let key = std::ptr::read_unaligned::<i32>(elem as *const i32);
        let hidden = std::ptr::read_unaligned::<i32>(elem.add(4) as *const i32);
        out.push(hidden ^ key);
    }
    out
}

'''

P1_ANCHOR = "/// ★ v3.24.9: Read IL2CPP List<int> via pointer — zero runtime_invoke\nunsafe fn read_il2cpp_int_list"

P1_CALL_OLD = r'''    let chara_effect_ids_arr = read_ptr_at(chara_obj, 1080); // _charaEffectIdArray
    let chara_effect_ids: Vec<i32> = if !chara_effect_ids_arr.is_null() {
        read_il2cpp_int_list(chara_effect_ids_arr)'''

P1_CALL_NEW = r'''    let chara_effect_ids_arr = read_ptr_at(chara_obj, 1080); // _charaEffectIdArray
    let chara_effect_ids: Vec<i32> = if !chara_effect_ids_arr.is_null() {
        // ★ v3.30.1: 这是 ObscuredInt[]（值类型内联），不是 List<int>
        read_obscured_int_array(chara_effect_ids_arr)'''

# ============================================================
# P2 · sigjmp_buf 尺寸 + 线程归属
# ============================================================

P2_BUF_OLD = r'''// sigsetjmp buffer: 200 bytes is enough for jmp_buf on aarch64 (typically 24 x 8 = 192 bytes)
static mut SIGSEGV_JMP_BUF: [u8; 200] = [0u8; 200];'''

P2_BUF_NEW = r'''// sigsetjmp buffer：Bionic aarch64 的 sigjmp_buf 是 264 字节（33 × 8）。
// 原来只开 200 字节，每次 sigsetjmp 都会往后踩坏 64 字节 .bss 全局内存。
// 320 = 264 向上对齐到 64，留余量给以后 ABI 变动。
static mut SIGSEGV_JMP_BUF: [u8; 320] = [0u8; 320];
// 设置 sigsetjmp 的那个线程的 tid：只有它才允许 siglongjmp 回来。
static SIGSEGV_TID: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(0);

#[inline]
fn cur_tid() -> i32 {
    // 用 gettid 而不是 pthread_self：后者是 pthread_t 指针，跨线程不可比。
    unsafe { libc::syscall(libc::SYS_gettid) as i32 }
}'''

P2_HANDLER_OLD = r'''    // ★ v3.22.35: If sigsetjmp was set (push thread), longjmp back instead of killing process
    if SIGSEGV_RECOVERY.load(std::sync::atomic::Ordering::Relaxed) {'''

P2_HANDLER_NEW = r'''    // ★ v3.30.1: 只有「设置 sigsetjmp 的那个线程」才能 longjmp 回来。
    // 别的线程触发信号时跳过去，会落到别人的栈上 —— 那比原崩溃更糟。
    let owner = SIGSEGV_TID.load(std::sync::atomic::Ordering::Relaxed);
    if owner == 0 || owner != cur_tid() {
        unsafe {
            sys_signal(sig, 0);
            sys_raise(sig);
        }
        return;
    }
    // ★ v3.22.35: If sigsetjmp was set (push thread), longjmp back instead of killing process
    if SIGSEGV_RECOVERY.load(std::sync::atomic::Ordering::Relaxed) {'''

P2_JMP_RE = re.compile(r"^([ \t]*)let (?:jm|jump)_result = unsafe \{ sys_sigsetjmp\(SIGSEGV_JMP_BUF\.as_mut_ptr\(\), 1\) \};[ \t]*$")

# ============================================================
# P3 · worker 线程 attach + 身份读取门禁
# ============================================================

P3_FN = r'''/// ★ v3.30.1: 把当前（插件自建的）线程挂到 IL2CPP 域上。
///
/// 后台 Rust 线程不属于 IL2CPP 的 domain，直接 `il2cpp_runtime_invoke`
/// （get_SingleMode / get_Character）会在 GC 阶段炸进程。
/// 只需在线程体开头调用一次，线程常驻不 detach。
static MT_ATTACHED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn mt_thread_attach() -> bool {
    use std::sync::atomic::Ordering::Relaxed;
    if MT_ATTACHED.load(Relaxed) {
        return true;
    }
    unsafe {
        let dg = resolve_il2cpp_symbol("il2cpp_domain_get");
        let at = resolve_il2cpp_symbol("il2cpp_thread_attach");
        if dg.is_null() || at.is_null() {
            return false;
        }
        type FnDomainGet = unsafe extern "C" fn() -> *mut c_void;
        type FnAttach = unsafe extern "C" fn(*mut c_void) -> *mut c_void;
        let get_domain: FnDomainGet = std::mem::transmute(dg);
        let attach: FnAttach = std::mem::transmute(at);
        let domain = get_domain();
        if domain.is_null() {
            return false;
        }
        let t = attach(domain);
        if t.is_null() {
            return false;
        }
    }
    MT_ATTACHED.store(true, Relaxed);
    true
}

'''

P3_ANCHOR = "static MT_WORKER_ON: std::sync::atomic::AtomicBool ="

P3_SPAWN_OLD = r'''        .spawn(|| {
            // 首轮让游戏先稳定，避开启动/加载的抖动期
            std::thread::sleep(std::time::Duration::from_secs(5));'''

P3_SPAWN_NEW = r'''        .spawn(|| {
            // ★ v3.30.1: 先把这个线程挂进 IL2CPP 域，否则 invoke 会在 GC 阶段崩
            mt_thread_attach();
            // 首轮让游戏先稳定，避开启动/加载的抖动期
            std::thread::sleep(std::time::Duration::from_secs(5));'''

P3_ID_OLD = r'''unsafe fn mt_read_identity() -> MtIdentity {
    let mut id = MtIdentity {
        uma: 0,
        cards: Vec::new(),
        raw: Vec::new(),
    };'''

P3_ID_NEW = r'''unsafe fn mt_read_identity() -> MtIdentity {
    let mut id = MtIdentity {
        uma: 0,
        cards: Vec::new(),
        raw: Vec::new(),
    };
    // ★ v3.30.1: 与 push_loop 的 read_summary 共用一把读锁 —— 两条线程同时读 IL2CPP
    // 容器，任何一侧指针失效都会互相踩死。
    // 用 try_lock 而不是 lock：这条链里会走 il2cpp invoke，万一它同步回调进
    // 另一个持锁入口，lock 就是自锁（游戏直接卡死）。拿不到锁就这一轮不读。
    let _read_guard = match READ_MUTEX.try_lock() {
        Ok(g) => g,
        Err(_) => return id,
    };'''

P3_CHARA_OLD = r'''    let chara = call_getter_ref(sm_class, sm, "get_Character");
    if chara.is_null() {
        return id;
    }

    let cid = read_obscured_int_at(chara, 36);'''

P3_CHARA_NEW = r'''    let chara = call_getter_ref(sm_class, sm, "get_Character");
    if chara.is_null() {
        return id;
    }
    // ★ v3.30.1: 门禁（与 build_matsuri_frame 的 chara 校验同源，这里补上漏掉的先执行路径）。
    // 非育成态 get_Character 可能返回「非空但已释放/被复用」的对象，
    // 读字段前必须核对对象头的类名，对不上就什么都不读。
    {
        let klass = std::ptr::read_unaligned::<*mut c_void>(chara as *const *mut c_void);
        if klass.is_null() {
            return id;
        }
        let cname = get_class_name_from_pointer(klass);
        if !cname.contains("WorkSingleModeCharaData") {
            return id;
        }
    }
    // 未开局（_totalTurnNum == 0）时没有身份可读
    if read_obscured_int_at(sm as *const c_void, 68) <= 0 {
        return id;
    }

    let cid = read_obscured_int_at(chara, 36);'''

# ============================================================
# P4 · 数据根 + 逐表校验 + 可重试
# ============================================================

P4_ROOT_OLD = 'pub const DATA_ROOT: &str = "/sdcard/Download/uma_so/matsuri_data";'

P4_ROOT_NEW = r'''// ★ v3.30.1: 迁到应用自有目录。游戏 targetSdk=36，
// READ/WRITE_EXTERNAL_STORAGE 对 /sdcard/Download 基本无效（需 MANAGE_EXTERNAL_STORAGE），
// 而 Android/data/<pkg>/files 不需要任何权限。
pub const DATA_ROOT: &str =
    "/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/matsuri_data";'''

P4_INIT_OLD = 'static DATA_INIT: OnceLock<Result<(), String>> = OnceLock::new();'

P4_INIT_NEW = r'''// ★ v3.30.1: 原来是 OnceLock<Result<..>> —— 只要第一次失败（目录还没建好、
// 权限瞬断），这个进程生命周期内就永久卡在 Err，再也起不来。
// 改成 bool + Mutex：成功才缓存，失败下次重试。
static DATA_INIT: Mutex<bool> = Mutex::new(false);'''

P4_ASSETS_OLD = r'''/// 把内嵌资产展开到运行时目录（已存在不覆盖）
fn ensure_assets() -> Result<(), String> {
    let root = Path::new(DATA_ROOT);
    if root.join("constants.json").exists() {
        return Ok(());
    }
    std::fs::create_dir_all(root).map_err(|e| format!("mkdir {DATA_ROOT}: {e}"))?;
    for (name, body) in EMBEDDED_GAMEDATA {
        let path = root.join(name);
        if path.exists() {
            continue;
        }
        std::fs::write(&path, body).map_err(|e| format!("write {}: {e}", path.display()))?;
    }
    Ok(())
}

/// 引擎全局数据初始化（进程内只尝试一次）
fn ensure_data() -> Result<(), String> {
    DATA_INIT
        .get_or_init(|| {
            ensure_assets()?;
            engine_init_data(DATA_ROOT).map_err(|e| format!("Data::init: {e}"))
        })
        .clone()
}'''

P4_ASSETS_NEW = r'''/// 把内嵌资产展开到运行时目录（★ v3.30.1：逐表比对，写一半崩了也能补）
///
/// 原来的判据是「constants.json 存在就算齐」，于是：
/// 展开到第 3 张表时崩 → 下次启动直接跳过 → engine 永远 init 失败。
/// 现在逐张按字节比对内嵌内容，缺的/坏的重写；写入走临时文件 + rename，避免半截文件。
fn ensure_assets() -> Result<(), String> {
    let root = Path::new(DATA_ROOT);
    std::fs::create_dir_all(root).map_err(|e| format!("mkdir {DATA_ROOT}: {e}"))?;
    for (name, body) in EMBEDDED_GAMEDATA {
        let path = root.join(name);
        let want = body.as_bytes();
        if let Ok(have) = std::fs::read(&path) {
            if have == want {
                continue;
            }
        }
        let tmp = root.join(format!("{}.part", name));
        std::fs::write(&tmp, want).map_err(|e| format!("write {}: {e}", tmp.display()))?;
        std::fs::rename(&tmp, &path).map_err(|e| format!("rename {}: {e}", path.display()))?;
    }
    Ok(())
}

/// 引擎全局数据初始化（★ v3.30.1：成功才缓存，失败可重试）
///
/// 整段持锁：`engine_init_data` 不是线程安全的，http 端点与 worker 同时进来
/// 会两次初始化引擎全局态（以前 OnceLock 靠「只执行一次」掩盖了这个问题）。
fn ensure_data() -> Result<(), String> {
    // 中毒也照常拿：这里只是「初始化过没有」的布尔，没有需要回滚的不变量，
    // 若因一次 panic 就永久拒绝初始化，反而会把整个决策栈锁死在 not_ready。
    let mut guard = DATA_INIT.lock().unwrap_or_else(|e| e.into_inner());
    if *guard {
        return Ok(());
    }
    ensure_assets()?;
    engine_init_data(DATA_ROOT).map_err(|e| format!("Data::init: {e}"))?;
    *guard = true;
    Ok(())
}'''

P4_STATUS_OLD = r'''    let data_init = DATA_INIT
        .get()
        .map(|r| match r {
            Ok(()) => "ok".to_string(),
            Err(e) => e.replace('"', "'"),
        })
        .unwrap_or_else(|| "not_attempted".to_string());'''

P4_STATUS_NEW = r'''    // ★ v3.30.1: DATA_INIT 从 OnceLock 改成 Mutex<bool>，这里同步改读法
    let data_init = match DATA_INIT.lock() {
        Ok(g) => {
            if *g {
                "ok".to_string()
            } else {
                "not_ready".to_string()
            }
        }
        Err(_) => "lock_poisoned".to_string(),
    };'''

# ============================================================
# P5 · 删裸写机器码兜底 + selector 用正确取址
# ============================================================

P5_TRAIN_OLD = r'''    } else {
        ura_log(
            1,
            "Training hook: interceptor_hook failed, falling back to write_hook_bytes",
        );
        // Fallback: old write_hook_bytes method (less safe but works without interceptor)
        std::ptr::copy_nonoverlapping(
            method_addr as *const u8,
            ORIG_ON_SUCCESS_PROLOGUE.as_mut_ptr(),
            16,
        );
        write_hook_bytes(method_addr, training_hook_handler as usize);
        TRAINING_HOOK_INSTALLED = true;
    }'''

P5_TRAIN_NEW = r'''    } else {
        // ★ v3.30.1: 删掉 write_hook_bytes 兜底。
        // 它是「原地改写前 16 字节 + 指望某处还能调回原函数」，没有 trampoline，
        // 装上之后 OnSuccessSendCommand 就再也不会正常执行 —— 不崩，但功能全废。
        // 拦不住就老实报错，让 /api/ai/hooks 一眼可见。
        ura_log(
            1,
            "Training hook: interceptor_hook failed (write_hook_bytes fallback removed)",
        );
    }'''

P5_SEL_OLD = '    let addr = find_method_addr(class, "SelectTraining", 1);'

P5_SEL_NEW = r'''    // ★ v3.30.1: 用 find_method_entry（MethodInfo +0，与 /il2cpp/disassemble 同源）
    // 而不是 find_method_addr（il2cpp_method_get_pointer 在 2.33.2 上返回错值，
    // 会把 hook 装到别的函数中段 —— installed=true 但 hits 恒 0）。
    let addr = find_method_entry(class, "SelectTraining", 1);'''

# ============================================================
# P6 · 启动不再盲扫 + weight hook 随启动安装 + 周期读取默认关
# ============================================================

P6_FN = r'''/// ★ v3.30.1: 周期读取开关，默认关闭。
///
/// `push_loop` 原来每 2 秒跑一次全量 `read_summary()`（几十次 il2cpp_runtime_invoke），
/// 等于在自己插件里造了个定时炸弹：每次都在赌游戏对象还有效。
/// 想恢复旧行为，往下面这个文件首行写 on（应用自有目录，无需权限）：
///   /sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/push_read.txt
const PUSH_READ_FLAG: &str =
    "/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/push_read.txt";

fn push_read_enabled() -> bool {
    match std::fs::read_to_string(PUSH_READ_FLAG) {
        Ok(t) => {
            let v = t.lines().next().unwrap_or("").trim().to_ascii_lowercase();
            v == "on" || v == "1" || v == "true"
        }
        Err(_) => false,
    }
}

/// ★ v3.30.1: IL2CPP 就绪探测 —— 只查「目标类能否解析」，不碰任何游戏对象。
///
/// 原来用 `read_summary()` 当就绪探测（读整个存档摘要），成本高且会崩。
fn wb_il2cpp_ready() -> bool {
    unsafe {
        if API.is_null() {
            return false;
        }
        let image = get_image();
        if image.is_null() {
            return false;
        }
        let class = find_class_by_short_name(image, "SingleModeAutoPlayWeightRepositoryBase");
        !class.is_null()
    }
}

'''

P6_ANCHOR = "fn push_loop() {"

P6_BOOT_OLD = r'''    // ★ Initial push: try pushing current data on startup
    // Don't rely solely on GAME_INITIALIZED callback — it may never fire
    // if the game was already initialized before the plugin loaded.
    // Instead, try reading data; if it succeeds, the game is ready.
    for wait_round in 0..60 {
        if GAME_INITIALIZED.load(Ordering::Relaxed) {
            break;
        }
        boot_trace("push_probe_begin");
        // Try a probe read — if it doesn't error, game is ready
        let probe = read_summary();
        if !probe.contains("\"error\"") {
            GAME_INITIALIZED.store(true, Ordering::Relaxed);
            unsafe {
                ura_log(3, "Push: game detected via probe (no callback)");
                // v3.22.98: Install hooks in fallback (on_game_initialized may never fire)
                install_training_hook();
                install_exec_training_hook();
                install_failure_rate_hook();
                install_event_choice_hook();
                install_ui_text_hook();
                install_cutin_trace();
                flog("init", &format!("plugin {} boot, endpoints ready", PLUGIN_VERSION));
                // 加速功能已停用（用户要求先删）：install_duration_hook / install_timescale_hooks 不再调用
                rng_consumption_probe::install();
                // ★ v3.24.40: sniff hooks were missing here — fallback mode
                // left /api/sniff permanently unhooked.
                install_api_sniff_hooks();
            }
            break;
        }
        if wait_round % 10 == 0 {
            unsafe {
                ura_log(
                    3,
                    &format!("Push: waiting for game... round={}", wait_round),
                );
            }
        }
        std::thread::sleep(std::time::Duration::from_secs(1));
    }'''

P6_BOOT_NEW = r'''    // ★ v3.30.1: 启动阶段不再用 read_summary() 盲扫当就绪探测。
    // 原来每轮都读一次全量摘要（几十次 runtime_invoke），是后台干扰与随机闪退的主源之一。
    // 现在只探测「IL2CPP 能否解析 SingleModeAutoPlayWeightRepositoryBase」——
    // 纯元数据查找，不碰任何游戏对象，代价可以忽略。
    for wait_round in 0..300 {
        if GAME_INITIALIZED.load(Ordering::Relaxed) {
            break;
        }
        if wb_il2cpp_ready() {
            GAME_INITIALIZED.store(true, Ordering::Relaxed);
            unsafe {
                ura_log(3, "Push: IL2CPP ready (class resolvable), installing hooks");
                install_training_hook();
                install_exec_training_hook();
                install_failure_rate_hook();
                install_event_choice_hook();
                install_ui_text_hook();
                install_cutin_trace();
                // ★ v3.30.1: weight hook 原来只在 /api/ai/weightcalls 里装，
                // 不进育成就永远挂不上 → matsuri 闭环一半时间根本没接管。
                // 这里随启动一并安装（内部有 WB_INSTALLED 幂等标志）。
                install_weight_hook();
                flog("init", &format!("plugin {} boot, endpoints ready", PLUGIN_VERSION));
                // 加速功能已停用（用户要求先删）：install_duration_hook / install_timescale_hooks 不再调用
                rng_consumption_probe::install();
                // ★ v3.24.40: sniff hooks were missing here — fallback mode
                // left /api/sniff permanently unhooked.
                install_api_sniff_hooks();
            }
            break;
        }
        if wait_round % 10 == 0 {
            unsafe {
                ura_log(
                    3,
                    &format!("Push: waiting for IL2CPP... round={}", wait_round),
                );
            }
        }
        std::thread::sleep(std::time::Duration::from_secs(1));
    }'''

P6_LOOP_OLD = r'''    loop {
        std::thread::sleep(interval);
        // Don't gate on GAME_INITIALIZED — just try reading;
        // if the game isn't ready, read_summary returns error and we skip.
        let summary = read_summary();'''

P6_LOOP_NEW = r'''    loop {
        std::thread::sleep(interval);
        // ★ v3.30.1: 周期读取默认关闭（见 push_read_enabled）。
        // 需要旧行为时写开关文件；开了也仍是「读失败就退避」，不会连炸。
        if !push_read_enabled() {
            continue;
        }
        let summary = read_summary();'''

# ============================================================
# P7 · 稽古槽不再被压成 0
# ============================================================

P7_GEN_DEF = r'''// ★ v3.30.1: 权重发布代际。奇数 = worker 正在写 MT_W；偶数 = 可读。
// hook 跑在游戏渲染线程，读到奇数就这一拍不接管，避免吃到「清了一半」的中间态。
static MT_GEN: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

'''

P7_GEN_ANCHOR = "static MT_W: [std::sync::atomic::AtomicI32; 10] = ["

P7_HOOK_OLD = r'''    if MT_STATE.load(Relaxed) != 1 {
        return None;
    }
    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;'''

P7_HOOK_NEW = r'''    if MT_STATE.load(Relaxed) != 1 {
        return None;
    }
    // ★ v3.30.1: worker 正在发布（gen 为奇数）→ 这一拍不接管，返回原值
    if MT_GEN.load(Relaxed) & 1 == 1 {
        return None;
    }
    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;'''

P7_OLD = r'''    // 先全部置 0（不可选），再把 matsuri 给出的候选按比例写进去
    for i in 0..MT_W.len() {
        MT_W[i].store(0, Relaxed);
    }
    for (tag, score) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            let w = if max_score.is_finite() && max_score > 0.0 && score.is_finite() {
                (10.0 + 190.0 * (score / max_score)).round() as i32
            } else {
                10
            };
            MT_W[slot].store(w.clamp(0, 200), Relaxed);
        }
    }'''

P7_NEW = r'''    // ★ v3.30.1: 区分「训练槽」与「稽古槽」，别再一次清零。
    //
    // 原来先把 10 个槽全写 0，再填 matsuri 给的候选。2.33.2 上 festa.acts 是空缺省
    // （dataset_missing），matsuri 的候选里根本没有 ActTrain，于是 150120..150124
    // 被写成 0 = 「稽古不可选」，把原版 200 的稽古权重整片压掉。
    //
    // 语义：MT_W[i] == -1 → 不接管（hook 返回 trampoline 原值）
    //       MT_W[i] ==  0 → matsuri 明确判为不可选
    let mut has_act_candidates = false;
    for (tag, _) in decision.actions.iter() {
        if (matsuri_bridge::TAG_ACTTRAIN_BASE..matsuri_bridge::TAG_ACTTRAIN_BASE + 5).contains(tag) {
            has_act_candidates = true;
        }
    }

    // 整组发布：写之前 gen 变奇数（写入中），写完再变偶数（可读）。
    // hook 在渲染线程读到奇数就这一拍不接管，避免吃到「清了一半」的中间态。
    let _ = MT_GEN.fetch_add(1, Relaxed);

    // 训练槽 0..5：训练位必然在候选里，matsuri 没给 = 本轮不可选
    for i in 0..5 {
        MT_W[i].store(0, Relaxed);
    }
    // 稽古槽 5..10：只有本轮真的给出稽古候选才接管，否则保持 -1 让原值生效
    for i in 5..MT_W.len() {
        MT_W[i].store(if has_act_candidates { 0 } else { -1 }, Relaxed);
    }

    for (tag, score) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            let w = if max_score.is_finite() && max_score > 0.0 && score.is_finite() {
                (10.0 + 190.0 * (score / max_score)).round() as i32
            } else {
                10
            };
            MT_W[slot].store(w.clamp(0, 200), Relaxed);
        }
    }

    let _ = MT_GEN.fetch_add(1, Relaxed);'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch12: {msg}")
    return 1


def patch_lib() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # ---- P1 ----
    if s.count(P1_ANCHOR) != 1:
        return _fail(f"P1 anchor count={s.count(P1_ANCHOR)}")
    s = s.replace(P1_ANCHOR, P1_FN + P1_ANCHOR, 1)
    if s.count(P1_CALL_OLD) != 1:
        return _fail(f"P1 call anchor count={s.count(P1_CALL_OLD)}")
    s = s.replace(P1_CALL_OLD, P1_CALL_NEW, 1)

    # ---- P2 ----
    if s.count(P2_BUF_OLD) != 1:
        return _fail(f"P2 buf anchor count={s.count(P2_BUF_OLD)}")
    s = s.replace(P2_BUF_OLD, P2_BUF_NEW, 1)
    if s.count(P2_HANDLER_OLD) != 1:
        return _fail(f"P2 handler anchor count={s.count(P2_HANDLER_OLD)}")
    s = s.replace(P2_HANDLER_OLD, P2_HANDLER_NEW, 1)

    lines = s.split("\n")
    hits = 0
    for i, line in enumerate(lines):
        m = P2_JMP_RE.match(line)
        if m:
            indent = m.group(1)
            lines[i] = (
                line
                + "\n"
                + indent
                + "SIGSEGV_TID.store(cur_tid(), std::sync::atomic::Ordering::Relaxed);"
            )
            hits += 1
    if hits < 10:
        return _fail(f"P2 sigsetjmp call sites={hits}（应 >= 10）")
    print(f"  P2 sigsetjmp call sites: {hits}")
    s = "\n".join(lines)

    # ---- P3 ----
    if s.count(P3_ANCHOR) != 1:
        return _fail(f"P3 worker anchor count={s.count(P3_ANCHOR)}")
    s = s.replace(P3_ANCHOR, P3_FN + P3_ANCHOR, 1)
    if s.count(P3_SPAWN_OLD) != 1:
        return _fail(f"P3 spawn anchor count={s.count(P3_SPAWN_OLD)}")
    s = s.replace(P3_SPAWN_OLD, P3_SPAWN_NEW, 1)
    if s.count(P3_ID_OLD) != 1:
        return _fail(f"P3 identity anchor count={s.count(P3_ID_OLD)}")
    s = s.replace(P3_ID_OLD, P3_ID_NEW, 1)
    if s.count(P3_CHARA_OLD) != 1:
        return _fail(f"P3 chara anchor count={s.count(P3_CHARA_OLD)}")
    s = s.replace(P3_CHARA_OLD, P3_CHARA_NEW, 1)

    # ---- P5 ----
    if s.count(P5_TRAIN_OLD) != 1:
        return _fail(f"P5 train anchor count={s.count(P5_TRAIN_OLD)}")
    s = s.replace(P5_TRAIN_OLD, P5_TRAIN_NEW, 1)
    if s.count(P5_SEL_OLD) != 1:
        return _fail(f"P5 selector anchor count={s.count(P5_SEL_OLD)}")
    s = s.replace(P5_SEL_OLD, P5_SEL_NEW, 1)

    # ---- P6 ----
    if s.count(P6_ANCHOR) != 1:
        return _fail(f"P6 push_loop anchor count={s.count(P6_ANCHOR)}")
    s = s.replace(P6_ANCHOR, P6_FN + P6_ANCHOR, 1)
    if s.count(P6_BOOT_OLD) != 1:
        return _fail(f"P6 boot anchor count={s.count(P6_BOOT_OLD)}")
    s = s.replace(P6_BOOT_OLD, P6_BOOT_NEW, 1)
    if s.count(P6_LOOP_OLD) != 1:
        return _fail(f"P6 loop anchor count={s.count(P6_LOOP_OLD)}")
    s = s.replace(P6_LOOP_OLD, P6_LOOP_NEW, 1)

    # ---- P7 ----
    if s.count(P7_GEN_ANCHOR) != 1:
        return _fail(f"P7 MT_W anchor count={s.count(P7_GEN_ANCHOR)}")
    s = s.replace(P7_GEN_ANCHOR, P7_GEN_DEF + P7_GEN_ANCHOR, 1)
    if s.count(P7_HOOK_OLD) != 1:
        return _fail(f"P7 hook anchor count={s.count(P7_HOOK_OLD)}")
    s = s.replace(P7_HOOK_OLD, P7_HOOK_NEW, 1)
    if s.count(P7_OLD) != 1:
        return _fail(f"P7 weight anchor count={s.count(P7_OLD)}")
    s = s.replace(P7_OLD, P7_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    return 0


def patch_bridge() -> int:
    s = open(PATH_BRIDGE, encoding="utf-8").read()
    before = len(s)

    for old, new, tag in (
        (P4_ROOT_OLD, P4_ROOT_NEW, "P4 DATA_ROOT"),
        (P4_INIT_OLD, P4_INIT_NEW, "P4 DATA_INIT"),
        (P4_ASSETS_OLD, P4_ASSETS_NEW, "P4 ensure_assets/ensure_data"),
        (P4_STATUS_OLD, P4_STATUS_NEW, "P4 status_json data_init"),
    ):
        if s.count(old) != 1:
            return _fail(f"{tag} anchor count={s.count(old)}")
        s = s.replace(old, new, 1)

    open(PATH_BRIDGE, "w", encoding="utf-8").write(s)
    print(f"patched {PATH_BRIDGE}: {before} -> {len(s)} bytes")
    return 0


def main() -> int:
    rc = patch_lib()
    if rc != 0:
        return rc
    rc = patch_bridge()
    if rc != 0:
        return rc

    lib = open(PATH, encoding="utf-8").read()
    bridge = open(PATH_BRIDGE, encoding="utf-8").read()

    for needle in (
        "fn read_obscured_int_array",
        "read_obscured_int_array(chara_effect_ids_arr)",
        "SIGSEGV_TID",
        "[u8; 320]",
        "fn mt_thread_attach",
        "mt_thread_attach();",
        "fn push_read_enabled",
        "push_read_enabled()",
        "install_weight_hook();",
        "has_act_candidates",
        "MT_GEN",
        'find_method_entry(class, "SelectTraining", 1)',
    ):
        hits = sum(1 for line in lib.splitlines() if needle in line)
        print(f"  lib · {needle}: {hits}")
        if hits == 0:
            return _fail(f"补丁后仍缺 {needle}")

    for needle in (
        "Android/data/jp.co.cygames.umamusume/files/uma_so/matsuri_data",
        "static DATA_INIT: Mutex<bool>",
        "fn ensure_assets",
        "fn ensure_data",
    ):
        hits = sum(1 for line in bridge.splitlines() if needle in line)
        print(f"  bridge · {needle}: {hits}")
        if hits == 0:
            return _fail(f"bridge 补丁后仍缺 {needle}")

    # CI verify 依赖的字面量：一个都不许消失（LTO 存活判据）
    for needle in (
        "handle_frame",
        "候选动作为空",
        "帧解析失败",
        "守门",
        "matsuri-worker",
        "dataset_missing",
        "chara_class_mismatch",
        "weight_override.txt",
        "find_method_entry",
        "trampoline_miss",
    ):
        hits = sum(1 for line in lib.splitlines() if needle in line)
        print(f"  keep · {needle}: {hits}")
        if hits == 0:
            return _fail(f"verify 字面量 {needle} 丢了")

    for bad in ("falling back to write_hook_bytes", "static mut SIGSEGV_JMP_BUF: [u8; 200]"):
        if bad in lib:
            return _fail(f"旧写法仍在：{bad}")

    print("ci_patch12: all anchors ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())