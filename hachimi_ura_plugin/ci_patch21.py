#!/usr/bin/env python3
"""CI 侧补丁 21：把 PC 版（delightedMaster/uma-so-pc）已验证的两处决策能力搬回安卓。

对照 PC 版 `src/hooks.rs`：
  · Hook 1b  SingleModeAutoPlayWeightRepositoryBase.GetFilterDataList(this) -> List
      —— 调原函数拿到 soft filter 列表后，把 List._count 写 0：
         「满体力禁外出 / 属性上限禁训练 / 强制自选赛过滤」这些软规则一次性失效；
         硬件与 UI 合法性（IsEnable）仍由游戏判定，不受影响。
  · 权重尺度  MT_BEST_WEIGHT = 100_000 / MT_SUPPRESS_WEIGHT = -50_000
      —— 注释写得很直白：要 comfortably exceed 原版 +1200 / -999 的条件加成。

安卓这边原来的首选权重是 1000（ci_patch17），压不过 `omakase_base_pt` 的
cond303 +1200。本补丁把它抬到 100_000，并补上 GetFilterDataList 这一道。

与 ci_patch20 的关系：20 是「按条件删记录」那一层的兜底（GetBadEffectConditionList
原样返回），21 是「soft 规则列表」这一层（更靠前）。两者叠加不打架：
都是让过滤不生效，21 覆盖 20 没覆盖到的规则。

未搬（需要上机 offset 校验，先不动）：
  · AutoPlayActionInfoFilter.IsUsableTraining 的「只对 AI 选中那一项豁免失败率」
    （PC 靠 AutoPlayTrainingRecord.TrainingFailureRate +40 / CommandId +32 动态解析；
     安卓侧暂时由 20 兜底，等有稳定偏移再做精细版）
  · MT_SUPPRESS_WEIGHT = -50_000 的「非首选非训练槽压制」
    （会在体力见底时强推训练，必须与 IsUsableTraining / 体力过滤一起上，暂缓）
  · state_hash / 蓝因子推断 / luck / post-T77 settle / candidate_scores 索引映射
    （属于帧构造与评估侧，见 PC 的 frame_builder.rs / matsuri_bridge.rs）
"""

import sys

PATH = "src/lib.rs"

BEST_OLD_1 = "            next_w[b] = if unified_10slot { 1000 } else { 170 };"
BEST_NEW_1 = "            next_w[b] = if unified_10slot { 100_000 } else { 170 };"

BEST_OLD_2 = "            next_w[b] = 1000;"
BEST_NEW_2 = "            next_w[b] = 100_000;"

FN = r'''// ===== 清空原版 omakase soft filter（GetFilterDataList，PC 版同源做法）=====
//
// 原版 soft 规则：满体力禁外出、属性上限禁训练、强制自选赛过滤 等，
// 都由这个 List 里的 filter 记录表达，在本轮决策前算好。
// 直接把返回 List 的 `_count` 写 0 = 本轮没有任何 soft filter；
// 硬件/UI 合法性（IsEnable）另由游戏判定，不受影响。
//
// 与 ci_patch20（GetBadEffectConditionList 原样返回）的区别：
//   20 是「按条件删记录」这一层，21 是「soft 规则列表」这一层，更靠前、覆盖更广。
static FILTERDL_INSTALLED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
static FILTERDL_HITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static FILTERDL_CLEARED: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static FILTERDL_ADDR: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

extern "C" fn get_filter_data_list_hook(
    this: *mut c_void,
    method_info: *const c_void,
) -> *mut c_void {
    use std::sync::atomic::Ordering::Relaxed;
    let trampoline = interceptor_get_trampoline(get_filter_data_list_hook as *const () as usize);
    if trampoline == 0 {
        return std::ptr::null_mut();
    }
    type FnType = unsafe extern "C" fn(*mut c_void, *const c_void) -> *mut c_void;
    let original: FnType = std::mem::transmute(trampoline);
    let list = unsafe { original(this, method_info) };
    let hit = FILTERDL_HITS.fetch_add(1, Relaxed) + 1;
    if list.is_null() {
        return list;
    }
    let cnt = unsafe { read_int_at(list as *const c_void, IL2CPP_LIST_COUNT_OFF as i32) };
    if hit <= 5 {
        unsafe {
            ura_log(
                3,
                &format!(
                    "filter hook #{}: list=0x{:x} count={}",
                    hit, list as usize, cnt
                ),
            );
        }
    }
    if (1..=256).contains(&cnt) {
        unsafe {
            let size_ptr = (list as usize + IL2CPP_LIST_COUNT_OFF) as *mut i32;
            std::ptr::write(size_ptr, 0);
        }
        FILTERDL_CLEARED.fetch_add(1, Relaxed);
    }
    list
}

unsafe fn install_filter_data_list_hook() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    if FILTERDL_INSTALLED.load(Relaxed) {
        return "already".to_string();
    }
    if API.is_null() {
        return "api_null".to_string();
    }
    let image = get_image();
    if image.is_null() {
        return "image_null".to_string();
    }
    let class = find_class_by_short_name(image, "SingleModeAutoPlayWeightRepositoryBase");
    if class.is_null() {
        return "class_not_found".to_string();
    }
    // GetFilterDataList(this) —— 0 参
    let addr = find_method_entry_exact(class, "GetFilterDataList", 0);
    if addr == 0 {
        return "method_not_found".to_string();
    }
    FILTERDL_ADDR.store(addr as u64, Relaxed);
    if interceptor_hook(addr, get_filter_data_list_hook as *const () as usize) {
        FILTERDL_INSTALLED.store(true, Relaxed);
        ura_log(
            3,
            &format!("filter hook: GetFilterDataList hooked at 0x{:x}", addr),
        );
        format!("hooked@0x{:x}", addr)
    } else {
        ura_log(1, "filter hook: interceptor_hook failed");
        "hook_failed".to_string()
    }
}

fn filterdl_json() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    format!(
        r#"{{"installed":{},"addr":"0x{:x}","hits":{},"cleared":{},"policy":"soft filter list _count=0"}}"#,
        FILTERDL_INSTALLED.load(Relaxed),
        FILTERDL_ADDR.load(Relaxed),
        FILTERDL_HITS.load(Relaxed),
        FILTERDL_CLEARED.load(Relaxed),
    )
}

'''

CALL_OLD = r'''        let _ = install_bad_effect_filter_hook();'''

CALL_NEW = r'''        let _ = install_bad_effect_filter_hook();
        // ★ v3.32.0：同时清 soft filter 列表（PC 版同源做法）
        let _ = install_filter_data_list_hook();'''

ROUTE_OLD = '    } else if path == "/api/ai/badfx" {'

ROUTE_NEW = r'''    } else if path == "/api/ai/filter" {
        let install = unsafe { install_filter_data_list_hook() };
        format!(r#"{{"ok":true,"install":"{}","state":{}}}"#, install, filterdl_json())
    } else if path == "/api/ai/badfx" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch21: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    if s.count(BEST_OLD_1) != 1:
        return _fail(f"best weight (train) anchor count={s.count(BEST_OLD_1)}")
    s = s.replace(BEST_OLD_1, BEST_NEW_1, 1)

    if s.count(BEST_OLD_2) != 1:
        return _fail(f"best weight (non-train) anchor count={s.count(BEST_OLD_2)}")
    s = s.replace(BEST_OLD_2, BEST_NEW_2, 1)

    anchor = "unsafe fn install_weight_hook() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"install_weight_hook anchor count={s.count(anchor)}")
    s = s.replace(anchor, FN + anchor, 1)

    if s.count(CALL_OLD) != 1:
        return _fail(f"worker install anchor count={s.count(CALL_OLD)}")
    s = s.replace(CALL_OLD, CALL_NEW, 1)

    if s.count(ROUTE_OLD) != 1:
        return _fail(f"badfx route anchor count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")

    for needle in (
        "fn get_filter_data_list_hook",
        "fn install_filter_data_list_hook",
        "install_filter_data_list_hook();",
        "/api/ai/filter",
        "GetFilterDataList",
        "next_w[b] = 100_000",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits}")
        if hits == 0:
            return _fail(f"补丁后仍缺 {needle}")

    print("ci_patch21: all anchors ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())