#!/usr/bin/env python3
"""CI 侧补丁 11：给 worker 加门禁，并给 chara 加类指针硬校验。

上一版崩溃复盘（2026-11-27）：
  ci_patch9 的 worker 启动条件只有"等 5 秒"，等于假设 5 秒后已经进了育成。
  实际还停在标题画面 → `get_Character` 拿到的对象不可用 → 后台线程段错误；
  而 worker 每 3 秒重试，于是变成反复闪退（用户描述"3 分钟闪退"）。

两道门禁：

1. **worker 活跃门**（改 ci_patch9 的循环）
   `GetBaseWeight` 被调用本身就说明"正在做自动育成决策、游戏对象有效"。
   所以让 hook 每次命中都写时间戳，worker 只有在
   「最近 10 秒内有过命中」时才动游戏内存；标题画面 / 结算界面 hook 不命中，
   worker 自然不跑。

2. **chara 类指针硬校验**（加在 build_matsuri_frame）
   非育成态 `get_Character` 可能返回非空但已释放/被复用的对象，
   `_totalTurnNum` 读到垃圾非零值会一路往下读 `Character` 字段然后崩 ——
   这正是交接文档 §6.2 那条 `S:after_read_speed` 的成因。
   所以读任何 Character 字段之前，先确认 klass 名是 `WorkSingleModeCharaData`。

范围：只动闭环相关代码，不碰 weightcalls / override / deckraw 等观测端点。
"""

import sys

PATH = "src/lib.rs"

LAST_HIT = r'''
/// 最近一次 GetBaseWeight 命中的时间戳（毫秒）；0 = 从未命中
static MT_LAST_HIT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
'''

WORKER_OLD = r'''            std::thread::sleep(std::time::Duration::from_secs(5));
            loop {
                let r = std::panic::catch_unwind(|| unsafe { mt_recompute() });'''

WORKER_NEW = r'''            std::thread::sleep(std::time::Duration::from_secs(5));
            loop {
                // 门禁：只有最近 10 秒内 GetBaseWeight 被调用过（说明正在决策、
                // 游戏对象有效）才动游戏内存。标题画面/结算界面 hook 不命中，
                // 这里直接跳过 —— 上一版就是缺这道门禁，在标题画面读 Character 崩的。
                let last = MT_LAST_HIT.load(std::sync::atomic::Ordering::Relaxed);
                let idle = last == 0 || wb_now_ms().saturating_sub(last) > 10_000;
                if idle {
                    std::thread::sleep(std::time::Duration::from_millis(1000));
                    continue;
                }
                let r = std::panic::catch_unwind(|| unsafe { mt_recompute() });'''

HOOK_OLD = r'''unsafe fn mt_weight_for_arg(arg: i32) -> Option<i32> {
    use std::sync::atomic::Ordering::Relaxed;
'''

HOOK_NEW = r'''unsafe fn mt_weight_for_arg(arg: i32) -> Option<i32> {
    use std::sync::atomic::Ordering::Relaxed;
    // 活跃信号：worker 靠这个判断"现在是不是在决策中"，别在标题画面乱读对象
    MT_LAST_HIT.store(wb_now_ms(), Relaxed);
'''

CHARA_OLD = r'''    let chara = call_getter_ref(sm_class, sm, "get_Character");
    if chara.is_null() {
        return r#"{"error":"no_chara"}"#.to_string();
    }

    // ---- baseGame：马娘与五维 ----'''

CHARA_NEW = r'''    let chara = call_getter_ref(sm_class, sm, "get_Character");
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
    }

    // ---- baseGame：马娘与五维 ----'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch11: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # 1) 活跃时间戳静态
    anchor = "static MT_WORKER_ON: std::sync::atomic::AtomicBool ="
    if s.count(anchor) != 1:
        return _fail(f"anchor MT_WORKER_ON count={s.count(anchor)}")
    s = s.replace(anchor, LAST_HIT.strip() + "\n\n" + anchor, 1)

    # 2) worker 门禁
    if s.count(WORKER_OLD) != 1:
        return _fail(f"worker loop anchor count={s.count(WORKER_OLD)}")
    s = s.replace(WORKER_OLD, WORKER_NEW, 1)

    # 3) hook 写活跃时间戳
    if s.count(HOOK_OLD) != 1:
        return _fail(f"mt_weight_for_arg anchor count={s.count(HOOK_OLD)}")
    s = s.replace(HOOK_OLD, HOOK_NEW, 1)

    # 4) chara 类校验
    if s.count(CHARA_OLD) != 1:
        return _fail(f"chara block anchor count={s.count(CHARA_OLD)}")
    s = s.replace(CHARA_OLD, CHARA_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "MT_LAST_HIT",
        "idle",
        "chara_class_mismatch",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())