# CI 最近一次失败日志

- run: 46111620363
- sha: 3b5fb29a1b2febc5b3000d65c60d6e42d0ffbc67

## 大小
```
so_size=14387680
```
## cargo error
```
```
## cargo 尾部
```
     --> src/lib.rs:24278:71
      |
24278 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as usize);
      |                                                                       ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
24278 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as *const () as usize);
      |                                                                       ++++++++++++

warning: direct cast of function item into an integer
     --> src/lib.rs:24326:27
      |
24326 |         failure_rate_hook as usize,
      |                           ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
24326 |         failure_rate_hook as *const () as usize,
      |                           ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:24327:9
      |
24327 |         &mut ORIG_FAILURE_RATE_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
24327 |         &raw mut ORIG_FAILURE_RATE_PROLOGUE,
      |          +++

warning: direct cast of function item into an integer
     --> src/lib.rs:24361:28
      |
24361 |         exec_training_hook as usize,
      |                            ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
24361 |         exec_training_hook as *const () as usize,
      |                            ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:24362:9
      |
24362 |         &mut ORIG_EXEC_TRAINING_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
24362 |         &raw mut ORIG_EXEC_TRAINING_PROLOGUE,
      |          +++

warning: `hachimi_ura` (lib) generated 178 warnings (run `cargo fix --lib -p hachimi_ura` to apply 85 suggestions)
    Finished `release` profile [optimized] target(s) in 2m 01s
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
patched src/lib.rs: 1057226 -> 1064191 bytes
patched src/matsuri_bridge.rs: 10987 -> 12007 bytes
  lib · fn read_inline_obscured_int_array: 1
  lib · read_inline_obscured_int_array(chara_effect_ids_arr): 1
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
