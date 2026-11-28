#!/usr/bin/env python3
"""CI 侧补丁 12b：对齐 QQ 侧《RUNTIME_FAULTS_LOG.md》的 #8 / #9 / #11。

前置：`ci_patch12.py` 已经把 7 项根因改完（含 SIGSEGV_JMP_BUF、crash handler、
启动安装链、权重归一化）。本补丁只在其产物上再做三件事：

#9（P0 · Dobby 4KB trampoline 页被撑爆）
    ci_patch12 的启动链仍然装 14 个 hook（ui_text / cutin / rng / api_sniff …）。
    实测：trampoline 页塞满后，页尾 `CutIn.CreateModelList` 跨页 `mprotect(0x2000)`
    把邻页 ART `LocalReferenceTable` 由 `rw-` 改成 `r--`，Adjust/Firebase 线程
    一写 local ref 就 `SEGV_ACCERR`。
    → 启动只装 6 个育成/决策核心 hook；辅助 hook 改为按需（端点触发）安装。

#8（P0 · 破坏 ART / Crashlytics 信号链）
    ci_patch12 只做了「缓冲区扩容 + tid 归属」，但 handler 里非恢复路径仍是
    `sys_signal(sig, 0); sys_raise(sig);` —— 把 ART 自己的 handler 直接抹成 SIG_DFL。
    → 改用 `libc::sigaction` 注册，保存旧 `struct sigaction`；非恢复路径
      **恢复旧 action 再 raise**，让 ART/Crashlytics 按原逻辑处理。

#11（P1 · 出道后 matsuri 推荐 Race/Rest 时训练槽被一刀切清零，AI 被架空）
    → matsuri 本轮没有训练候选时，训练槽保持 -1（不接管，原值 10/200 生效），
      而不是写 0 把 5 项训练的相对排序整片抹掉。

纪律：只替换 `ci_patch12.py` 已经落地的文本，锚点唯一，命中数不对整体失败。
"""

import sys

PATH = "src/lib.rs"

# ============================================================
# #9 · 启动只装 6 个核心 hook
# ============================================================

BOOT_OLD = r'''            unsafe {
                ura_log(3, "Push: IL2CPP ready (class resolvable), installing hooks");
                install_training_hook();
                install_selector_hooks();
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
            }'''

BOOT_NEW = r'''            unsafe {
                // ★ v3.30.2（STABILITY #9）：开机只装 6 个育成/决策核心 hook。
                // 原来开机装 14 个（ui_text / cutin / rng / api_sniff …），把 Dobby 的
                // 4KB trampoline 页塞满；页尾 CutIn.CreateModelList 跨页 mprotect(0x2000)
                // 把邻页 ART LocalReferenceTable 由 rw- 改成 r-- →
                // Adjust / Firebase 线程一写 local ref 就 SEGV_ACCERR 闪退。
                // 辅助 hook 全部改为按需（由对应端点触发安装），不再进开机路径。
                ura_log(3, "Push: IL2CPP ready (class resolvable), installing core hooks");
                install_weight_hook();
                install_selector_hooks();
                install_training_hook();
                install_exec_training_hook();
                install_failure_rate_hook();
                install_event_choice_hook();
                flog("init", &format!("plugin {} boot, core hooks ready", PLUGIN_VERSION));
            }'''

# ============================================================
# #8 · sigaction 链式转发
# ============================================================

SIG_FN = r'''// ===== 与 ART / Crashlytics 共存的信号处理（STABILITY #8）=====
//
// 旧实现用 `signal()` 直接把 SIGSEGV/SIGBUS 换成自己的 handler，非恢复路径再
// `signal(sig, 0)` + `raise(sig)` —— 等于把 ART 的 FaultManager、Crashlytics
// 注册的处理器一起抹掉。ART 运行时本来就靠 SIGSEGV 做隐式空指针检查 / 栈保护页 /
// GC 屏障，抹掉之后这些信号会直接杀死游戏进程。
//
// 现在：用 sigaction 注册并**保存旧 action**；非恢复路径「恢复旧 action 再 raise」，
// 让原 handler（ART / Crashlytics）按它自己的 sa_flags 处理，行为与没装插件一致。
static mut OLD_SEGV_ACT: *mut libc::sigaction = std::ptr::null_mut();
static mut OLD_SIGBUS_ACT: *mut libc::sigaction = std::ptr::null_mut();

unsafe fn install_recoverable_sigaction(sig: i32) {
    let old = Box::into_raw(Box::new(std::mem::zeroed::<libc::sigaction>()));
    let mut act: libc::sigaction = std::mem::zeroed();
    act.sa_sigaction = crash_signal_handler as *const () as usize;
    // SA_ONSTACK：用 ALT 栈跑 handler，栈溢出类信号才有机会记日志
    // SA_NODEFER：handler 内部还要 raise 原信号，不能被自己阻塞
    act.sa_flags = libc::SA_ONSTACK | libc::SA_NODEFER;
    libc::sigemptyset(&mut act.sa_mask);
    libc::sigaction(sig, &act as *const libc::sigaction, old);
    if sig == 11 {
        OLD_SEGV_ACT = old;
    } else {
        OLD_SIGBUS_ACT = old;
    }
}

/// 非恢复路径：把旧 handler 装回去再重抛，交还给 ART / Crashlytics
unsafe fn restore_and_reraise(sig: i32) {
    let old: *mut libc::sigaction = if sig == 11 {
        std::ptr::addr_of_mut!(OLD_SEGV_ACT).read()
    } else {
        std::ptr::addr_of_mut!(OLD_SIGBUS_ACT).read()
    };
    if !old.is_null() {
        libc::sigaction(sig, old as *const libc::sigaction, std::ptr::null_mut());
    } else {
        libc::signal(sig, libc::SIG_DFL);
    }
    libc::raise(sig);
}

'''

INIT_OLD = r'''fn init_crash_handler() {
    unsafe {
        let handler = crash_signal_handler as usize;
        sys_signal(11, handler); // SIGSEGV
        sys_signal(6, handler); // SIGABRT
        sys_signal(7, handler); // SIGBUS
        sys_signal(8, handler); // SIGFPE
    }'''

INIT_NEW = r'''fn init_crash_handler() {
    unsafe {
        // ★ v3.30.2（STABILITY #8）：只接管 SIGSEGV / SIGBUS，且保存旧 action。
        // 不再劫持 SIGABRT / SIGFPE —— 那两个由 ART / 系统自己处理更安全。
        install_recoverable_sigaction(11); // SIGSEGV
        install_recoverable_sigaction(7); // SIGBUS
    }'''

CHAIN_OLD = r'''    let owner = SIGSEGV_TID.load(std::sync::atomic::Ordering::Relaxed);
    if owner == 0 || owner != cur_tid() {
        unsafe {
            sys_signal(sig, 0);
            sys_raise(sig);
        }
        return;
    }'''

CHAIN_NEW = r'''    let owner = SIGSEGV_TID.load(std::sync::atomic::Ordering::Relaxed);
    if owner == 0 || owner != cur_tid() {
        // ★ v3.30.2（STABILITY #8）：不是我们的恢复窗口 → 交还给 ART / Crashlytics。
        unsafe {
            restore_and_reraise(sig);
        }
        return;
    }'''

# ============================================================
# #11 · matsuri 没有训练候选时不要清零训练槽
# ============================================================

TRAIN_OLD = r'''    // 训练槽 0..5：训练位必然在候选里，matsuri 没给 = 本轮不可选
    for i in 0..5 {
        MT_W[i].store(0, Relaxed);
    }'''

TRAIN_NEW = r'''    // ★ v3.30.2（STABILITY #11）：matsuri 首选不是训练（Race / Rest）时，
    // 候选里可能根本没有 Train —— 这时把 5 个训练槽全写 0，等于连
    // 「智 279.6 > 速 36.5 > 根 -229.8」这种相对排序也一起丢掉，
    // 游戏只能退回原版 omakase（稽古恒 200），AI 彻底架空。
    // 语义：有训练候选 → 未列出的槽 0（本轮不可选）
    //       无训练候选 → 全部 -1（不接管，原值 / 原版条件加成继续生效）
    let has_train_candidates = decision
        .actions
        .iter()
        .any(|(t, _)| (matsuri_bridge::TAG_TRAIN_BASE..matsuri_bridge::TAG_TRAIN_BASE + 5).contains(t));
    for i in 0..5 {
        MT_W[i].store(if has_train_candidates { 0 } else { -1 }, Relaxed);
    }'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch12b: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # ---- #9 ----
    if s.count(BOOT_OLD) != 1:
        return _fail(f"#9 boot chain anchor count={s.count(BOOT_OLD)}")
    s = s.replace(BOOT_OLD, BOOT_NEW, 1)

    # ---- #8 ----
    anchor = "fn init_crash_handler() {"
    if s.count(anchor) != 1:
        return _fail(f"#8 init_crash_handler anchor count={s.count(anchor)}")
    s = s.replace(anchor, SIG_FN + anchor, 1)
    if s.count(INIT_OLD) != 1:
        return _fail(f"#8 init block anchor count={s.count(INIT_OLD)}")
    s = s.replace(INIT_OLD, INIT_NEW, 1)
    if s.count(CHAIN_OLD) != 1:
        return _fail(f"#8 chain anchor count={s.count(CHAIN_OLD)}")
    s = s.replace(CHAIN_OLD, CHAIN_NEW, 1)

    # ---- #11 ----
    if s.count(TRAIN_OLD) != 1:
        return _fail(f"#11 train slot anchor count={s.count(TRAIN_OLD)}")
    s = s.replace(TRAIN_OLD, TRAIN_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")

    for needle in (
        "install_recoverable_sigaction",
        "restore_and_reraise",
        "OLD_SEGV_ACT",
        "has_train_candidates",
        "core hooks ready",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits}")
        if hits == 0:
            return _fail(f"补丁后仍缺 {needle}")

    # 反例：开机链里不该再出现这四个辅助 hook
    head = s.find("core hooks ready")
    if head > 0:
        window = s[head:head + 1200]
        for bad in ("install_ui_text_hook();", "install_cutin_trace();", "install_api_sniff_hooks();"):
            if bad in window:
                print(f"::warning::开机链附近仍出现 {bad}（检查 BOOT_NEW 是否生效）")
    print("ci_patch12b: all anchors ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())