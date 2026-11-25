//! 自动育成计划条目改名（原生 UI，不改布局）
//!
//! 已经用 il2cpp 明文元数据核对过的地址链（2.33.0 实测，字段偏移为十进制转十六进制）：
//!
//! ```text
//! PartsSingleModeAutoPlayPlanDetail
//!   +0x20  _planDetailDefaultOption : PartsSingleModeAutoPlayPlanDetailDefaultOption
//!            +0x20  _planBaseInfo              : PartsSingleModeAutoPlayDefaultPlanBaseInfo
//!                     +0x20  _planNameText        : Gallop.TextCommon   ← 标题
//!                     +0x28  _planDescriptionText : Gallop.TextCommon   ← 描述
//!            +0x28  _customPlanBaseInfo       : PartsSingleModeAutoPlayCustomPlanBaseInfo
//! ```
//!
//! 写入方式：`Gallop.TextCommon.set_text(string)`（101 个方法里已确认存在）。
//!
//! 关于坐标：改文字**不需要** RectTransform。只有插入新控件/挪位置才需要，
//! 而 `UnityEngine.RectTransform` 在 il2cpp 元数据里 dump 不到（引擎类被剥离），
//! 那时只能走 `Component.get_transform()` 调用或实测 Transform 内存偏移。

use std::ffi::c_void;
use std::ptr;

/// 计划条目的标题/描述偏移（相对各自对象基址）
pub const OFF_PLAN_DETAIL_OPTION: usize = 0x20; // PlanDetail → DefaultOption
pub const OFF_OPTION_BASE_INFO: usize = 0x20; // DefaultOption → BaseInfo
pub const OFF_BASE_NAME_TEXT: usize = 0x20; // BaseInfo → _planNameText
pub const OFF_BASE_DESC_TEXT: usize = 0x28; // BaseInfo → _planDescriptionText

/// 从对象 + 偏移读一个引用字段
unsafe fn read_ptr_at(obj: *const c_void, off: usize) -> *mut c_void {
    if obj.is_null() {
        return ptr::null_mut();
    }
    let p = (obj as *const u8).add(off) as *const *mut c_void;
    ptr::read_unaligned(p)
}

/// 解析出标题 / 描述两个 TextCommon 实例
pub unsafe fn resolve_plan_texts(plan_detail: *mut c_void) -> (*mut c_void, *mut c_void) {
    let opt = read_ptr_at(plan_detail, OFF_PLAN_DETAIL_OPTION);
    if opt.is_null() {
        return (ptr::null_mut(), ptr::null_mut());
    }
    let base = read_ptr_at(opt, OFF_OPTION_BASE_INFO);
    if base.is_null() {
        return (ptr::null_mut(), ptr::null_mut());
    }
    (
        read_ptr_at(base, OFF_BASE_NAME_TEXT),
        read_ptr_at(base, OFF_BASE_DESC_TEXT),
    )
}

/// 5 个预设槽 → 我们的档位名
pub fn slot_name(slot: u8) -> (&'static str, &'static str) {
    match slot {
        0 => ("速主 · matsuri", "バランスよく育成（matsuri default 21101）"),
        1 => ("耐主 · matsuri", "スタミナ重視（matsuri stamina_build）"),
        2 => ("智主 · matsuri", "賢さ重視（matsuri wisdom_build 双智）"),
        3 => ("根主 · matsuri", "根性重視（matsuri guts_build 20111）"),
        _ => ("自定义 · matsuri", "自定档位（读取 /sdcard/Download/uma_so/*.toml）"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offsets_are_aligned() {
        // il2cpp 引用字段在 arm64 上 8 字节对齐
        for off in [
            OFF_PLAN_DETAIL_OPTION,
            OFF_OPTION_BASE_INFO,
            OFF_BASE_NAME_TEXT,
            OFF_BASE_DESC_TEXT,
        ] {
            assert_eq!(off % 8, 0, "offset {off:#x} not aligned");
        }
    }

    #[test]
    fn five_slots_named() {
        for s in 0..5u8 {
            let (n, d) = slot_name(s);
            assert!(!n.is_empty() && !d.is_empty());
        }
    }

    #[test]
    fn null_object_is_safe() {
        unsafe {
            let (a, b) = resolve_plan_texts(ptr::null_mut());
            assert!(a.is_null() && b.is_null());
        }
    }
}
