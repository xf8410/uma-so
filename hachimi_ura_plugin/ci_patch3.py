#!/usr/bin/env python3
"""CI 侧补丁 3：GetBaseWeight 运行时权重覆盖。

为什么需要这一层：
  实测确认 GetBaseWeight(commandIdType) 返回的就是
  master.mdb 里 omakase_command.base_weight（150101→10 / 150107→20 /
  150108→0 / 150124→200，与表内数值逐条一致）。
  表在加密 mdb 里，改表影响所有对局且不可热回滚，所以在 hook 上加一层覆盖表。

覆盖文件（2 秒缓存，按顺序取第一个能读到的）：
  1. /sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/weight_override.txt
  2. /sdcard/Download/uma_so/weight_override.txt
  每行 `commandId=weight`，`#` 开头注释；空文件 = 不覆盖。

为什么首选 Android/data：
  游戏 targetSdk=36，实测 READ_EXTERNAL_STORAGE / WRITE_EXTERNAL_STORAGE
  都是 granted=false，且该 targetSdk 下这对权限对 Download 已不适用
  （需要 MANAGE_EXTERNAL_STORAGE）。而应用自己的
  Android/data/<pkg>/files 不需要任何权限，且 shell 侧可写入投放文件。

用途：
  1. 验证 150120–150124 与五个训练位的一一对应（填 10000 看它练哪项）；
  2. 映射确认后，这条通道就是「用 matsuri 分数替代原版权重」的入口。

安全：
  - hook 内不做 IO（2 秒才重读一次，其余命中只查内存 Vec）；
  - 未命中覆盖时原样返回 trampoline 结果，行为与未安装一致；
  - 覆盖命中数单列计数，/api/ai/override 可查。

实测记录（2026-11-27，游戏 2.33.2 / setting 1501）：
  omakase_command 84 行，1501 占 15 行：
    150101-150106 → 10   150107 → 20   150108/150109 → 0
    150110 → 10          150120-150124 → 200（五个训练位）
  GetBaseWeight 命中样本与表内一致；SelectTraining 的 arg/ret 都是指针（非索引）。
"""

import sys

PATH = "src/lib.rs"

OVERRIDE_FN = r'''
// ===== GetBaseWeight 运行时覆盖表 =====
//
// 路径按顺序尝试，第一个读到的生效：
//   1. Android/data/<pkg>/files/uma_so/  （应用自有目录，无需权限，shell 可投放）
//   2. Download/uma_so/                  （targetSdk=36 下应用通常读不到）
static WB_OVR_AT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static WB_OVR_HITS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static WB_OVR_SIZE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
static WB_OVR: std::sync::Mutex<Vec<(i32, i32)>> = std::sync::Mutex::new(Vec::new());
static WB_OVR_SRC: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());

const WB_OVR_PATHS: &[&str] = &[
    "/sdcard/Android/data/jp.co.cygames.umamusume/files/uma_so/weight_override.txt",
    "/sdcard/Download/uma_so/weight_override.txt",
];

fn wb_now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn wb_override_reload() {
    use std::sync::atomic::Ordering::Relaxed;
    let now = wb_now_ms();
    let last = WB_OVR_AT.load(Relaxed);
    if last != 0 && now.saturating_sub(last) < 2000 {
        return;
    }
    WB_OVR_AT.store(now, Relaxed);

    let mut picked: Option<(&str, String)> = None;
    for p in WB_OVR_PATHS {
        if let Ok(t) = std::fs::read_to_string(p) {
            picked = Some((p, t));
            break;
        }
    }
    let (src, text) = match picked {
        Some(v) => v,
        None => return,
    };

    let mut out: Vec<(i32, i32)> = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            if let (Ok(k), Ok(v)) = (k.trim().parse::<i32>(), v.trim().parse::<i32>()) {
                out.push((k, v));
            }
        }
    }
    WB_OVR_SIZE.store(out.len() as u64, Relaxed);
    if let Ok(mut g) = WB_OVR.lock() {
        *g = out;
    }
    if let Ok(mut s) = WB_OVR_SRC.lock() {
        *s = src.to_string();
    }
}

fn wb_override_lookup(arg: i32) -> Option<i32> {
    wb_override_reload();
    let g = WB_OVR.lock().ok()?;
    g.iter().find(|(k, _)| *k == arg).map(|(_, v)| *v)
}

fn wb_override_json() -> String {
    use std::sync::atomic::Ordering::Relaxed;
    let pairs = WB_OVR
        .lock()
        .map(|g| {
            g.iter()
                .map(|(k, v)| format!(r#"{{"id":{k},"w":{v}}}"#))
                .collect::<Vec<_>>()
                .join(",")
        })
        .unwrap_or_default();
    let src = WB_OVR_SRC
        .lock()
        .map(|s| s.clone())
        .unwrap_or_default()
        .replace('"', "'");
    format!(
        r#"{{"ok":true,"source":"{}","candidates":{},"size":{},"applied":{},"entries":[{}]}}"#,
        src,
        WB_OVR_PATHS.len(),
        WB_OVR_SIZE.load(Relaxed),
        WB_OVR_HITS.load(Relaxed),
        pairs
    )
}
'''

HOOK_OLD = """        let ret = original(this, arg0);
        let slot = (n as usize % 16) * 2;
        WB_LOG[slot].store(arg0 as i64, Relaxed);
        WB_LOG[slot + 1].store(ret as i64, Relaxed);
        ret"""

HOOK_NEW = """        let orig = original(this, arg0);
        let slot = (n as usize % 16) * 2;
        if let Some(v) = wb_override_lookup(arg0) {
            WB_OVR_HITS.fetch_add(1, Relaxed);
            WB_LOG[slot].store(arg0 as i64, Relaxed);
            WB_LOG[slot + 1].store(v as i64, Relaxed);
            return v;
        }
        WB_LOG[slot].store(arg0 as i64, Relaxed);
        WB_LOG[slot + 1].store(orig as i64, Relaxed);
        orig"""

ROUTE_OLD = '    } else if path == "/api/ai/init" {'

ROUTE_NEW = '''    } else if path == "/api/ai/override" {
        wb_override_json()
    } else if path == "/api/ai/init" {'''


def _fail(msg: str) -> int:
    print(f"::error::ci_patch3: {msg}")
    return 1


def main() -> int:
    s = open(PATH, encoding="utf-8").read()
    before = len(s)

    anchor = "unsafe fn install_weight_hook() -> String {"
    if s.count(anchor) != 1:
        return _fail(f"anchor install_weight_hook count={s.count(anchor)}")
    s = s.replace(anchor, OVERRIDE_FN.strip() + "\n\n" + anchor, 1)

    if s.count(HOOK_OLD) != 1:
        return _fail(f"hook body anchor count={s.count(HOOK_OLD)}")
    s = s.replace(HOOK_OLD, HOOK_NEW, 1)

    anchor = '    "/api/ai/weightcalls",'
    if s.count(anchor) != 1:
        return _fail(f"anchor ENDPOINTS weightcalls count={s.count(anchor)}")
    s = s.replace(anchor, anchor + '\n    "/api/ai/override",', 1)

    if s.count(ROUTE_OLD) != 1:
        return _fail(f"route anchor init count={s.count(ROUTE_OLD)}")
    s = s.replace(ROUTE_OLD, ROUTE_NEW, 1)

    open(PATH, "w", encoding="utf-8").write(s)
    print(f"patched {PATH}: {before} -> {len(s)} bytes")
    for needle in (
        "WB_OVR_PATHS",
        "Android/data/jp.co.cygames.umamusume/files/uma_so",
        "fn wb_override_lookup",
        "wb_override_lookup(arg0)",
        "/api/ai/override",
    ):
        hits = sum(1 for line in s.splitlines() if needle in line)
        print(f"  {needle}: {hits} line(s)")
        if hits == 0:
            return _fail(f"{needle} 补丁后仍不存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())