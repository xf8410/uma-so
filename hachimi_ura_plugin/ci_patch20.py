#!/usr/bin/env python3
"""CI 侧补丁 20：关掉训练位的「失败率 0%」过滤（omakase_filter F15006..F15010）。

## 数据层（scenario 15 / setting 1501，master.mdb 全量导出核对过）

    F15006..F15010 → command 150101..150105（速 耐 力 根 智）
    condition_set 601..605：condition_type = 18，
        value1 = TrainingCommandId（1..5），value2 = 0（失败率上限 0%）
    is_modifiable = 0（界面锁死），filter_detail.filter_type = 0（训练类）

即：**只要该训练有 1% 失败率，整条 command 在进入权重计算之前就被剔除**。
这解释了为什么 `150105=10000` 这类覆盖永远不生效 —— 过滤发生在加权之前。
对照：F15001..F15005 = 比赛 150110（cond 303..307）；
      F15011..F15013 = 150106/150107/150108（cond 211 = 体力 ≥100 时禁止）。

## 代码层

`Gallop.SingleModeAutoPlay.SingleModeAutoPlayWeightRepositoryBase`
    GetBadEffectConditionList(conditionFilterRecordList)
Festa 子类不覆写，全剧本共用一份 → 只接这一处就够。

## 做法（三个可改点里风险最低的那个）

Hook 之后**原样返回入参**，等价于把 condition_type 18 的失败率上限当成无限大：
一条记录都不删。不改 master.mdb、不碰懒加载字典、随时可回滚。

为什么不调 trampoline：原函数唯一作用就是「按条件删记录」，返回/写回的仍是同一个
集合对象；把入参交回去就等于「一条都不删」。这样即使参数个数与预期不符也不会崩
（我们从不跳进原函数），最坏情况是过滤照旧发生 —— 属于「失败即回到原行为」。

## 观测

`/api/ai/badfx` 给出 installed / addr / hits / last_this / last_list / last_count。
上机后先看这个端点：hits 在涨、count > 0，才说明 hook 真挂在决策路径上。
"""

import sys

PATH = "src/lib.rs"

FN = r'''// ===== 失败率过滤（omakase_filter F15006..F15010 / condition_type 18）=====
//
// 数据层：scenario 15（setting 1501）
//   F15006..F15010 → command 150101..150105，condition_set 601..605
//   condition_type = 18，value1 = TrainingCommandId(1..5)，value2 = 0（失败率上限 0%）
//   → 只要训练有 1% 失败率，这条 command 在**加权之前**就被删出候选；
//     所以 `150105=10000` 这类覆盖不可能命中（过滤先于权重）。
//   is_modifiable = 0（界面锁定）；filter_detail.filter_type = 0（训练类）。
//   对照：F15001..F15005 = 比赛 150110（cond 303..307）；
//         F15011..F15013 = 150106/150107/150108（cond 211 = 体力 ≥100 禁止）。
//
// 代码层：SingleModeAutoPlayWeightRepositoryBase.GetBadEffectConditionList(records)
//   Festa 子类不覆写 → 只接这一处，全剧本生效。
//
// 处理：原样返回入参（一条不删）= 把 type18 的失败率上限当成无限大。
// 为什么不调 trampoline：原函数只是「按条件删记录」，返回的仍是同一个集合；
// 交回入参就等于零删除。这样参数个数与预期不符也不会崩 —— 我们从不跳进原函数，
// 最坏情况只是过滤照旧发生（失败即回到原行为）。
static BADFX_INSTALLED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static BADFX_HITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static BADFX_ADDR: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static BADFX_LAST_THIS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static BADFX_LAST_LIST: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static BADFX_LAST_COUNT: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(-1);
static BADFX_LOG_BUDGET: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(8);

fn badfx_list_count(p: *mut c_void) -> i32 {
    if p.is_null() {
        return -1;
    }
    unsafe {
        let base = p as *const u8;
        let c = std::ptr::read_unaligned::<usize>(base.add(IL2CPP_LIST_COUNT_OFF) as *const usize);
        if c > 4096 {
            -1
        } else {
            c as i32
        }
    }
}

extern "C" fn get_bad_effect_condition_list_hook(
    this: *mut c_void,
    records: *mut c_void,
) -> *mut c_void {
    use std::sync::atomic::Ordering::Relaxed;
    let n = BADFX_HITS.fetch_add(1, Relaxed) + 1;
    let count = badfx_list_count(records);
    BADFX_LAST_THIS.store(this as usize as u64, Relaxed);
    BADFX_LAST_LIST.store(records as usize as u64, Relaxed);
    BADFX_LAST_COUNT.store(count, Relaxed);
    // 只打前 8 次 + 之后每 50 次一条，避免刷爆 ura.log
    if BADFX_LOG_BUDGET.fetch_sub(1, Relaxed) > 0 {
        unsafe {
            ura_log(
                3,
                &format!(
                    "badfx hook #{}: this=0x{:x} records=0x{:x} count={} (passthrough)",
                    n, this as usize, records as usize, count
                ),
            );
        }
    } else if n % 50 == 1 {
        unsafe {
            ura_log(4, &format!("badfx hook #{}: count={}", n, count));
        }
    }
    // ★ 原样返回入参：不删任何 record（失败率过滤失效）
    records
}

unsafe fn install_bad_effect_filter_hook() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    if BADFX_INSTALLED.load(Relaxed) {
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
    // 参数个数按实测签名取 1（conditionFilterRecordList）
    let addr = find_method_entry_exact(class, "GetBadEffectConditionList", 1);
    if addr == 0 {
        return "method_not_found".to_string();
    }
    BADFX_ADDR.store(addr as u64, Relaxed);
    if interceptor_hook(addr, get_bad_effect_condition_list_hook as usize) {
        BADFX_INSTALLED.store(true, Relaxed);
        ura_log(
            3,
            &format!(
                "badfx hook: GetBadEffectConditionList hooked at 0x{:x}",
                addr
            ),
        );
        format!("hooked@0x{:x}", addr)
    } else {
        ura_log(1, "badfx hook: interceptor_hook failed");
        "hook_failed".to_string()
    }
}

fn badfx_json() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    format!(
        r#"{{"installed":{},"addr":"0x{:x}","hits":{},"last_this":"0x{:x}","last_list":"0x{:x}","last_count":{},"dev_filter":"oh_hook_passthrough","policy":"F15006..F15010 condition_type18 失败率上限视为无限大"}}"#,
        BADFX_INSTALLED.load(Relaxed),
        BADFX_ADDR.load(Relaxed),
        BADFX_HITS.load(Relaxed),
        BADFX_LAST_THIS.load(Relaxed),
        BADFX_LAST_LIST.load(Relaxed),
        BADFX_LAST_COUNT.load(Relaxed),
    )
}

'''

CALL_OLD = r'''fn mt_start_worker() {'''

CALL_NEW = r'''fn mt_start_worker() {
    // ★ v3.31.0：装失败率过滤 hook（失败即回到原行为，不阻塞启动）
    // 放在函数体最前面：ci_patch16 已把这里的 use 行改成 {Acquire, Relaxed}，不要再绑那一行
    unsafe {
        let _ = install_bad_effect_filter_hook();
    }'''

ROUTE_OLD = '    } else if path == "/api/ai/matsuri" {'

ROUTE_NEW = r'''    } else if path == "/api/ai/badfx" {
        let install = unsafe { install_bad_effect_filter_hook() };
        format!(r#"{{"ok":true,"install":"{}","state":{}}}"#, install, badfx_json())
    } else if path == "/api/ai/matsuri" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch20: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    anchor = "unsafe fn install_weight_hook() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"install_weight_hook anchor count={s.count(anchor)}")
    s = s.replace(anchor, FN + anchor, 1)

    if s.count(CALL_OLD) != 1:
        return _fail(f"mt_start_worker anchor count={s.count(CALL_OLD)}")
    s = s.replace(CALL_OLD, CALL_NEW, 1)

    if s.count(ROUTE_OLD) != 1:
        return _fail(f"matsuri route anchor count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")

    for needle in (
        "fn get_bad_effect_condition_list_hook",
        "fn install_bad_effect_filter_hook",
        "install_bad_effect_filter_hook();",
        "/api/ai/badfx",
        "GetBadEffectConditionList",
        "BADFX_HITS",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits}")
        if hits == 0:
            return _fail(f"补丁后仍缺 {needle}")

    print("ci_patch20: all anchors ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())