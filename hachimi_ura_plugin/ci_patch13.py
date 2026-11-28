#!/usr/bin/env python3
"""CI 侧补丁 13：修复 ART SIGSEGV 信号链破坏 + 消除 Dobby trampoline 越页踩踏。

真机/雷电 tombstone_04 实测取证（2026-11-27）：
  Fatal signal 11 (SIGSEGV), code 2 (SEGV_ACCERR), fault addr 0x78d0e3efb000
  in tid (Adjust-pool-24-) -> libart.so art::jni::LocalReferenceTable::Add
  memory map:
    ---> 000078d0'e3efa000-000078d0'e3efbfff r--  0  2000
  memory near fault addr:
    000078d0e3efafe0..afff 为 CutIn.CreateModelList 的 ARM64 trampoline 尾部！

双重根因：
  1. [P0] init_crash_handler() 用 sys_signal(11, crash_signal_handler) 覆盖了 Android
     ART / libsigchain 的 SIGSEGV 处理器，且非恢复路径直接 sys_signal(11, 0) + raise，
     未链接转发给先前的 sigaction 处理器，会直接杀掉正常的 ART 隐式 SIGSEGV 或第三方
     Crashlytics 信号链。
     → 改用 libc::sigaction(SA_SIGINFO | SA_ONSTACK) 保存并链式转发给 OLD_SIGSEGV_ACT /
       OLD_SIGBUS_ACT，不再劫持 SIGABRT / SIGFPE。
  2. [P0] 启动时安装了 14 个当前未使用的诊断/嗅探/文本/过场 hook（CutIn 4 个、
     API sniff 6 个、RNG probe 2 个、UI text 2 个），撑满 Dobby 4KB trampoline 页，
     最后一个 CutIn.CreateModelList 恰好落在页尾 +0xfe4，mprotect 向上取整 2 页
     (0x2000) 直接把紧邻的 ART LocalReferenceTable 可写页 (rw-) 改成只读 (r--)，
     任何 Java 线程做 JNI AddLocalReference 立即 SEGV_ACCERR 闪退！
     → 启动路径精简为仅安装 matsuri/育成核心 hook（GetBaseWeight / SelectTraining /
       training / exec_training / failure_rate / event_choice），去掉未使用的
       CutIn / API sniff / RNG probe / UI text 自动安装。
"""

import sys

PATH = "src/lib.rs"

OLD_HANDLER = r'''extern "C" fn crash_signal_handler(sig: i32) {
    CRASH_SIG.store(sig, std::sync::atomic::Ordering::Relaxed);
    CRASH_STEP.store(
        PREDICT_STEP.load(std::sync::atomic::Ordering::Relaxed),
        std::sync::atomic::Ordering::Relaxed,
    );
    // Log the crash
    let step = PREDICT_STEP.load(std::sync::atomic::Ordering::Relaxed);
    let mut msg = [0u8; 64];
    let p = b"CRASH at step ";
    msg[..p.len()].copy_from_slice(p);
    let mut len = p.len();
    let mut n = step;
    if n == 0 {
        msg[len] = b'0';
        len += 1;
    } else {
        let mut digits = [0u8; 10];
        let mut dlen = 0;
        while n > 0 {
            digits[dlen] = b'0' + (n % 10) as u8;
            n /= 10;
            dlen += 1;
        }
        for i in (0..dlen).rev() {
            msg[len] = digits[i];
            len += 1;
        }
    }
    let s = b" sig=";
    msg[len..len + s.len()].copy_from_slice(s);
    len += s.len();
    let mut n2 = sig as u32;
    if n2 == 0 {
        msg[len] = b'0';
        len += 1;
    } else {
        let mut digits = [0u8; 10];
        let mut dlen = 0;
        while n2 > 0 {
            digits[dlen] = b'0' + (n2 % 10) as u8;
            n2 /= 10;
            dlen += 1;
        }
        for i in (0..dlen).rev() {
            msg[len] = digits[i];
            len += 1;
        }
    }
    let r = b" RECOVERED";
    msg[len..len + r.len()].copy_from_slice(r);
    len += r.len();
    msg[len] = b'\n';
    len += 1;
    let path = b"/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log\0";
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
    }
    // Not in recovery context — re-raise signal to kill process (unrecoverable)
    unsafe {
        sys_signal(sig, 0);
        sys_raise(sig);
    }
}

fn init_crash_handler() {
    unsafe {
        let handler = crash_signal_handler as usize;
        sys_signal(11, handler); // SIGSEGV
        sys_signal(6, handler); // SIGABRT
        sys_signal(7, handler); // SIGBUS
        sys_signal(8, handler); // SIGFPE
    }'''

NEW_HANDLER = r'''static mut OLD_SIGSEGV_ACT: libc::sigaction = unsafe { std::mem::zeroed() };
static mut OLD_SIGBUS_ACT: libc::sigaction = unsafe { std::mem::zeroed() };

/// ★ ci_patch13: 符合 Android ART / libsigchain 规范的 sigaction 信号处理器
/// - 仅在当前线程正处于本插件 sigsetjmp 保护窗口内时执行恢复；
/// - 其余所有线程/场景均完整链式转发给原先保存的 sigaction（保护 ART FaultManager / Crashlytics）。
extern "C" fn crash_signal_handler(
    sig: libc::c_int,
    info: *mut libc::siginfo_t,
    uctx: *mut libc::c_void,
) {
    let cur_tid = unsafe { sys_gettid() };
    if SIGSEGV_RECOVERY.load(std::sync::atomic::Ordering::Acquire)
        && SIGSEGV_RECOVERY_TID.load(std::sync::atomic::Ordering::Acquire) == cur_tid
    {
        CRASH_SIG.store(sig, std::sync::atomic::Ordering::Relaxed);
        let step = PREDICT_STEP.load(std::sync::atomic::Ordering::Relaxed);
        CRASH_STEP.store(step, std::sync::atomic::Ordering::Relaxed);

        let mut msg = [0u8; 64];
        let p = b"CRASH at step ";
        msg[..p.len()].copy_from_slice(p);
        let mut len = p.len();
        let mut n = step;
        if n == 0 {
            msg[len] = b'0';
            len += 1;
        } else {
            let mut digits = [0u8; 10];
            let mut dlen = 0;
            while n > 0 {
                digits[dlen] = b'0' + (n % 10) as u8;
                n /= 10;
                dlen += 1;
            }
            for i in (0..dlen).rev() {
                msg[len] = digits[i];
                len += 1;
            }
        }
        let s = b" sig=";
        msg[len..len + s.len()].copy_from_slice(s);
        len += s.len();
        let mut n2 = sig as u32;
        if n2 == 0 {
            msg[len] = b'0';
            len += 1;
        } else {
            let mut digits = [0u8; 10];
            let mut dlen = 0;
            while n2 > 0 {
                digits[dlen] = b'0' + (n2 % 10) as u8;
                n2 /= 10;
                dlen += 1;
            }
            for i in (0..dlen).rev() {
                msg[len] = digits[i];
                len += 1;
            }
        }
        let r = b" RECOVERED\n";
        msg[len..len + r.len()].copy_from_slice(r);
        len += r.len();
        let path = b"/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/crash_sig.log\0";
        let fd = unsafe { sys_open(path.as_ptr() as *const i8, 1 | 64 | 1024, 0o644) };
        if fd >= 0 {
            unsafe {
                sys_write(fd, msg.as_ptr(), len);
                sys_close(fd);
            }
        }

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
    }

    // 非本插件保护上下文：链式转发给先前的 sigaction（ART / Crashlytics 等）
    unsafe {
        let old = if sig == libc::SIGSEGV {
            &OLD_SIGSEGV_ACT
        } else {
            &OLD_SIGBUS_ACT
        };
        let fn_addr = old.sa_sigaction;
        if fn_addr != 0
            && fn_addr != libc::SIG_DFL
            && fn_addr != libc::SIG_IGN
            && fn_addr != (crash_signal_handler as usize)
        {
            if (old.sa_flags as libc::c_int & libc::SA_SIGINFO) != 0 {
                let prev: extern "C" fn(libc::c_int, *mut libc::siginfo_t, *mut libc::c_void) =
                    std::mem::transmute(fn_addr);
                prev(sig, info, uctx);
                return;
            } else {
                let prev: extern "C" fn(libc::c_int) = std::mem::transmute(fn_addr);
                prev(sig);
                return;
            }
        }
        let mut dfl: libc::sigaction = std::mem::zeroed();
        dfl.sa_sigaction = libc::SIG_DFL;
        libc::sigaction(sig, &dfl, std::ptr::null_mut());
        libc::raise(sig);
    }
}

fn init_crash_handler() {
    unsafe {
        let mut act: libc::sigaction = std::mem::zeroed();
        libc::sigemptyset(&mut act.sa_mask);
        act.sa_flags = (libc::SA_SIGINFO | libc::SA_ONSTACK) as _;
        act.sa_sigaction = crash_signal_handler as usize;
        libc::sigaction(libc::SIGSEGV, &act, &mut OLD_SIGSEGV_ACT);
        libc::sigaction(libc::SIGBUS, &act, &mut OLD_SIGBUS_ACT);
    }'''

PUSH_HOOKS_OLD = r'''                install_training_hook();
                install_selector_hooks();
                let _ = install_weight_hook();
                install_exec_training_hook();
                install_failure_rate_hook();
                install_event_choice_hook();
                install_ui_text_hook();
                install_cutin_trace();
                flog("init", &format!("plugin {} boot, hooks ready", PLUGIN_VERSION));
                rng_consumption_probe::install();
                install_api_sniff_hooks();'''

PUSH_HOOKS_NEW = r'''                // ★ ci_patch13: 仅安装核心育成/matsuri hook，移除未使用的 CutIn/Sniff/RNG/UI 文本
                //   自动安装（防止 Dobby 4KB trampoline 页溢界 mprotect 把邻页 ART LocalReferenceTable 改成只读）
                let _ = install_weight_hook();
                install_selector_hooks();
                install_training_hook();
                install_exec_training_hook();
                install_failure_rate_hook();
                install_event_choice_hook();
                flog("init", &format!("plugin {} boot, core hooks ready", PLUGIN_VERSION));'''

ON_INIT_HOOKS_OLD = r'''        install_training_hook();
        install_selector_hooks();
        let _ = install_weight_hook();
        install_exec_training_hook();
        install_failure_rate_hook();
        install_api_sniff_hooks();
        install_event_choice_hook();
                rng_consumption_probe::install();'''

ON_INIT_HOOKS_NEW = r'''        // ★ ci_patch13: 与 push_loop 一致，仅安装核心 hook（避免 Dobby trampoline 越页踩踏 ART 内存）
        let _ = install_weight_hook();
        install_selector_hooks();
        install_training_hook();
        install_exec_training_hook();
        install_failure_rate_hook();
        install_event_choice_hook();'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch13: {msg}")
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
        s = _replace_once(s, OLD_HANDLER, NEW_HANDLER, "crash_handler_sigaction")
        s = _replace_once(s, PUSH_HOOKS_OLD, PUSH_HOOKS_NEW, "push_core_hooks")
        s = _replace_once(s, ON_INIT_HOOKS_OLD, ON_INIT_HOOKS_NEW, "on_init_core_hooks")
    except RuntimeError as e:
        return _fail(str(e))

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "OLD_SIGSEGV_ACT",
        "libc::SA_SIGINFO",
        "core hooks ready",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
