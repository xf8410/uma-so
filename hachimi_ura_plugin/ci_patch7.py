#!/usr/bin/env python3
"""CI 侧补丁 7：导出支援卡数组的原始字节，用于定死元素布局。

背景：
  ci_patch6 按"值类型内联 128 字节"读 `CharaData +160`，结果明显是错的：
    raw[0] = {pos: 369433728, card: 117, lb: 369433568}
  pos 应该是 0..5 的小整数，369433728 看着像指针；
  而 `/debug/training_partners` 同时报出相互矛盾的两条信息：
    element_is_value_type: false   （元素是引用）
    element_size: 128              （对象 128 字节）
  并且它按引用取元素时 ptr 全是 null。

所以这版不再猜布局，只把原始内存导出来：
  1. CharaData 地址、`+160` 取到的数组对象地址、`+0x18` 读到的长度
  2. 数组对象头部 0x40 字节
  3. `+0x20` 起 320 字节（覆盖 2~3 个候选元素，够看出跨度）
  4. 两种解释并排给出：
       · 按值类型内联（跨度 128）读前 6 组三元组
       · 按引用（元素 = 8 字节指针）读前 6 组三元组
  5. 若按引用读到的指针非空，顺带导出该对象的前 64 字节

有了这份数据就能唯一确定：元素跨度、元素内字段偏移、元素是值还是引用。
在此之前不再改读取逻辑（避免又一次"看起来读到了其实是垃圾"）。
"""

import sys

PATH = "src/lib.rs"

RAW_FN = r'''
// ===== 支援卡数组原始字节导出（定死布局用，只读）=====
fn mt_deck_raw_json() -> String {
    unsafe {
        if API.is_null() {
            return r#"{"ok":false,"error":"api_null"}"#.to_string();
        }
        let image = get_image();
        if image.is_null() {
            return r#"{"ok":false,"error":"image_null"}"#.to_string();
        }
        let wdm_class = find_class(
            image,
            to_cstr("Gallop").as_ptr(),
            to_cstr("WorkDataManager").as_ptr(),
        );
        if wdm_class.is_null() {
            return r#"{"ok":false,"error":"no_wdm_class"}"#.to_string();
        }
        let wdm = get_singleton(wdm_class);
        if wdm.is_null() {
            return r#"{"ok":false,"error":"no_wdm_singleton"}"#.to_string();
        }
        let sm_class = find_class(
            image,
            to_cstr("Gallop").as_ptr(),
            to_cstr("WorkSingleModeData").as_ptr(),
        );
        if sm_class.is_null() {
            return r#"{"ok":false,"error":"no_sm_class"}"#.to_string();
        }
        let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
        if sm.is_null() {
            return r#"{"ok":false,"error":"no_single_mode"}"#.to_string();
        }
        let chara = call_getter_ref(sm_class, sm, "get_Character");
        if chara.is_null() {
            return r#"{"ok":false,"error":"no_chara"}"#.to_string();
        }

        let arr = read_ptr_at(chara, 160);
        if arr.is_null() {
            return format!(
                r#"{{"ok":true,"chara":"{:p}","arr":null,"note":"CharaData+160 is null"}}"#,
                chara
            );
        }

        let len = seq_len(arr);
        let head = hex_encode(std::slice::from_raw_parts(arr as *const u8, 0x40));
        let body_len = 320usize;
        let body = hex_encode(std::slice::from_raw_parts(
            (arr as *const u8).add(0x20),
            body_len,
        ));

        // 解释 A：值类型内联，跨度 128，字段在元素内 +16/+20/+24
        let mut as_value: Vec<String> = Vec::new();
        for i in 0..len.min(6) {
            let e = (arr as *const u8).add(0x20 + i * 128);
            let p = std::ptr::read_unaligned::<i32>(e.add(0x10) as *const i32);
            let c = std::ptr::read_unaligned::<i32>(e.add(0x14) as *const i32);
            let l = std::ptr::read_unaligned::<i32>(e.add(0x18) as *const i32);
            as_value.push(format!(r#"{{"pos":{p},"card":{c},"lb":{l}}}"#));
        }

        // 解释 B：元素是 8 字节指针，指向对象；对象内 +16/+20/+24
        let mut as_ref: Vec<String> = Vec::new();
        let mut obj_hex = String::new();
        for i in 0..len.min(6) {
            let p = seq_item(arr, i);
            if p.is_null() {
                as_ref.push(r#"{"ptr":null}"#.to_string());
                continue;
            }
            let pos = std::ptr::read_unaligned::<i32>((p as *const u8).add(0x10) as *const i32);
            let card = std::ptr::read_unaligned::<i32>((p as *const u8).add(0x14) as *const i32);
            let lb = std::ptr::read_unaligned::<i32>((p as *const u8).add(0x18) as *const i32);
            as_ref.push(format!(
                r#"{{"ptr":"{:p}","pos":{pos},"card":{card},"lb":{lb}}}"#,
                p
            ));
            if i == 0 {
                obj_hex = hex_encode(std::slice::from_raw_parts(p as *const u8, 64));
            }
        }

        format!(
            r#"{{"ok":true,"chara":"{:p}","arr":"{:p}","len":{},"head_hex":"{}","body_hex_320":"{}","as_value_inline128":[{}],"as_ref_ptr":[{}],"obj0_hex64":"{}"}}"#,
            chara,
            arr,
            len,
            head,
            body,
            as_value.join(","),
            as_ref.join(","),
            obj_hex
        )
    }
}
'''

ROUTE_OLD = '    } else if path == "/api/ai/init" {'
ROUTE_NEW = '''    } else if path == "/api/ai/deckraw" {
        mt_deck_raw_json()
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch7: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    anchor = "unsafe fn mt_recompute() {"
    if s.count(anchor) != 1:
        return _fail(f"anchor mt_recompute count={s.count(anchor)}")
    s = s.replace(anchor, RAW_FN.strip() + "\n\n" + anchor, 1)

    anchor = '    "/api/ai/deck",'
    if s.count(anchor) != 1:
        return _fail(f"anchor ENDPOINTS deck count={s.count(anchor)}")
    s = s.replace(anchor, anchor + '\n    "/api/ai/deckraw",', 1)

    if s.count(ROUTE_OLD) != 1:
        return _fail(f"route anchor init count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in ("fn mt_deck_raw_json", "as_value_inline128", "as_ref_ptr", "/api/ai/deckraw"):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())