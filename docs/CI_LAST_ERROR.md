# CI 最近一次失败日志

- run: 46111276567
- sha: d10f83318be64abe8917e11bab1fdc4c3b3d75ef

## 大小
```
```
## cargo error
```
285:error[E0428]: the name `read_obscured_int_array` is defined multiple times
325:error[E0061]: this function takes 1 argument but 3 arguments were supplied
349:error[E0061]: this function takes 1 argument but 3 arguments were supplied
373:error[E0061]: this function takes 1 argument but 3 arguments were supplied
580:error: could not compile `hachimi_ura` (lib) due to 4 previous errors; 25 warnings emitted
```
## cargo 尾部
```
14829 |     unsafe {
      |     ------ because it's nested under this `unsafe` block
...
15064 |                     unsafe {
      |                     ^^^^^^ unnecessary `unsafe` block

warning: variable does not need to be mutable
     --> src/lib.rs:15732:9
      |
15732 |     let mut dream_overflow = false;
      |         ----^^^^^^^^^^^^^^
      |         |
      |         help: remove this `mut`

warning: value assigned to `min_level` is never read
     --> src/lib.rs:15908:9
      |
15908 |         min_level = 0;
      |         ^^^^^^^^^^^^^
      |
      = help: maybe it is overwritten before being read?
      = note: `#[warn(unused_assignments)]` (part of `#[warn(unused)]`) on by default

warning: variable does not need to be mutable
     --> src/lib.rs:18432:20
      |
18432 |                 Ok(mut stmt) => stmt.column_names().iter().map(|s| s.to_string()).collect(),
      |                    ----^^^^
      |                    |
      |                    help: remove this `mut`

warning: value assigned to `all_events_count` is never read
     --> src/lib.rs:23404:37
      |
23404 |       let mut all_events_count: i32 = 0;
      |                                       ^ this value is reassigned later and never used
...
23443 | /             all_events_count = conn
23444 | |                 .query_row("SELECT COUNT(*) FROM single_mode_story_data", [], |r| {
23445 | |                     r.get(0)
23446 | |                 })
23447 | |                 .unwrap_or(0);
      | |_____________________________- `all_events_count` is overwritten here before the previous value is read

warning: unused variable: `ds_class`
     --> src/lib.rs:23935:9
      |
23935 |     let ds_class = get_class_from_object(ds_obj);
      |         ^^^^^^^^ help: if this is intentional, prefix it with an underscore: `_ds_class`

warning: unused variable: `is_literal`
     --> src/lib.rs:24957:13
      |
24957 |         let is_literal = match field_get_flags {
      |             ^^^^^^^^^^ help: if this is intentional, prefix it with an underscore: `_is_literal`

Some errors have detailed explanations: E0061, E0428.
For more information about an error, try `rustc --explain E0061`.
warning: `hachimi_ura` (lib) generated 25 warnings
error: could not compile `hachimi_ura` (lib) due to 4 previous errors; 25 warnings emitted
```
## patch 尾部
```
  read_obscured_int_at(e, 36): 7 line(s)
  read_obscured_int_at(e, 56): 4 line(s)
  seq_item(arr, i): 2 line(s)
patched src/lib.rs: 1053429 -> 1054501 bytes
  fn mt_start_worker: 1 line(s)
  matsuri-worker: 2 line(s)
  mt_start_worker();: 1 line(s)
  mt_recompute() 直接调用点: 0（应为 1，仅 worker 内）
patched src/lib.rs: 1054501 -> 1055879 bytes
  dataset_missing: 1 line(s)
  "acts":[]: 1 line(s)
  notes: 2 line(s)
  ::warning:: 仍有 dataset_null 字样（应为 0，旧分支已替换）
patched src/lib.rs: 1055879 -> 1057226 bytes
  MT_LAST_HIT: 3 line(s)
  idle: 14 line(s)
  chara_class_mismatch: 3 line(s)
ci_patch12_pre: patched ci_patch12.py
  P2 sigsetjmp call sites: 10
patched src/lib.rs: 1057226 -> 1064177 bytes
patched src/matsuri_bridge.rs: 10987 -> 12007 bytes
  lib · fn read_obscured_int_array: 2
  lib · read_obscured_int_array(chara_effect_ids_arr): 1
  lib · SIGSEGV_TID: 12
  lib · [u8; 320]: 1
  lib · fn mt_thread_attach: 1
  lib · mt_thread_attach();: 1
  lib · fn push_read_enabled: 1
  lib · push_read_enabled(): 2
  lib · install_weight_hook();: 1
  lib · has_act_candidates: 3
  lib · MT_GEN: 4
  lib · find_method_entry(class, "SelectTraining", 1): 1
  bridge · Android/data/jp.co.cygames.umamusume/files/uma_so/matsuri_data: 1
  bridge · static DATA_INIT: Mutex<bool>: 1
  bridge · fn ensure_assets: 1
  bridge · fn ensure_data: 1
  keep · handle_frame: 2
  keep · 候选动作为空: 0
::warning::源码里没有 候选动作为空（由 verify 扫 .so 兜底）
  keep · 帧解析失败: 0
::warning::源码里没有 帧解析失败（由 verify 扫 .so 兜底）
  keep · 守门: 1
  keep · matsuri-worker: 2
  keep · dataset_missing: 2
  keep · chara_class_mismatch: 3
  keep · weight_override.txt: 2
  keep · find_method_entry: 6
  keep · trampoline_miss: 2
ci_patch12: all anchors ok
```
