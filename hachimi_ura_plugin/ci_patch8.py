#!/usr/bin/env python3
"""CI 侧补丁 8：按 ObscuredInt 正确读支援卡（修正 ci_patch6）。

实测证据（2026-11-27，游戏 2.33.2，/api/ai/deckraw）：

  as_ref_ptr[0] = { ptr: 0x7748eb9d20, pos: 444444, card: 444445, lb: 1 }

  444444 = 0x6C81C 是 ObscuredInt 的 currentCryptoKey，不是真实值。
  同一元素的 obj0_hex64 逐字段解出：

    +0x10  1c c8 06 00  position    key     = 444444
    +0x14  1d c8 06 00  position    hidden  = 444445 → ^key = 1
    +0x18  01 00 00 00  inited
    +0x24  1c c8 06 00  cardId      key     = 444444
    +0x28  6e be 06 00  cardId      hidden  = 442990 → ^key = 30322
    +0x38  1c c8 06 00  limitBreak  key     = 444444
    +0x3C  18 c8 06 00  limitBreak  hidden  = 444440 → ^key = 4

  六个元素的 position 依次解出 1,2,3,4,5,6，说明这个解释是对的。

结论（两处错都在 ci_patch6）：
  1. 元素是**引用**（seq_item 取指针），不是值类型内联 128 字节；
  2. 字段是 **ObscuredInt**，间距 **20 字节**：+16 position、+36 cardId、+56 limitBreak；
     插件里 `IL2CPP_SUPPORT_CARD_ID_OFF = 0x14 / LIMIT_OFF = 0x18` 那组常量是紧凑布局，
     对这组字段不适用，用它会读到 key/hidden 的低位。

本补丁把读取改成：
  seq_item(arr, i) → read_obscured_int_at(p, 16/36/56) → card*10+lb
卡号格式仍按 data/ai_config.toml：`supportCardId * 10 + limitBreakCount`。
"""

import sys

PATH = "src/lib.rs"

OLD = r'''    let arr = read_ptr_at(chara, 160);
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
    id'''

NEW = r'''    let arr = read_ptr_at(chara, 160);
    if arr.is_null() {
        return id;
    }
    // 元素是引用：seq_item 取指针；对象内三个字段都是 ObscuredInt，间距 20 字节
    //   +16 position、+36 supportCardId、+56 limitBreakCount
    // （实测：6 个元素 position 依次解出 1..6，cardId/limitBreak 也落在合理范围）
    let n = seq_len(arr).min(8);
    for i in 0..n {
        let e = seq_item(arr, i);
        if e.is_null() {
            continue;
        }
        let pos = read_obscured_int_at(e, 16);
        let sc = read_obscured_int_at(e, 36);
        let lb = read_obscured_int_at(e, 56);
        id.raw.push((pos, sc, lb));
        // 卡号口径见 data/ai_config.toml：supportCardId * 10 + limitBreakCount
        if sc > 0 && (0..=4).contains(&lb) {
            id.cards.push((sc * 10 + lb) as u32);
        }
    }
    id'''

ROUTE_OLD = '    } else if path == "/api/ai/init" {'
ROUTE_NEW = '''    } else if path == "/api/ai/deckcards" {
        mt_deck_raw_json()
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch8: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    if s.count(OLD) != 1:
        return _fail(f"identity read block anchor count={s.count(OLD)}")
    s = s.replace(OLD, NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "read_obscured_int_at(e, 16)",
        "read_obscured_int_at(e, 36)",
        "read_obscured_int_at(e, 56)",
        "seq_item(arr, i)",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())