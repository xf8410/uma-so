#!/usr/bin/env python3
"""CI 侧补丁 9：matsuri 重活搬到后台线程，hook 只读缓存。

现象：
  用户开自动后闪退，但 ura.log 没有崩溃记录、tombstone/ANR 都是旧的。
  `matsuri_data/` 目录在闪退那一刻被创建，6 个文件（7.3 MB，含 5.7 MB 的
  text_data_dict.json）都写完 —— 说明 `ensure_assets` 成功，崩在紧接着的
  `Data::init`（解析这 7.3 MB）+ 建会话。

  这一段原先跑在 `GetBaseWeight` 的 hook 里，也就是**游戏的渲染线程**。
  在渲染线程上解析 7 MB JSON，轻则掉帧卡死，重则内存峰值被系统杀掉；
  而此时 `hits` 恰好在涨，`state` 又没到 1。

改法：
  1. hook 内只读 `MT_W` 静态缓存，命中就返回，**不再触发任何计算**；
  2. 新增 `matsuri-worker` 后台线程：启动后等 5 秒，然后每 3 秒一次
     identity → init → build_frame → decide → 写 `MT_W`；
  3. hook 安装成功时拉起该线程（只拉一次）。

安全：
  - worker 每轮都在 `catch_unwind` 内，panic 不会带崩线程；
  - `MT_STATE` 三态（0 未就绪 / 1 就绪 / 2 失败）由 worker 独占写，hook 只读；
  - 未就绪时 hook 原样返回 trampoline 结果，行为等同没装闭环；
  - 线程栈给 8 MB，避免 serde 深层递归压栈。
"""

import sys

PATH = "src/lib.rs"

WORKER = r'''
// ===== matsuri 后台计算线程 =====
//
// 为什么不在 hook 里算：hook 跑在游戏渲染线程，Data::init 要解析 7.3 MB 内嵌数据，
// 实测会在那一瞬间闪退（matsuri_data 已落盘但 state 没到 1）。
static MT_WORKER_ON: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn mt_start_worker() {
    use std::sync::atomic::Ordering::Relaxed;
    if MT_WORKER_ON.swap(true, Relaxed) {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name("matsuri-worker".into())
        .stack_size(8 * 1024 * 1024)
        .spawn(|| {
            // 首轮让游戏先稳定，避开启动/加载的抖动期
            std::thread::sleep(std::time::Duration::from_secs(5));
            loop {
                let r = std::panic::catch_unwind(|| unsafe { mt_recompute() });
                if r.is_err() {
                    if let Ok(mut g) = MT_ERR.lock() {
                        *g = format!("{} | worker panic", g);
                    }
                    MT_STATE.store(2, std::sync::atomic::Ordering::Relaxed);
                }
                std::thread::sleep(std::time::Duration::from_millis(3000));
            }
        });
    if spawned.is_err() {
        MT_WORKER_ON.store(false, Relaxed);
    }
}
'''

THROTTLE_OLD = r'''    // 节流：3 秒最多算一次（decide 耗时 100~800ms，不能每次 hook 都跑）
    let now = wb_now_ms();
    let last = MT_AT.load(Relaxed);
    if last == 0 || now.saturating_sub(last) >= 3000 {
        MT_AT.store(now, Relaxed);
        mt_recompute();
    }
'''

THROTTLE_NEW = r'''    // 计算已移到 matsuri-worker 线程，这里只读缓存：
    // hook 跑在渲染线程，任何重活（Data::init 解析 7.3MB、建会话）都会闪退。
'''

INSTALL_OLD = r'''        WB_INSTALLED.store(true, Relaxed);
        ura_log(
            3,
            &format!("weight hook: GetBaseWeight hooked at 0x{:x}", addr),
        );'''

INSTALL_NEW = r'''        WB_INSTALLED.store(true, Relaxed);
        ura_log(
            3,
            &format!("weight hook: GetBaseWeight hooked at 0x{:x}", addr),
        );
        // 拉起后台计算线程（只拉一次）
        mt_start_worker();'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch9: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # 1) 后台线程（放在 mt_recompute 之前）
    anchor = "unsafe fn mt_recompute() {"
    if s.count(anchor) != 1:
        return _fail(f"anchor mt_recompute count={s.count(anchor)}")
    s = s.replace(anchor, WORKER.strip() + "\n\n" + anchor, 1)

    # 2) hook 侧去掉触发计算
    if s.count(THROTTLE_OLD) != 1:
        return _fail(f"throttle block anchor count={s.count(THROTTLE_OLD)}")
    s = s.replace(THROTTLE_OLD, THROTTLE_NEW, 1)

    # 3) 安装 hook 时拉起线程
    if s.count(INSTALL_OLD) != 1:
        return _fail(f"install anchor count={s.count(INSTALL_OLD)}")
    s = s.replace(INSTALL_OLD, INSTALL_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "fn mt_start_worker",
        "matsuri-worker",
        "mt_start_worker();",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    # hook 里不能再出现 mt_recompute()
    tail_hits = sum(
        1
        for line in s.splitlines()
        if "mt_recompute();" in line and "mt_start_worker" not in line
    )
    print(f"  mt_recompute() 直接调用点: {tail_hits}（应为 1，仅 worker 内）")
    return 0


if __name__ == "__main__":
    sys.exit(main())