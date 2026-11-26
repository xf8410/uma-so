#!/usr/bin/env python3
"""CI 侧补丁 2：omakase 权重层接入。

两个落点，都只读、不替改数值：

A. `/api/ai/weights` —— 静态表探测
   Gallop.MasterDataManager（singleton=true, f=643）
     +3208 masterOmakaseCommand → MasterOmakaseCommand
                                    +24 _lazyPrimaryKeyDictionary
                                         Dictionary<Int32, OmakaseCommand>
                                           entry.value → OmakaseCommand
                                                           +16 CommandId    Int32
                                                           +20 SettingId    Int32
                                                           +24 BaseWeight   Int32

B. `/api/ai/weightcalls` —— 运行时取值链观测
   hook SingleModeAutoPlayWeightRepositoryBase.GetBaseWeight(this, i32) -> i32
     地址 0x7878098320，入口 `mov w19,w1` / `mov x20,x0`，ret_offsets [152]
   为什么是它：
     - FestaSingleModeAutoPlayWeightRepository 只覆写 6 个方法
       （get_ScenarioId / ConvertToCommandIdTypeByTrainingCommandId /
         CustomConvertToCommandIdTypeByTrainingCommandId /
         get_AutoPlayPauseInfo / get_AutoPlayStopInfo / .ctor），
       取权重的那一批全在 base 类，只有一份实现，全剧本共用；
     - 它是 commandIdType → 基础权重的单一出口，比改 745 行静态表更靠上层，
       不会被 modify / condition / filter 三层覆盖掉。

同族表偏移（MasterDataManager 下）：
  +3192 scenario  +3200 state  +3216 basePt  +3224 outingBasePt
  +3232 modify  +3240 modifyPt  +3248 conditionSet  +3256 filter
  +3264 filterDetail  +3272 defaultPlan  +3280 defaultPlanSet
  +3288 stopCondition  +3296 stopTrigger  +3304 stopperEvent
  +3312 hideSkill  +3320 continueCondition  +3328 modifyLegend
  +3336 basePtOnsen  +3344 conditionSetOnsen

纪律：本脚本不写任何游戏数据。布局/映射未唯一判定前只输出观测值。
"""

import sys

PATH = "src/lib.rs"

PROBE_FN = r'''
// ===== omakase_command 静态表：只读探测 =====
unsafe fn dump_omakase_dict() -> String {
    if API.is_null() {
        return r#"{"error":"api_null"}"#.to_string();
    }
    let image = get_image();
    if image.is_null() {
        return r#"{"error":"image_null"}"#.to_string();
    }
    let mdm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("MasterDataManager").as_ptr(),
    );
    if mdm_class.is_null() {
        return r#"{"error":"no_mdm_class"}"#.to_string();
    }
    let mdm = get_singleton(mdm_class);
    if mdm.is_null() {
        return r#"{"error":"no_mdm_singleton"}"#.to_string();
    }
    let master = read_ptr_at(mdm, 3208);
    if master.is_null() {
        return format!(
            r#"{{"error":"omakase_command_null","mdm":"{:p}"}}"#,
            mdm
        );
    }
    let dict = read_ptr_at(master, 24);
    if dict.is_null() {
        return format!(
            r#"{{"error":"dict_null","mdm":"{:p}","master":"{:p}","note":"lazy dict not built; 先让游戏查一次 omakase_command"}}"#,
            mdm, master
        );
    }

    let head = std::slice::from_raw_parts(dict as *const u8, 0x60);
    let buckets = read_ptr_at(dict, 0x10);
    let entries = read_ptr_at(dict, 0x18);
    let count = read_int_at(dict, 0x20);
    let free_list = read_int_at(dict, 0x28);
    let free_count = read_int_at(dict, 0x30);
    let version = read_int_at(dict, 0x38);
    let keys = read_ptr_at(dict, 0x48);
    let values = read_ptr_at(dict, 0x50);

    let mut ent_hex = String::new();
    let mut ent_words = String::new();
    if !entries.is_null() {
        let n = 8usize;
        let stride = 24usize;
        let src = std::slice::from_raw_parts(entries as *const u8, n * stride);
        ent_hex = hex_encode(src);
        let mut words: Vec<String> = Vec::new();
        for i in 0..(n * stride / 8) {
            let w = std::ptr::read_unaligned::<u64>((entries as *const u8).add(i * 8) as *const u64);
            words.push(format!("{:016x}", w));
        }
        ent_words = words.join(",");
    }

    let buck_hex = if buckets.is_null() {
        String::new()
    } else {
        hex_encode(std::slice::from_raw_parts(buckets as *const u8, 32))
    };

    format!(
        r#"{{"ok":true,"mdm":"{:p}","master":"{:p}","dict":"{:p}","head_hex":"{}","buckets":"{:p}","entries":"{:p}","count":{},"freeList":{},"freeCount":{},"version":{},"keys":"{:p}","values":"{:p}","buckets_hex32":"{}","entries_words_u64":"[{}]","entries_hex_first8x24":"{}"}}"#,
        mdm,
        master,
        dict,
        hex_encode(head),
        buckets,
        entries,
        count,
        free_list,
        free_count,
        version,
        keys,
        values,
        buck_hex,
        ent_words,
        ent_hex
    )
}

// ===== GetBaseWeight 运行时观测 =====
//
// 目标方法：SingleModeAutoPlayWeightRepositoryBase.GetBaseWeight
//   地址 0x7878098320（本次构建）
//   入口 `mov w19, w1`（arg0 = i32）、`mov x20, x0`（this）
//   出口 x0/w0，ret_offsets [152]
// 只记录 arg0 → 返回值，不替改。用于建立
// 「commandIdType → 训练/休息/外出/比赛」映射，再决定怎么喂 matsuri 分。
static WB_HITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static WB_ERR: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static WB_ADDR: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static WB_INSTALLED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// 最近 16 次调用，[arg, ret] 交替；-999 = 空槽
static WB_LOG: [std::sync::atomic::AtomicI64; 32] = [
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
    std::sync::atomic::AtomicI64::new(-999),
];

extern "C" fn get_base_weight_hook(this: *mut c_void, arg0: i32) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        use std::sync::atomic::Ordering::Relaxed;
        let n = WB_HITS.fetch_add(1, Relaxed);
        let trampoline = interceptor_get_trampoline(get_base_weight_hook as usize);
        if trampoline == 0 {
            WB_ERR.fetch_add(1, Relaxed);
            return 0i32;
        }
        type FnType = unsafe extern "C" fn(*mut c_void, i32) -> i32;
        let original: FnType = std::mem::transmute(trampoline);
        let ret = original(this, arg0);
        let slot = (n as usize % 16) * 2;
        WB_LOG[slot].store(arg0 as i64, Relaxed);
        WB_LOG[slot + 1].store(ret as i64, Relaxed);
        ret
    }))
    .unwrap_or(0)
}

unsafe fn install_weight_hook() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    if WB_INSTALLED.load(Relaxed) {
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
    let addr = find_method_addr(class, "GetBaseWeight", 1);
    if addr == 0 {
        return "method_not_found".to_string();
    }
    WB_ADDR.store(addr as u64, Relaxed);
    if interceptor_hook(addr, get_base_weight_hook as usize) {
        WB_INSTALLED.store(true, Relaxed);
        ura_log(
            3,
            &format!("weight hook: GetBaseWeight hooked at 0x{:x}", addr),
        );
        format!("hooked@0x{:x}", addr)
    } else {
        ura_log(1, "weight hook: interceptor_hook failed");
        "hook_failed".to_string()
    }
}

fn weight_calls_json() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    let pairs: Vec<String> = (0..16)
        .map(|i| {
            let a = WB_LOG[i * 2].load(Relaxed);
            let r = WB_LOG[i * 2 + 1].load(Relaxed);
            format!(r#"{{"arg":{a},"ret":{r}}}"#)
        })
        .collect();
    format!(
        r#"{{"ok":true,"installed":{},"addr":"0x{:x}","hits":{},"trampoline_miss":{},"recent":[{}]}}"#,
        WB_INSTALLED.load(Relaxed),
        WB_ADDR.load(Relaxed),
        WB_HITS.load(Relaxed),
        WB_ERR.load(Relaxed),
        pairs.join(",")
    )
}
'''

ROUTE_OLD = '    } else if path == "/api/ai/init" {'

ROUTE_NEW = '''    } else if path == "/api/ai/weights" {
        unsafe { dump_omakase_dict() }
    } else if path == "/api/ai/weightcalls" {
        let install = unsafe { install_weight_hook() };
        format!(r#"{{"install":"{}","calls":{}}}"#, install, weight_calls_json())
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch2: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # ---- 1) 注入探测与观测 ----
    anchor = "fn selector_hooks_json() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"anchor selector_hooks_json count={s.count(anchor)}")
    s = s.replace(anchor, PROBE_FN.strip() + "\n\n" + anchor, 1)

    # ---- 2) ENDPOINTS ----
    anchor = '    "/api/ai/hooks",'
    if s.count(anchor) != 1:
        return _fail(f"anchor ENDPOINTS hooks count={s.count(anchor)}")
    s = s.replace(
        anchor,
        anchor + '\n    "/api/ai/weights",\n    "/api/ai/weightcalls",',
        1,
    )

    # ---- 3) 路由 ----
    if s.count(ROUTE_OLD) != 1:
        return _fail(f"route anchor init count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "fn dump_omakase_dict",
        "fn get_base_weight_hook",
        "fn install_weight_hook",
        "/api/ai/weights",
        "/api/ai/weightcalls",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())