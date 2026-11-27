#!/usr/bin/env python3
"""CI 侧补丁 6：从游戏读本局身份（马娘 + 6 张支援卡），喂给 matsuri 初始化。

问题：
  matsuri 的 `OnlineSession::new` 需要 `uma` 与 `cards`（6 张支援卡），
  编组门禁 E4 会拒绝不足 6 张卡。之前初始化用默认身份（uma=0、空卡组），
  必然卡在门禁；而 `/debug/training_partners` 读支援卡时把元素当**引用**取，
  结果全是 `ptr: null`。

正确读法（文档 §3.3 + 本仓既有常量）：
  CharaData +160  EquipSupportCardArray
  元素是**值类型内联**，跨度 128 字节（/debug/training_partners 的 element_size 也报 128）
  元素内：+16 position、+20 supportCardId、+24 limitBreakCount

卡号格式（data/ai_config.toml 注释）：
  cards 元素 = card_id * 10 + 突破档（4 = 满凸）
  所以要用 supportCardId * 10 + limitBreakCount 组合

马娘 ID：
  CharaData +36 _cardId（本局育成马娘的 card id，matsuri 的 sim.uma 就用它）

本补丁：
  1. 新增 `mt_read_identity()`：读 uma + cards（值类型内联，不取指针）；
  2. `mt_recompute()` 改为用真实身份初始化，身份变化才重建 session；
  3. 新增 `/api/ai/deck` 输出读到的 uma / cards / 原始元素字节，便于对照游戏核对。

安全：
  读失败只返回空，不影响游戏；init 失败把 uma/cards 原样写进 last_error，
  一眼能看出是读错了还是编组不合规。
"""

import sys

PATH = "src/lib.rs"

IDENTITY_FN = r'''
// ===== 从游戏读本局身份（uma + 支援卡）=====
//
// CharaData +36 = _cardId（育成马娘）；+160 = EquipSupportCardArray。
// EquipSupportCard 是值类型，数组元素内联、跨度 128 字节：
//   +16 position、+20 supportCardId、+24 limitBreakCount
// matsuri 的 cards 元素 = supportCardId * 10 + limitBreakCount。
static MT_ID_KEY: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());

struct MtIdentity {
    uma: u32,
    cards: Vec<u32>,
    /// 每个元素的 (position, supportCardId, limitBreak)，供核对
    raw: Vec<(i32, i32, i32)>,
}

unsafe fn mt_read_identity() -> MtIdentity {
    let mut id = MtIdentity {
        uma: 0,
        cards: Vec::new(),
        raw: Vec::new(),
    };
    if API.is_null() {
        return id;
    }
    let image = get_image();
    if image.is_null() {
        return id;
    }
    let wdm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkDataManager").as_ptr(),
    );
    if wdm_class.is_null() {
        return id;
    }
    let wdm = get_singleton(wdm_class);
    if wdm.is_null() {
        return id;
    }
    let sm_class = find_class(
        image,
        to_cstr("Gallop").as_ptr(),
        to_cstr("WorkSingleModeData").as_ptr(),
    );
    if sm_class.is_null() {
        return id;
    }
    let sm = call_getter_ref(wdm_class, wdm, "get_SingleMode");
    if sm.is_null() {
        return id;
    }
    let chara = call_getter_ref(sm_class, sm, "get_Character");
    if chara.is_null() {
        return id;
    }

    let cid = read_obscured_int_at(chara, 36);
    if cid > 0 {
        id.uma = cid as u32;
    }

    let arr = read_ptr_at(chara, 160);
    if arr.is_null() {
        return id;
    }
    let n = seq_len(arr).min(8);
    let base = arr as *const u8;
    for i in 0..n {
        // 值类型内联：元素地址 = base + 0x20 + i * 128
        let e = base.add(0x20 + i * 128);
        let pos = std::ptr::read_unaligned::<i32>(e.add(0x10) as *const i32);
        let sc = std::ptr::read_unaligned::<i32>(e.add(0x14) as *const i32);
        let lb = std::ptr::read_unaligned::<i32>(e.add(0x18) as *const i32);
        id.raw.push((pos, sc, lb));
        if sc > 0 && (0..=4).contains(&lb) {
            id.cards.push((sc * 10 + lb) as u32);
        }
    }
    id
}

fn mt_deck_json() -> String {
    let id = unsafe { mt_read_identity() };
    let cards = id
        .cards
        .iter()
        .map(|c| c.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let raw = id
        .raw
        .iter()
        .map(|(p, c, l)| format!(r#"{{"pos":{p},"card":{c},"lb":{l}}}"#))
        .collect::<Vec<_>>()
        .join(",");
    format!(
        r#"{{"ok":true,"uma":{},"cards":[{}],"card_n":{},"raw":[{}]}}"#,
        id.uma,
        cards,
        id.cards.len(),
        raw
    )
}
'''

INIT_OLD = r'''    if !matsuri_bridge::ready() {
        // 首次：用默认身份初始化（uma/cards 取内嵌 ai_config，seed 固定）
        if let Err(e) = matsuri_bridge::init(0, &[], 61444) {
            MT_STATE.store(2, Relaxed);
            mt_set_err(format!("init: {e}"));
            return;
        }
    }'''

INIT_NEW = r'''    // 从游戏读本局身份；身份变了才重建 session（重建会丢会话状态）
    let identity = mt_read_identity();
    let key = format!("{}:{:?}", identity.uma, identity.cards);
    let need_init = !matsuri_bridge::ready()
        || MT_ID_KEY
            .lock()
            .map(|g| g.as_str() != key.as_str())
            .unwrap_or(true);
    if need_init {
        match matsuri_bridge::init(identity.uma, &identity.cards, 61444) {
            Ok(()) => {
                if let Ok(mut g) = MT_ID_KEY.lock() {
                    *g = key;
                }
            }
            Err(e) => {
                MT_STATE.store(2, Relaxed);
                mt_set_err(format!(
                    "init(uma={}, cards={:?}): {e}",
                    identity.uma, identity.cards
                ));
                return;
            }
        }
    }'''

ROUTE_OLD = '    } else if path == "/api/ai/init" {'
ROUTE_NEW = '''    } else if path == "/api/ai/deck" {
        mt_deck_json()
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch6: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    anchor = "unsafe fn mt_recompute() {"
    if s.count(anchor) != 1:
        return _fail(f"anchor mt_recompute count={s.count(anchor)}")
    s = s.replace(anchor, IDENTITY_FN.strip() + "\n\n" + anchor, 1)

    if s.count(INIT_OLD) != 1:
        return _fail(f"init block anchor count={s.count(INIT_OLD)}")
    s = s.replace(INIT_OLD, INIT_NEW, 1)

    anchor = '    "/api/ai/matsuri",'
    if s.count(anchor) != 1:
        return _fail(f"anchor ENDPOINTS matsuri count={s.count(anchor)}")
    s = s.replace(anchor, anchor + '\n    "/api/ai/deck",', 1)

    if s.count(ROUTE_OLD) != 1:
        return _fail(f"route anchor init count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "struct MtIdentity",
        "fn mt_read_identity",
        "fn mt_deck_json",
        "mt_read_identity()",
        "/api/ai/deck",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())