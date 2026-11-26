#!/usr/bin/env python3
"""CI 侧补丁 2：omakase_command 权重表只读探测。

为什么单独一个脚本：
  ci_patch.py 已经在改 lib.rs 的三处锚点，再往里塞会让那个文件越来越难核对；
  这里只做一件事——注入 `dump_omakase_dict()` 和 `/api/ai/weights`，
  锚点都用 ci_patch.py 落地后的稳定文本，改坏了只影响这一个端点。

链路（全部实测，来自 /debug/dumpclass）：
  Gallop.MasterDataManager（singleton=true, f=643）
    +3208  masterOmakaseCommand → Gallop.MasterOmakaseCommand
                                    +24  _lazyPrimaryKeyDictionary
                                         Dictionary<Int32, OmakaseCommand>
                                           entry.value → OmakaseCommand
                                                           +16 CommandId    Int32
                                                           +20 SettingId    Int32
                                                           +24 BaseWeight   Int32

同族表（同一持有者，后续按需扩展）：
  +3192 scenario   +3200 state      +3216 basePt     +3224 outingBasePt
  +3232 modify     +3240 modifyPt   +3248 conditionSet +3256 filter
  +3264 filterDetail +3272 defaultPlan +3280 defaultPlanSet
  +3288 stopCondition +3296 stopTrigger +3304 stopperEvent
  +3312 hideSkill  +3320 continueCondition +3328 modifyLegend
  +3336 basePtOnsen +3344 conditionSetOnsen

本脚本只读，不写任何字节：
  1. get_singleton(MasterDataManager) 取实例（不 invoke）
  2. +3208 取 MasterOmakaseCommand
  3. +24 取 Dictionary
  4. 输出 Dictionary 头 0x60 原始字节、buckets / entries 指针、count / freeList / freeCount / version
  5. 输出 entries 前 8 项 × 24 字节的 u64 词与原始 hex（用于反推 entry 跨度与 key/value 偏移）

布局判定纪律（来自复核意见）：
  - 单份 dump 不足以定布局：整数 key 与 hashCode 可能相等，两列都能 100% 命中；
  - 两个命中地址之差是跨度的倍数，不能直接当跨度；
  - 还有多个解释时结论就是「未判定」，不许挑命中最多的候选去写。
"""

import sys

PATH = "src/lib.rs"

WEIGHTS_FN = r'''
// ===== omakase_command 权重表：只读探测 =====
//
// 链路：MasterDataManager(singleton) +3208 → MasterOmakaseCommand +24 → Dictionary
//        entry.value → OmakaseCommand { +16 CommandId, +20 SettingId, +24 BaseWeight }
// 只读。任何写回都必须先有本端点产出的真实行对象地址与旧值。
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
            r#"{{"error":"omakase_command_null","mdm":"{:p}","note":"MasterDataManager+3208 is null"}}"#,
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

    // Dictionary 头：先整段抓原始字节，不预设字段顺序
    let head = std::slice::from_raw_parts(dict as *const u8, 0x60);
    let buckets = read_ptr_at(dict, 0x10);
    let entries = read_ptr_at(dict, 0x18);
    let count = read_int_at(dict, 0x20);
    let free_list = read_int_at(dict, 0x28);
    let free_count = read_int_at(dict, 0x30);
    let version = read_int_at(dict, 0x38);
    let keys = read_ptr_at(dict, 0x48);
    let values = read_ptr_at(dict, 0x50);

    // entries 前 8 项 × 24 字节（int key + 引用 value 的常见跨度，仅作候选）
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
'''

ROUTE_OLD = '    } else if path == "/api/ai/init" {'

ROUTE_NEW = '''    } else if path == "/api/ai/weights" {
        unsafe { dump_omakase_dict() }
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch2: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # ---- 1) 注入只读探测函数 ----
    anchor = "fn selector_hooks_json() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"anchor selector_hooks_json count={s.count(anchor)}")
    s = s.replace(anchor, WEIGHTS_FN.strip() + "\n\n" + anchor, 1)

    # ---- 2) ENDPOINTS 注册 ----
    anchor = '    "/api/ai/hooks",'
    if s.count(anchor) != 1:
        return _fail(f"anchor ENDPOINTS hooks count={s.count(anchor)}")
    s = s.replace(anchor, anchor + '\n    "/api/ai/weights",', 1)

    # ---- 3) 路由 ----
    if s.count(ROUTE_OLD) != 1:
        return _fail(f"route anchor init count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in ("fn dump_omakase_dict", "/api/ai/weights", "masterOmakaseCommand"):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())