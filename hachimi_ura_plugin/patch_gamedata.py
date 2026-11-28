#!/usr/bin/env python3
"""将游戏 master.mdb 提取的新卡 / 新角色补丁合并进 vendor/gamedata/cardDB.json 与 text_data_dict.json。

用途：
  上游 umaai-next/gamedata/cardDB.json 滞后于游戏 2.33.2（缺 30323 SSR 吾妻等 5 张新卡，
  且 text_data_dict.json 缺 9052 吾妻、1153 等角色名）。
  本脚本在 cargo build 前把 extra_gamedata.json 按“缺则补、已有保留上游”原则合并进
  vendor/gamedata/，确保内嵌进 libhachimi_ura.so 的表完整支持 2.33.2 全部支援卡。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRA_PATH = os.path.join(HERE, "extra_gamedata.json")
GAMEDATA_DIR = os.path.normpath(os.path.join(HERE, "..", "vendor", "gamedata"))


def main() -> int:
    extra = json.load(open(EXTRA_PATH, encoding="utf-8"))

    card_path = os.path.join(GAMEDATA_DIR, "cardDB.json")
    cards = json.load(open(card_path, encoding="utf-8"))
    added_cards = []
    for k, v in extra.get("cards", {}).items():
        if k not in cards:
            cards[k] = v
            added_cards.append(k)
    with open(card_path, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False)
    print(f"cardDB.json: +{len(added_cards)} cards ({added_cards}), total={len(cards)}")
    if "30323" not in cards:
        print("::error::patch_gamedata: 30323 still missing from cardDB.json")
        return 1

    td_path = os.path.join(GAMEDATA_DIR, "text_data_dict.json")
    td = json.load(open(td_path, encoding="utf-8"))
    cat6 = td.setdefault("6", {})
    added_ch = []
    for k, v in extra.get("text_data_6", {}).items():
        if k not in cat6:
            cat6[k] = v
            added_ch.append(f"{k}:{v}")
    with open(td_path, "w", encoding="utf-8") as f:
        json.dump(td, f, ensure_ascii=False)
    print(f"text_data_dict.json[6]: +{len(added_ch)} charas ({added_ch})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
