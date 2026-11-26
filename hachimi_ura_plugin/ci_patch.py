#!/usr/bin/env python3
"""CI 侧补丁：把 matsuri_bridge 挂进 lib.rs（mod 声明 + ENDPOINTS + 路由分支）。

为什么在 CI 里改而不是直接改 lib.rs：
  lib.rs 约 25k 行 / 1MB，手工整文件替换不可靠；脚本化替换每处都校验锚点命中数，
  锚点对不上就非零退出，比"悄悄没挂上"安全。三处锚点任一变化都必须先修本脚本。

锚点清单（改动 lib.rs 结构时必须同步）：
  1. `mod ui_rename;`                       —— 模块声明区末尾
  2. `    "/scenario",`                     —— const ENDPOINTS 里的首条（第一处命中）
  3. `    } else if path == "/scenario" {`  —— 请求路由链

关于 /api/ai/decide 为什么必须有：
  `matsuri_bridge::decide` 若没有调用者，LTO 会把整条决策链
  （ParsedFrame::parse → MatsuriGame::restore → route → Trainer::select_action → value 打分）
  全部当死代码删掉，产物里只剩数据表和 OnlineSession::new 的构造路径。
  判据：strip 后 `handle_frame` / `bridge_not_initialized` 这些串消失。
  所以 decide 必须挂到端点上，算法才会真的留在 .so 里。
"""

import sys

PATH = "src/lib.rs"


def _fail(msg: str) -> int:
    print(f"::error::ci_patch: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    # ---- 1) 模块声明 ----
    anchor = "mod ui_rename;"
    if s.count(anchor) != 1:
        return _fail(f"anchor 'mod ui_rename;' count={s.count(anchor)}")
    s = s.replace(
        anchor,
        anchor + '\n#[cfg(feature = "matsuri")]\nmod matsuri_bridge;',
        1,
    )

    # ---- 2) ENDPOINTS 注册（第一处 = const ENDPOINTS）----
    anchor = '    "/scenario",'
    if s.count(anchor) < 1:
        return _fail("anchor ENDPOINTS '\"/scenario\",' missing")
    s = s.replace(
        anchor,
        anchor
        + '\n    "/api/ai/status",\n    "/api/ai/init",\n    "/api/ai/decide",',
        1,
    )

    # ---- 3) 路由分支 ----
    anchor = '    } else if path == "/scenario" {'
    if s.count(anchor) != 1:
        return _fail(f"route anchor count={s.count(anchor)}")
    block = """    } else if path == "/api/ai/status" {
        #[cfg(feature = "matsuri")]
        {
            matsuri_bridge::status_json()
        }
        #[cfg(not(feature = "matsuri"))]
        {
            r#"{"ok":false,"error":"matsuri_feature_off"}"#.to_string()
        }
    } else if path == "/api/ai/init" {
        #[cfg(feature = "matsuri")]
        {
            // 配置：/sdcard/Download/uma_so/matsuri_uma.txt
            //   uma=101301
            //   cards=303174,303114,303004,303224,303204,303234
            //   seed=61444
            let mut uma = 0u32;
            let mut seed = 61444u64;
            let mut cards: Vec<u32> = Vec::new();
            if let Ok(text) = std::fs::read_to_string("/sdcard/Download/uma_so/matsuri_uma.txt") {
                for line in text.lines() {
                    let line = line.trim();
                    if line.is_empty() || line.starts_with('#') {
                        continue;
                    }
                    if let Some(v) = line.strip_prefix("uma=") {
                        uma = v.trim().parse().unwrap_or(0);
                    } else if let Some(v) = line.strip_prefix("seed=") {
                        seed = v.trim().parse().unwrap_or(61444);
                    } else if let Some(v) = line.strip_prefix("cards=") {
                        cards = v
                            .split(',')
                            .filter_map(|x| x.trim().parse::<u32>().ok())
                            .collect();
                    }
                }
            }
            match matsuri_bridge::init(uma, &cards, seed) {
                Ok(()) => format!(
                    r#"{{"ok":true,"uma":{},"cards":{},"seed":{}}}"#,
                    uma,
                    cards.len(),
                    seed
                ),
                Err(e) => format!(r#"{{"ok":false,"error":"{}"}}"#, e.replace('"', "'")),
            }
        }
        #[cfg(not(feature = "matsuri"))]
        {
            r#"{"ok":false,"error":"matsuri_feature_off"}"#.to_string()
        }
    } else if path == "/api/ai/decide" {
        #[cfg(feature = "matsuri")]
        {
            // 帧来源：/sdcard/Download/uma_so/frame.json（thisTurn.json 原样丢进去）
            //   —— 这一步是决策链的入口，缺它整条算法会被 LTO 裁掉
            let t0 = std::time::Instant::now();
            match std::fs::read_to_string("/sdcard/Download/uma_so/frame.json") {
                Ok(frame) => match matsuri_bridge::decide(&frame) {
                    Ok(d) => {
                        let scores = d
                            .scores
                            .iter()
                            .map(|(i, v)| format!(r#"{{"i":{},"v":{:.3}}}"#, i, v))
                            .collect::<Vec<_>>()
                            .join(",");
                        format!(
                            r#"{{"ok":true,"turn":{},"epoch":{},"kind":"{}","chosen":{},"desc":"{}","note":"{}","candidates":{},"search_backed":{},"scores":[{}],"elapsed_ms":{}}}"#,
                            d.turn,
                            d.epoch,
                            d.kind,
                            d.chosen,
                            d.action_desc.replace('"', "'"),
                            d.note.replace('"', "'"),
                            d.candidate_n,
                            d.search_backed,
                            scores,
                            t0.elapsed().as_millis()
                        )
                    }
                    Err(e) => format!(r#"{{"ok":false,"error":"{}"}}"#, e.replace('"', "'")),
                },
                Err(e) => {
                    format!(r#"{{"ok":false,"error":"read frame.json: {}"}}"#, e)
                }
            }
        }
        #[cfg(not(feature = "matsuri"))]
        {
            r#"{"ok":false,"error":"matsuri_feature_off"}"#.to_string()
        }
"""
    s = s.replace(anchor, block + anchor, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "mod matsuri_bridge",
        "/api/ai/status",
        "/api/ai/init",
        "/api/ai/decide",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())