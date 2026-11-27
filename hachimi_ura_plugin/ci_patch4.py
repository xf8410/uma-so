#!/usr/bin/env python3
"""CI 侧补丁 4：修正方法入口寻址（hook 装错地址的根因）。

问题实录（2026-11-27，游戏 2.33.2）：
  /api/ai/weightcalls 显示 installed=true，但 hits 一直 0。
  对比两个地址：
    /il2cpp/disassemble?class=SingleModeAutoPlayWeightRepositoryBase&method=GetBaseWeight
      → 0x7879dab2dc   开头 `adrp x17 / add x17 / br x16`（转发桩）
        桩后 0x7879dab2e8 才是真入口：`ldrb w8 / mov w19,w1 / mov x20,x0`
    hook 实际装的
      → 0x78782012dc   开头 `bl ...` —— 落在别的函数中段，永远不可能命中

根因：
  `find_method_addr()` 走 `il2cpp_method_get_pointer(mi)`，拿到的不是 methodPointer；
  而 `il2cpp_disassemble()` 直接读 `MethodInfo + 0` 的 methodPointer 是对的。
  同一份 lib.rs 里两套取法，结果不一致。

本补丁：
  1. 新增 `find_method_entry()`：与 il2cpp_disassemble 同源，读 MethodInfo +0；
  2. `install_weight_hook` 改用它；
  3. 新增 `/api/ai/entry` 同时返回两种取法的地址，便于现场对比
     （正确时两者应相等；不等说明还有个 API 在骗人）。

注意：本补丁只改 weight hook 的取址，不动其它 hook（training / ui_text 等），
避免把别处可能"将错就错"跑通的行为一起改坏。
"""

import sys

PATH = "src/lib.rs"

ENTRY_FN = r'''
// ===== 方法入口寻址（与 /il2cpp/disassemble 同源）=====
//
// 为什么不用 find_method_addr：
//   它调 il2cpp_method_get_pointer(mi)，实测返回的不是 methodPointer
//   （2.33.2 上 GetBaseWeight 给 0x78782012dc，落在别的函数中段，hook 永不命中）；
//   而 il2cpp_disassemble 读 MethodInfo +0 拿到 0x7879dab2dc（转发桩）是对的。
//   两者必须同源，否则 hook 装得上但不生效。
unsafe fn find_method_entry(class: *mut c_void, method_name: &str, _param_count: i32) -> usize {
    let get_methods_fn: Option<
        unsafe extern "C" fn(*mut c_void, *mut *mut c_void) -> *const c_void,
    > = {
        let p = resolve_il2cpp_symbol("il2cpp_class_get_methods");
        if p.is_null() {
            None
        } else {
            Some(std::mem::transmute(p))
        }
    };
    let method_get_name_fn: Option<unsafe extern "C" fn(*const c_void) -> *const c_char> = {
        let p = resolve_il2cpp_symbol("il2cpp_method_get_name");
        if p.is_null() {
            None
        } else {
            Some(std::mem::transmute(p))
        }
    };
    let (Some(get_methods), Some(get_name)) = (get_methods_fn, method_get_name_fn) else {
        return 0;
    };

    let mut iter: *mut c_void = std::ptr::null_mut();
    loop {
        let mi = get_methods(class, &mut iter);
        if mi.is_null() {
            break;
        }
        let name_ptr = get_name(mi);
        if name_ptr.is_null() {
            continue;
        }
        let name = CStr::from_ptr(name_ptr).to_string_lossy();
        if name == method_name {
            // MethodInfo 布局：+0 = methodPointer（64 位）
            let ptr =
                std::ptr::read_unaligned::<*const c_void>(mi as *const *const c_void);
            if !ptr.is_null() {
                ura_log(
                    3,
                    &format!("find_method_entry: {} -> 0x{:x}", method_name, ptr as usize),
                );
                return ptr as usize;
            }
        }
    }
    0
}

fn method_entry_probe_json() -> String {
    unsafe {
        if API.is_null() {
            return r#"{"ok":false,"error":"api_null"}"#.to_string();
        }
        let image = get_image();
        if image.is_null() {
            return r#"{"ok":false,"error":"image_null"}"#.to_string();
        }
        let class = find_class_by_short_name(image, "SingleModeAutoPlayWeightRepositoryBase");
        if class.is_null() {
            return r#"{"ok":false,"error":"class_not_found"}"#.to_string();
        }
        let legacy = find_method_addr(class, "GetBaseWeight", 1);
        let entry = find_method_entry(class, "GetBaseWeight", 1);
        format!(
            r#"{{"ok":true,"legacy_get_pointer":"0x{:x}","method_pointer":"0x{:x}","match":{}}}"#,
            legacy,
            entry,
            if legacy == entry { "true" } else { "false" }
        )
    }
}
'''

ADDR_OLD = '    let addr = find_method_addr(class, "GetBaseWeight", 1);'
ADDR_NEW = '    let addr = find_method_entry(class, "GetBaseWeight", 1);'

ROUTE_OLD = '    } else if path == "/api/ai/init" {'
ROUTE_NEW = '''    } else if path == "/api/ai/entry" {
        method_entry_probe_json()
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch4: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    anchor = "unsafe fn install_weight_hook() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"anchor install_weight_hook count={s.count(anchor)}")
    s = s.replace(anchor, ENTRY_FN.strip() + "\n\n" + anchor, 1)

    if s.count(ADDR_OLD) != 1:
        return _fail(f"anchor weight addr count={s.count(ADDR_OLD)}")
    s = s.replace(ADDR_OLD, ADDR_NEW, 1)

    anchor = '    "/api/ai/override",'
    if s.count(anchor) != 1:
        return _fail(f"anchor ENDPOINTS override count={s.count(anchor)}")
    s = s.replace(anchor, anchor + '\n    "/api/ai/entry",', 1)

    if s.count(ROUTE_OLD) != 1:
        return _fail(f"route anchor init count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "fn find_method_entry",
        "find_method_entry(class, \"GetBaseWeight\"",
        "fn method_entry_probe_json",
        "/api/ai/entry",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())