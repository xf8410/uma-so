#!/usr/bin/env python3
"""CI 侧补丁 10：dataset 缺失时给空 festa 段，而不是整帧失败。

背景（2026-11-27，游戏 2.33.2）：
  matsuri 会话已能初始化（ready=true，卡组 6 张过了门禁），
  但帧构造报 `dataset_null, scenario_id=15, turn=16`。
  实测 `WorkSingleModeScenarioFesta` 只有 1 个字段（+16 DataSet），
  而 property `get_DataSet` 与 backing field 都指向 null —— 2.33.2 里
  这套 DataSet 在运行时不再绑定，数据搬到 `SingleMode.ScenarioFesta.*`
  的 Repository/Entity（ActEntity +56 CurrentActGauge 等）。

  直接调 `ActListRepository.Get()`（静态零参）会让游戏崩，所以这条先不走。

为什么可以给空 acts：
  matsuri 的 `restore_acts` 是"逐条覆盖"语义，注释明确写着
  `level == 0 表示本帧未给（真机运行期恒 ≥1），保持默认 Lv1`。
  也就是说 acts 为空是**合法缺省**，引擎自己会用 gauge=0 / level=1 继续算，
  不会当假数据用。这不是编造数值，是把"本帧没有这项数据"如实表达出来。

代价（必须写在明处）：
  演目槽与演目 Lv 缺失 → 稽古相关估值偏低、`ActTrain` 候选的打分不准。
  所以这一版只是让决策链跑通、拿到 matsuri 的出分形状；
  真实 acts 仍要从新数据层补，补齐前不能拿它的稽古决策当真。

本补丁：
  `build_matsuri_frame()` 里 dataset_obj 为 null 时，不再返回 error，
  而是输出「baseGame 完整 + festa 空壳」的帧，并在 note 里标注 dataset_missing。
"""

import sys

PATH = "src/lib.rs"

OLD = r'''    if dataset_obj.is_null() {
        // 数据集没绑定 = 不在决策点（结算中间态 / 未开场），直接报错而不是喂残帧
        return format!(
            r#"{{"error":"dataset_null","scenario_id":{},"turn":{}}}"#,
            sid, turn
        );
    }'''

NEW = r'''    if dataset_obj.is_null() {
        // 2.33.2 里 FestaDataSet 运行时不再绑定（get_DataSet 与 backing field 都是 null），
        // 数据搬到 SingleMode.ScenarioFesta.* 的 Repository/Entity。
        // 直接调 ActListRepository.Get() 会让游戏崩，这条先不走。
        //
        // matsuri 的 restore_acts 是"逐条覆盖"：acts 为空时保持默认 gauge=0 / level=1，
        // 引擎注释明确写了 level==0 表示本帧未给 —— 所以空 acts 是合法缺省，不是假数据。
        // 代价：稽古/演目 Lv 相关估值偏低，补齐真实 acts 前不能拿稽古决策当真。
        return format!(
            r#"{{"festa":{{"acts":[],"maxActLevel":10,"campRemain":0,"trainLevelTotal":null,"persons":[],"checkPoints":[]}},"baseGame":{{"scenarioId":{},"umaId":{},"turn":{},"vital":{},"maxVital":{},"motivation":{},"fiveStatus":[{},{},{},{},{}],"fiveStatusLimit":[{},{},{},{},{}],"skillPt":{},"skillScore":0,"totalHints":0,"trainLevelCount":[0,0,0,0,0],"ptScoreRate":1.0,"failureRateBias":0,"isIll":{},"isQieZhe":false,"isXiaoQie":false,"isAiJiao":false,"PositiveThinkingCount":0,"isRefreshMind":false,"LuckyCount":0,"zhongMaBlueCount":[0,0,0,0,0],"isRacing":false,"cardId":[],"persons":[],"personDistribution":[],"lockedTrainingId":0,"friendship_noncard_yayoi":0,"friendship_noncard_reporter":0,"friend_stage":0,"friend_outgoingUsed":0,"charaEffects":[{}],"training":{{}}}},"notes":["dataset_missing"]}}"#,
            sid,
            uma_id,
            turn,
            vital,
            max_vital,
            motivation,
            f[0], f[1], f[2], f[3], f[4],
            lim[0], lim[1], lim[2], lim[3], lim[4],
            skill_pt,
            if eff_ids.is_empty() { "false" } else { "true" },
            eff_json
        );
    }'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch10: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    if s.count(OLD) != 1:
        return _fail(f"dataset_null block anchor count={s.count(OLD)}")
    s = s.replace(OLD, NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in ("dataset_missing", "\"acts\":[]", "notes"):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    if 'dataset_null' in s:
        print("  ::warning:: 仍有 dataset_null 字样（应为 0，旧分支已替换）")
    return 0


if __name__ == "__main__":
    sys.exit(main())