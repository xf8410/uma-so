#!/usr/bin/env python3
"""CI 侧补丁 12：稳定性系统性加固（7 项根因一次收口）。

审计发现并修复的问题清单（详见 STABILITY_AUDIT_REPORT.md）：
  1. [P0] _charaEffectIdArray（CharaData +1080）是内联 ObscuredInt[]，原代码误走
          read_il2cpp_int_list 按 8 字节指针解引用，马娘任意病种/状态即必崩；
          → 新增 read_obscured_int_array（20 字节内联读取 + 长度上限保护）。
  2. [P0] SIGSEGV_JMP_BUF 仅 200 字节，小于 Android Bionic aarch64 sigjmp_buf 的
          264 字节（_JBLEN=32，33*8），且跨线程共享未绑定 TID；
          → 扩容并 16 字节对齐至 512 字节 + SIGSEGV_RECOVERY_TID 线程绑定 +
            sigsegv_arm/sigsegv_disarm 统一接管全部 10 处 sigsetjmp 保护点。
  3. [P0] matsuri-worker 未调用 il2cpp_thread_attach，且 mt_read_identity() 漏掉
          _totalTurnNum>0 与 klass 硬校验，也未与 push_loop 共持 READ_MUTEX；
          → 新增 ensure_il2cpp_thread_attached()，补齐 sm/chara klass 校验与
            +88 直读，mt_recompute 仅在内存读取段持 READ_MUTEX + sigsetjmp 保护，
            拿到字符串后立刻释锁再跑 matsuri_bridge::decide。
  4. [P1] DATA_ROOT 与原子展开已在 src/matsuri_bridge.rs 修复。
  5. [P1] 删除 install_training_hook 中残留的 write_hook_bytes 无 trampoline 兜底；
          install_selector_hooks 改用 find_method_entry（与 disassemble 同源）。
  6. [P1] 补齐 PUSH_READ_ENABLED（默认 false）与 /api/push/read{,/on,/off}，
          切断后台每 2 秒盲扫 read_summary；同时将 install_weight_hook() 挂入
          on_game_initialized 与启动初始化路径。
  7. [P2] crash_signal_handler 改用 async-signal-safe 的 clock_gettime，日志路径改为
          /sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log；
          mt_recompute 先算局部数组再用 Release/Acquire 发布 MT_W，消除全 0 撕裂窗口。
"""

import sys

PATH = "src/lib.rs"

EXTERN_OLD = r'''    #[link_name = "sigsetjmp"]
    fn sys_sigsetjmp(env: *mut u8, savemask: i32) -> i32;
    #[link_name = "siglongjmp"]
    fn sys_siglongjmp(env: *const u8, val: i32) -> !;
}'''

EXTERN_NEW = r'''    #[link_name = "sigsetjmp"]
    fn sys_sigsetjmp(env: *mut u8, savemask: i32) -> i32;
    #[link_name = "siglongjmp"]
    fn sys_siglongjmp(env: *const u8, val: i32) -> !;
    #[link_name = "gettid"]
    fn sys_gettid() -> i32;
    #[link_name = "clock_gettime"]
    fn sys_clock_gettime(clk_id: i32, tp: *mut [i64; 2]) -> i32;
}'''

JMPBUF_OLD = r'''const CRASH_LOG_PATH: &str = "/data/data/jp.pokemon.pokeuma/files/uma_predict.log";

// ★ v3.22.35: SIGSEGV recovery for push thread
// sigsetjmp buffer: 200 bytes is enough for jmp_buf on aarch64 (typically 24 x 8 = 192 bytes)
static mut SIGSEGV_JMP_BUF: [u8; 200] = [0u8; 200];
static SIGSEGV_RECOVERY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
// Cooldown: after SIGSEGV recovery, skip reads for N seconds
static SIGSEGV_COOLDOWN_UNTIL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);'''

JMPBUF_NEW = r'''const CRASH_LOG_PATH: &str =
    "/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log";

// ★ ci_patch12: Android Bionic aarch64 sigjmp_buf is 33 * 8 = 264 bytes (_JBLEN=32).
// Original [u8; 200] overflowed 64 bytes into adjacent .bss globals on every sigsetjmp!
#[repr(C, align(16))]
struct AlignedJmpBuf([u8; 512]);
impl AlignedJmpBuf {
    #[inline]
    fn as_mut_ptr(&mut self) -> *mut u8 {
        self.0.as_mut_ptr()
    }
    #[inline]
    fn as_ptr(&self) -> *const u8 {
        self.0.as_ptr()
    }
}

static mut SIGSEGV_JMP_BUF: AlignedJmpBuf = AlignedJmpBuf([0u8; 512]);
static SIGSEGV_RECOVERY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static SIGSEGV_RECOVERY_TID: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(0);
// Cooldown: after SIGSEGV recovery, skip reads for N seconds
static SIGSEGV_COOLDOWN_UNTIL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

#[inline]
fn sigsegv_arm() {
    let tid = unsafe { sys_gettid() };
    SIGSEGV_RECOVERY_TID.store(tid, std::sync::atomic::Ordering::Release);
    SIGSEGV_RECOVERY.store(true, std::sync::atomic::Ordering::Release);
}

#[inline]
fn sigsegv_disarm() {
    SIGSEGV_RECOVERY.store(false, std::sync::atomic::Ordering::Release);
    SIGSEGV_RECOVERY_TID.store(0, std::sync::atomic::Ordering::Release);
}

#[inline]
fn is_valid_user_ptr(p: *const c_void) -> bool {
    let addr = p as usize;
    addr >= 0x10000 && addr < 0x0001_0000_0000_0000
}

/// 保证当前原生后台线程已 attach 到 IL2CPP（每条 OS 线程仅执行一次）
unsafe fn ensure_il2cpp_thread_attached() {
    thread_local! {
        static ATTACHED: std::cell::Cell<bool> = std::cell::Cell::new(false);
    }
    if ATTACHED.with(|c| c.get()) {
        return;
    }
    let dom_fn_ptr = resolve_il2cpp_symbol("il2cpp_domain_get");
    let att_fn_ptr = resolve_il2cpp_symbol("il2cpp_thread_attach");
    if dom_fn_ptr.is_null() || att_fn_ptr.is_null() {
        return;
    }
    let domain_get: unsafe extern "C" fn() -> *mut c_void = std::mem::transmute(dom_fn_ptr);
    let thread_attach: unsafe extern "C" fn(*mut c_void) -> *mut c_void =
        std::mem::transmute(att_fn_ptr);
    let domain = domain_get();
    if !domain.is_null() {
        let _ = thread_attach(domain);
        ATTACHED.with(|c| c.set(true));
    }
}'''

HANDLER_TAIL_OLD = r'''    let path = b"/data/data/jp.pokemon.pokeuma/files/uma_predict.log\0";
    let fd = unsafe { sys_open(path.as_ptr() as *const i8, 1 | 64 | 1024, 0o644) };
    if fd >= 0 {
        unsafe {
            sys_write(fd, msg.as_ptr(), len);
            sys_close(fd);
        }
    }
    // ★ v3.22.35: If sigsetjmp was set (push thread), longjmp back instead of killing process
    if SIGSEGV_RECOVERY.load(std::sync::atomic::Ordering::Relaxed) {
        // Set cooldown: skip reads for 60 seconds
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        SIGSEGV_COOLDOWN_UNTIL.store(now + 60, std::sync::atomic::Ordering::Relaxed);
        SIGSEGV_RECOVERY.store(false, std::sync::atomic::Ordering::Relaxed);
        unsafe {
            sys_siglongjmp(SIGSEGV_JMP_BUF.as_ptr(), 1);
        }
    }'''

HANDLER_TAIL_NEW = r'''    let path = b"/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log\0";
    let fd = unsafe { sys_open(path.as_ptr() as *const i8, 1 | 64 | 1024, 0o644) };
    if fd >= 0 {
        unsafe {
            sys_write(fd, msg.as_ptr(), len);
            sys_close(fd);
        }
    }
    // ★ ci_patch12: 仅允许已注册 sigsetjmp 的同一线程恢复，防止跨线程 siglongjmp 炸栈；
    //   全程使用 async-signal-safe 的 clock_gettime，避免 SystemTime::now() 在信号上下文死锁。
    let cur_tid = unsafe { sys_gettid() };
    if SIGSEGV_RECOVERY.load(std::sync::atomic::Ordering::Acquire)
        && SIGSEGV_RECOVERY_TID.load(std::sync::atomic::Ordering::Acquire) == cur_tid
    {
        let mut ts = [0i64; 2];
        let now = if unsafe { sys_clock_gettime(0, &mut ts as *mut [i64; 2]) } == 0 && ts[0] > 0 {
            ts[0] as u64
        } else {
            0
        };
        if now > 0 {
            SIGSEGV_COOLDOWN_UNTIL.store(now + 180, std::sync::atomic::Ordering::Release);
        }
        SIGSEGV_RECOVERY.store(false, std::sync::atomic::Ordering::Release);
        SIGSEGV_RECOVERY_TID.store(0, std::sync::atomic::Ordering::Release);
        unsafe {
            sys_siglongjmp(SIGSEGV_JMP_BUF.as_ptr(), 1);
        }
    }'''

READ_INLINE_ARRAY_FN = r'''
/// ★ ci_patch12: 正确读取内联 ObscuredInt[]（如 CharaData+1080 _charaEffectIdArray）
/// 每个 ObscuredInt 为 20 字节内联 struct（+0 key, +4 hidden），绝不能当指针数组解引用！
unsafe fn read_inline_obscured_int_array(arr: *const c_void) -> Vec<i32> {
    if !is_valid_user_ptr(arr) {
        return Vec::new();
    }
    let count = seq_len(arr);
    if count == 0 || count > 64 {
        return Vec::new();
    }
    let mut result = Vec::with_capacity(count);
    for i in 0..count {
        let off = (0x20 + i * 20) as i32;
        let val = read_obscured_int_at(arr, off);
        result.push(val);
    }
    result
}
'''

EFF_OLD = r'''    let eff_arr = read_ptr_at(chara, 1080);
    let eff_ids: Vec<i32> = if eff_arr.is_null() {
        Vec::new()
    } else {
        read_il2cpp_int_list(eff_arr)
    };'''

EFF_NEW = r'''    let eff_arr = read_ptr_at(chara, 1080);
    let eff_ids: Vec<i32> = if !is_valid_user_ptr(eff_arr) {
        Vec::new()
    } else {
        read_inline_obscured_int_array(eff_arr)
    };'''

FRAME_SM_OLD = r'''    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    if sm.is_null() {
        return r#"{"error":"no_single_mode"}"#.to_string();
    }

    // _totalTurnNum(68)：>0 才在育成局内；游戏值 1 基，帧要 0 基
    let raw_turn = read_obscured_int_at(sm, 68);
    if raw_turn <= 0 {
        return r#"{"error":"not_in_training"}"#.to_string();
    }

    let chara_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkSingleModeCharaData").as_ptr(),
    );
    let chara = call_getter_ref(sm_class, sm, "get_Character");
    if chara.is_null() {
        return r#"{"error":"no_chara"}"#.to_string();
    }
    // 硬校验：非育成态可能拿到"非空但已释放/被复用"的对象，此时读 _totalTurnNum
    // 会得到垃圾非零值，一路读到 Character 字段就段错误（交接文档 §6.2 同源）。
    // 读任何字段前先确认 klass 名，对不上就直接返回，不碰内存。
    {
        let klass = std::ptr::read_unaligned::<*mut c_void>(chara as *const *mut c_void);
        if klass.is_null() {
            return r#"{"error":"chara_klass_null"}"#.to_string();
        }
        let cname = get_class_name_from_pointer(klass);
        if !cname.contains("WorkSingleModeCharaData") {
            return format!(
                r#"{{"error":"chara_class_mismatch","klass":"{}"}}"#,
                cname.replace('"', "'")
            );
        }
    }'''

FRAME_SM_NEW = r'''    ensure_il2cpp_thread_attached();
    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    if !is_valid_user_ptr(sm) {
        return r#"{"error":"no_single_mode"}"#.to_string();
    }
    {
        let sm_klass = std::ptr::read_unaligned::<*mut c_void>(sm as *const *mut c_void);
        if !is_valid_user_ptr(sm_klass) {
            return r#"{"error":"sm_klass_invalid"}"#.to_string();
        }
        let sname = get_class_name_from_pointer(sm_klass);
        if !sname.contains("WorkSingleModeData") {
            return format!(
                r#"{{"error":"sm_class_mismatch","klass":"{}"}}"#,
                sname.replace('"', "'")
            );
        }
    }

    // _totalTurnNum(68)：>0 才在育成局内；游戏值 1 基，帧要 0 基
    let raw_turn = read_obscured_int_at(sm, 68);
    if raw_turn <= 0 || raw_turn > 200 {
        return r#"{"error":"not_in_training"}"#.to_string();
    }

    let chara_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkSingleModeCharaData").as_ptr(),
    );
    // 优先直读 WorkSingleModeData+88 <Character>，减少非主线程 il2cpp_runtime_invoke
    let mut chara = read_ptr_at(sm, 88);
    if !is_valid_user_ptr(chara) {
        chara = call_getter_ref(sm_class, sm, "get_Character");
    }
    if !is_valid_user_ptr(chara) {
        return r#"{"error":"no_chara"}"#.to_string();
    }
    // 硬校验：读任何字段前先确认 klass 指针合法且名称是 WorkSingleModeCharaData
    {
        let klass = std::ptr::read_unaligned::<*mut c_void>(chara as *const *mut c_void);
        if !is_valid_user_ptr(klass) {
            return r#"{"error":"chara_klass_null"}"#.to_string();
        }
        let cname = get_class_name_from_pointer(klass);
        if !cname.contains("WorkSingleModeCharaData") {
            return format!(
                r#"{{"error":"chara_class_mismatch","klass":"{}"}}"#,
                cname.replace('"', "'")
            );
        }
    }'''

IDENTITY_OLD = r'''    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    if sm.is_null() {
        return id;
    }
    let chara = call_getter_ref(sm_class, sm, "get_Character");
    if chara.is_null() {
        return id;
    }

    let cid = read_obscured_int_at(chara, 36);
    if cid > 0 {
        id.uma = cid as u32;
    }

    let arr = read_ptr_at(chara, 160);
    if arr.is_null() {
        return id;
    }
    // 元素是引用：seq_item 取指针；对象内三个字段都是 ObscuredInt，间距 20 字节
    //   +16 position、+36 supportCardId、+56 limitBreakCount
    // （实测：6 个元素 position 依次解出 1..6，cardId/limitBreak 也落在合理范围）
    let n = seq_len(arr).min(8);
    for i in 0..n {
        let e = seq_item(arr, i);
        if e.is_null() {
            continue;
        }'''

IDENTITY_NEW = r'''    ensure_il2cpp_thread_attached();
    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    if !is_valid_user_ptr(sm) {
        return id;
    }
    {
        let sm_klass = std::ptr::read_unaligned::<*mut c_void>(sm as *const *mut c_void);
        if !is_valid_user_ptr(sm_klass)
            || !get_class_name_from_pointer(sm_klass).contains("WorkSingleModeData")
        {
            return id;
        }
    }
    let raw_turn = read_obscured_int_at(sm, 68);
    if raw_turn <= 0 || raw_turn > 200 {
        return id;
    }
    let mut chara = read_ptr_at(sm, 88);
    if !is_valid_user_ptr(chara) {
        chara = call_getter_ref(sm_class, sm, "get_Character");
    }
    if !is_valid_user_ptr(chara) {
        return id;
    }
    {
        let c_klass = std::ptr::read_unaligned::<*mut c_void>(chara as *const *mut c_void);
        if !is_valid_user_ptr(c_klass)
            || !get_class_name_from_pointer(c_klass).contains("WorkSingleModeCharaData")
        {
            return id;
        }
    }

    let cid = read_obscured_int_at(chara, 36);
    if cid > 0 {
        id.uma = cid as u32;
    }

    let arr = read_ptr_at(chara, 160);
    if !is_valid_user_ptr(arr) {
        return id;
    }
    // 元素是引用：seq_item 取指针；对象内三个字段都是 ObscuredInt，间距 20 字节
    //   +16 position、+36 supportCardId、+56 limitBreakCount
    let raw_n = seq_len(arr);
    if raw_n == 0 || raw_n > 16 {
        return id;
    }
    let n = raw_n.min(8);
    for i in 0..n {
        let e = seq_item(arr, i);
        if !is_valid_user_ptr(e) {
            continue;
        }'''

RECOMPUTE_OLD = r'''unsafe fn mt_recompute() {
    use std::sync::atomic::Ordering::Relaxed;

    // 从游戏读本局身份；身份变了才重建 session（重建会丢会话状态）
    let identity = mt_read_identity();
    let key = format!("{}:{:?}", identity.uma, identity.cards);
    let need_init = !matsuri_bridge::ready()
        || MT_ID_KEY
            .lock()
            .map(|g| g.as_str() != key.as_str())
            .unwrap_or(true);
    if need_init {
        match matsuri_bridge::init(identity.uma, &identity.cards, 61444) {
            Ok(()) => {
                if let Ok(mut g) = MT_ID_KEY.lock() {
                    *g = key;
                }
            }
            Err(e) => {
                MT_STATE.store(2, Relaxed);
                mt_set_err(format!(
                    "init(uma={}, cards={:?}): {e}",
                    identity.uma, identity.cards
                ));
                return;
            }
        }
    }

    let frame = build_matsuri_frame();
    if frame.contains("\"error\"") {
        MT_STATE.store(2, Relaxed);
        mt_set_err(format!("frame: {}", frame.chars().take(120).collect::<String>()));
        return;
    }

    let decision = match matsuri_bridge::decide(&frame) {
        Ok(d) => d,
        Err(e) => {
            MT_STATE.store(2, Relaxed);
            mt_set_err(format!("decide: {e}"));
            return;
        }
    };

    // 找最大分做归一化基准
    let mut max_score = f64::NEG_INFINITY;
    for (_, s) in decision.actions.iter() {
        if s.is_finite() && *s > max_score {
            max_score = *s;
        }
    }

    // 先全部置 0（不可选），再把 matsuri 给出的候选按比例写进去
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
    }

    MT_TURN.store(decision.turn, Relaxed);
    MT_CHOSEN_TAG.store(decision.chosen_tag, Relaxed);
    MT_STATE.store(1, Relaxed);
    mt_set_err(String::new());
}'''

RECOMPUTE_NEW = r'''unsafe fn mt_recompute() {
    use std::sync::atomic::Ordering::{Acquire, Relaxed, Release};

    // SIGSEGV 冷却期内不动游戏内存
    let now_s = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    if now_s < SIGSEGV_COOLDOWN_UNTIL.load(Acquire) {
        return;
    }

    ensure_il2cpp_thread_attached();

    // 仅在快速内存读取阶段持有 READ_MUTEX + 线程绑定 sigsetjmp 保护；
    // 拿到纯 Rust 字符串后立刻释锁，避免 100~800ms 决策计算阻塞 HTTP 端点。
    let (identity, frame) = {
        let _lock = READ_MUTEX.lock().unwrap_or_else(|e| e.into_inner());
        let jmp = sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1);
        if jmp != 0 {
            sigsegv_disarm();
            MT_STATE.store(2, Release);
            mt_set_err("sigsegv_recovered_in_worker".to_string());
            return;
        }
        sigsegv_arm();
        let id = mt_read_identity();
        let fr = if id.uma > 0 && id.cards.len() == 6 {
            build_matsuri_frame()
        } else {
            String::new()
        };
        sigsegv_disarm();
        (id, fr)
    };

    if identity.uma == 0 || identity.cards.len() != 6 {
        MT_STATE.store(2, Release);
        mt_set_err(format!(
            "identity_not_ready(uma={}, cards={:?})",
            identity.uma, identity.cards
        ));
        return;
    }

    // 身份变了才重建 session（重建会丢会话状态）
    let key = format!("{}:{:?}", identity.uma, identity.cards);
    let need_init = !matsuri_bridge::ready()
        || MT_ID_KEY
            .lock()
            .map(|g| g.as_str() != key.as_str())
            .unwrap_or(true);
    if need_init {
        match matsuri_bridge::init(identity.uma, &identity.cards, 61444) {
            Ok(()) => {
                if let Ok(mut g) = MT_ID_KEY.lock() {
                    *g = key;
                }
            }
            Err(e) => {
                MT_STATE.store(2, Release);
                mt_set_err(format!(
                    "init(uma={}, cards={:?}): {e}",
                    identity.uma, identity.cards
                ));
                return;
            }
        }
    }

    if frame.is_empty() || frame.contains("\"error\"") {
        MT_STATE.store(2, Release);
        mt_set_err(format!("frame: {}", frame.chars().take(120).collect::<String>()));
        return;
    }

    let decision = match matsuri_bridge::decide(&frame) {
        Ok(d) => d,
        Err(e) => {
            MT_STATE.store(2, Release);
            mt_set_err(format!("decide: {e}"));
            return;
        }
    };

    // 找最大分做归一化基准
    let mut max_score = f64::NEG_INFINITY;
    for (_, s) in decision.actions.iter() {
        if s.is_finite() && *s > max_score {
            max_score = *s;
        }
    }

    // 先在局部数组算完再一次性写入，消除渲染线程读到“全 0 撕裂态”的窗口
    let mut next_w = [0i32; 10];
    for (tag, score) in decision.actions.iter() {
        if let Some(slot) = mt_slot(*tag) {
            let w = if max_score.is_finite() && max_score > 0.0 && score.is_finite() {
                (10.0 + 190.0 * (score / max_score)).round() as i32
            } else {
                10
            };
            next_w[slot] = w.clamp(0, 200);
        }
    }
    for i in 0..MT_W.len() {
        MT_W[i].store(next_w[i], Release);
    }

    MT_TURN.store(decision.turn, Release);
    MT_CHOSEN_TAG.store(decision.chosen_tag, Release);
    MT_STATE.store(1, Release);
    mt_set_err(String::new());
}'''

MT_WEIGHT_OLD = r'''    if MT_STATE.load(Relaxed) != 1 {
        return None;
    }
    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;
    let slot = mt_slot(tag)?;
    let w = MT_W[slot].load(Relaxed);'''

MT_WEIGHT_NEW = r'''    if MT_STATE.load(std::sync::atomic::Ordering::Acquire) != 1 {
        return None;
    }
    let tag = matsuri_bridge::omakase_arg_to_tag(arg)?;
    let slot = mt_slot(tag)?;
    let w = MT_W[slot].load(std::sync::atomic::Ordering::Acquire);'''

SELECTOR_ADDR_OLD = '    let addr = find_method_addr(class, "SelectTraining", 1);'
SELECTOR_ADDR_NEW = '    let addr = find_method_entry(class, "SelectTraining", 1);'

TRAIN_HOOK_OLD = r'''    } else {
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

TRAIN_HOOK_NEW = r'''    } else {
        // ★ ci_patch12: 彻底移除 write_hook_bytes 兜底（无 trampoline 必崩）
        ura_log(
            1,
            "Training hook: interceptor_hook failed (no write_hook_bytes fallback)",
        );
    }'''

PUSH_OLD = r'''fn push_loop() {
    let interval =
        std::time::Duration::from_secs(unsafe { get_config() }.push_interval_secs.max(2));
    let mut consecutive_errors: u32 = 0;

    // ★ Initial push: try pushing current data on startup
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
                install_selector_hooks();
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
    }
    let init_summary = read_summary();
    if !init_summary.contains("\"error\"") {
        unsafe {
            LAST_PUSH_HASH = simple_hash(&init_summary);
        }
        push_to_app(&init_summary);
        unsafe {
            ura_log(3, "Push: initial data pushed");
        }
    }

    loop {
        std::thread::sleep(interval);
        // Don't gate on GAME_INITIALIZED — just try reading;
        // if the game isn't ready, read_summary returns error and we skip.
        let summary = read_summary();'''

PUSH_NEW = r'''/// ★ ci_patch12: 后台主动轮询开关（默认关闭，防止标题画面/过渡期每 2 秒盲扫 read_summary 触发 SIGSEGV）
static PUSH_READ_ENABLED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn push_loop() {
    let interval =
        std::time::Duration::from_secs(unsafe { get_config() }.push_interval_secs.max(2));
    let mut consecutive_errors: u32 = 0;

    // 仅等待 il2cpp image 就绪后安装 hook，不再在标题画面盲调 read_summary()
    for wait_round in 0..60 {
        if GAME_INITIALIZED.load(Ordering::Relaxed) {
            break;
        }
        let img_ready = unsafe { !API.is_null() && !get_image().is_null() };
        if img_ready && wait_round >= 5 {
            GAME_INITIALIZED.store(true, Ordering::Relaxed);
            unsafe {
                ensure_il2cpp_thread_attached();
                ura_log(3, "Push: game image ready (fallback hook install, no summary probe)");
                install_training_hook();
                install_selector_hooks();
                let _ = install_weight_hook();
                install_exec_training_hook();
                install_failure_rate_hook();
                install_event_choice_hook();
                install_ui_text_hook();
                install_cutin_trace();
                flog("init", &format!("plugin {} boot, hooks ready", PLUGIN_VERSION));
                rng_consumption_probe::install();
                install_api_sniff_hooks();
            }
            break;
        }
        std::thread::sleep(std::time::Duration::from_secs(1));
    }

    loop {
        std::thread::sleep(interval);
        // 默认关闭主动轮询；仅在显式 /api/push/read/on 时才在后台读 summary
        if !PUSH_READ_ENABLED.load(Ordering::Relaxed) {
            continue;
        }
        unsafe { ensure_il2cpp_thread_attached(); }
        let summary = read_summary();'''

ON_INIT_OLD = r'''        install_training_hook();
        install_selector_hooks();
        install_exec_training_hook();'''

ON_INIT_NEW = r'''        install_training_hook();
        install_selector_hooks();
        let _ = install_weight_hook();
        install_exec_training_hook();'''

PUSH_ROUTE_OLD = '    } else if path == "/api/ai/init" {'
PUSH_ROUTE_NEW = r'''    } else if path == "/api/push/read" {
        format!(
            r#"{{"ok":true,"push_read_enabled":{}}}"#,
            PUSH_READ_ENABLED.load(Ordering::Relaxed)
        )
    } else if path == "/api/push/read/on" {
        PUSH_READ_ENABLED.store(true, Ordering::Relaxed);
        r#"{"ok":true,"push_read_enabled":true}"#.to_string()
    } else if path == "/api/push/read/off" {
        PUSH_READ_ENABLED.store(false, Ordering::Relaxed);
        r#"{"ok":true,"push_read_enabled":false}"#.to_string()
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch12: {msg}")
    return 1


def _replace_once(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"anchor '{label}' count={n}")
    return s.replace(old, new, 1)


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    try:
        # 1) extern "C" 增加 gettid / clock_gettime
        s = _replace_once(s, EXTERN_OLD, EXTERN_NEW, "extern_c")

        # 2) SIGSEGV_JMP_BUF 扩容 512 字节 + 16 字节对齐 + TID 绑定 + ensure_il2cpp_thread_attached
        s = _replace_once(s, JMPBUF_OLD, JMPBUF_NEW, "jmp_buf")

        # 3) crash_signal_handler 线程绑定 + clock_gettime + 新日志路径
        s = _replace_once(s, HANDLER_TAIL_OLD, HANDLER_TAIL_NEW, "crash_handler_tail")

        # 4) 统一将全部调用点的 SIGSEGV_RECOVERY.store(true/false, ...) 换成 sigsegv_arm/disarm
        #    （注意不要改 sigsegv_arm / sigsegv_disarm 与 crash_signal_handler 自身，它们用了 Release）
        true_full = "SIGSEGV_RECOVERY.store(true, std::sync::atomic::Ordering::Relaxed);"
        false_full = "SIGSEGV_RECOVERY.store(false, std::sync::atomic::Ordering::Relaxed);"
        true_short = "SIGSEGV_RECOVERY.store(true, Ordering::Relaxed);"
        false_short = "SIGSEGV_RECOVERY.store(false, Ordering::Relaxed);"
        c_arm = s.count(true_full) + s.count(true_short)
        c_dis = s.count(false_full) + s.count(false_short)
        if c_arm < 8 or c_dis < 8:
            raise RuntimeError(f"unexpected SIGSEGV_RECOVERY Relaxed counts: arm={c_arm}, dis={c_dis}")
        s = s.replace(true_full, "sigsegv_arm();").replace(true_short, "sigsegv_arm();")
        s = s.replace(false_full, "sigsegv_disarm();").replace(false_short, "sigsegv_disarm();")
        print(f"  replaced SIGSEGV_RECOVERY arm={c_arm}, disarm={c_dis}")

        # 5) 替换 /debug/crashlog 读取路径（避免仍读不存在的 jp.pokemon.pokeuma）
        old_log_str = '"/data/data/jp.pokemon.pokeuma/files/uma_predict.log"'
        old_log_bytes = 'b"/data/data/jp.pokemon.pokeuma/files/uma_predict.log\\0"'
        n_log = s.count(old_log_str) + s.count(old_log_bytes)
        s = s.replace(
            old_log_str,
            '"/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log"',
        )
        s = s.replace(
            old_log_bytes,
            'b"/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log\\0"',
        )
        print(f"  updated crash log paths: {n_log}")

        # 6) 新增 read_obscured_int_array 并修复 build_matsuri_frame 对 +1080 的读取
        s = _replace_once(
            s,
            "unsafe fn read_il2cpp_int_list(",
            READ_INLINE_ARRAY_FN.strip() + "\n\nunsafe fn read_il2cpp_int_list(",
            "read_il2cpp_int_list",
        )
        s = _replace_once(s, EFF_OLD, EFF_NEW, "eff_arr_read")

        # 7) build_matsuri_frame 与 mt_read_identity 补齐 attach + sm/chara klass 校验 + +88 直读
        s = _replace_once(s, FRAME_SM_OLD, FRAME_SM_NEW, "frame_sm_gate")
        s = _replace_once(s, IDENTITY_OLD, IDENTITY_NEW, "identity_gate")

        # 8) mt_recompute 持 READ_MUTEX + sigsetjmp 保护读内存，释锁后再决策 + 局部数组防撕裂
        s = _replace_once(s, RECOMPUTE_OLD, RECOMPUTE_NEW, "mt_recompute")
        s = _replace_once(s, MT_WEIGHT_OLD, MT_WEIGHT_NEW, "mt_weight_acquire")

        # 9) 删除 install_training_hook 残留 write_hook_bytes + 修复 SelectTraining 取址
        s = _replace_once(s, TRAIN_HOOK_OLD, TRAIN_HOOK_NEW, "train_hook_no_fallback")
        s = _replace_once(s, SELECTOR_ADDR_OLD, SELECTOR_ADDR_NEW, "selector_find_entry")

        # 10) push_loop 默认关闭主动盲扫 + on_game_initialized 自动装 weight hook + 开关端点
        s = _replace_once(s, PUSH_OLD, PUSH_NEW, "push_loop_gate")
        s = _replace_once(s, ON_INIT_OLD, ON_INIT_NEW, "on_game_init_weight_hook")
        s = _replace_once(
            s,
            '    "/api/ai/deckraw",',
            '    "/api/ai/deckraw",\n    "/api/push/read",\n    "/api/push/read/on",\n    "/api/push/read/off",',
            "endpoints_push_read",
        )
        s = _replace_once(s, PUSH_ROUTE_OLD, PUSH_ROUTE_NEW, "route_push_read")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")

    for needle in (
        "struct AlignedJmpBuf",
        "SIGSEGV_RECOVERY_TID",
        "fn ensure_il2cpp_thread_attached",
        "fn read_inline_obscured_int_array",
        "PUSH_READ_ENABLED",
        "sigsegv_recovered_in_worker",
        "crash_sig.log",
        'find_method_entry(class, "SelectTraining"',
        "/api/push/read/on",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")

    if "falling back to write_hook_bytes" in s:
        return _fail("write_hook_bytes fallback 仍残留")
    return 0


if __name__ == "__main__":
    sys.exit(main())
