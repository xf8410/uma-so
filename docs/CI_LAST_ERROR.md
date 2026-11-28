# CI 最近一次失败日志

- run: 46182095216
- sha: ba8de097c6f96235644b1ce6bb47b889ae866f0a

## 大小
```
so_size=12904320
```
## cargo error
```
```
## cargo 尾部
```
     --> src/lib.rs:26204:71
      |
26204 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as usize);
      |                                                                       ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
26204 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as *const () as usize);
      |                                                                       ++++++++++++

warning: direct cast of function item into an integer
     --> src/lib.rs:26252:27
      |
26252 |         failure_rate_hook as usize,
      |                           ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
26252 |         failure_rate_hook as *const () as usize,
      |                           ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:26253:9
      |
26253 |         &mut ORIG_FAILURE_RATE_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
26253 |         &raw mut ORIG_FAILURE_RATE_PROLOGUE,
      |          +++

warning: direct cast of function item into an integer
     --> src/lib.rs:26287:28
      |
26287 |         exec_training_hook as usize,
      |                            ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
26287 |         exec_training_hook as *const () as usize,
      |                            ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:26288:9
      |
26288 |         &mut ORIG_EXEC_TRAINING_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
26288 |         &raw mut ORIG_EXEC_TRAINING_PROLOGUE,
      |          +++

warning: `hachimi_ura` (lib) generated 199 warnings (run `cargo fix --lib -p hachimi_ura` to apply 93 suggestions)
    Finished `release` profile [optimized] target(s) in 1m 36s
```
## patch 尾部
```
patched src/lib.rs: 1057226 -> 1061477 bytes
  struct AlignedJmpBuf: 1 line(s)
  SIGSEGV_RECOVERY_TID: 5 line(s)
  fn ensure_il2cpp_thread_attached: 1 line(s)
  fn read_inline_obscured_int_array: 1 line(s)
  PUSH_READ_ENABLED: 5 line(s)
  sigsegv_recovered_in_worker: 1 line(s)
  crash_sig.log: 8 line(s)
  find_method_entry(class, "SelectTraining": 1 line(s)
  /api/push/read/on: 3 line(s)
patched src/lib.rs: 1061477 -> 1062900 bytes
  OLD_SIGSEGV_ACT: 3 line(s)
  libc::SA_SIGINFO: 2 line(s)
  core hooks ready: 1 line(s)
patched src/lib.rs: 1062900 -> 1069998 bytes
  read_inline_obscured_int_array(chara_effect_ids_arr): 1 line(s)
  "playing_state":1: 1 line(s)
  next_w[best_slot] = 1000;: 1 line(s)
  personDistribution: 4 line(s)
patched src/lib.rs: 1069998 -> 1073200 bytes
  has_act_cand: 3 line(s)
  decision.note.contains("重复帧"): 1 line(s)
  next_w[best_slot] = 170;: 1 line(s)
  get_TrainingLevelInfoArray: 4 line(s)
patched src/lib.rs: 1073200 -> 1089669 bytes
  fn read_obscured_bool_at: 1 line(s)
  read_ptr_at(chara, 1232): 1 line(s)
  festa_dataset_live: 2 line(s)
  fn mt_peek_turn_fast: 1 line(s)
  MT_STALE_SKIPS: 3 line(s)
  MT_SYNC_WAITS: 3 line(s)
  unified_10slot: 4 line(s)
patched src/lib.rs: 1089669 -> 1090843 bytes
  static MT_W: [std::sync::atomic::AtomicI32; 15]: 1 line(s)
  TAG_FRIEND_OUTING: 1 line(s)
  let mut next_w = [-1i32; 15];: 1 line(s)
  best_is_non_train: 3 line(s)
patched src/lib.rs: 1090843 -> 1092932 bytes
  read_ptr_at(sm, 208): 1 line(s)
  raceHistory: 3 line(s)
  advice_pending: 5 line(s)
  acts_advice_nonzero: 3 line(s)
patched src/lib.rs: 1092932 -> 1135306 bytes
  fn build_matsuri_frame_ex: 1 line(s)
  fn find_method_entry_exact: 1 line(s)
  fn read_il2cpp_ref_seq: 1 line(s)
  fn build_live_story_json: 1 line(s)
  fn decode_choice_branch_values: 1 line(s)
  fn festa_attach_advice_hook_handler: 1 line(s)
  fn festa_fusion_advice_hook_handler: 1 line(s)
  fn story_try_choice_hook_handler: 1 line(s)
  install_festa_advice_and_event_hooks: 3 line(s)
patched src/lib.rs: 1135306 -> 1140195 bytes
  fn get_bad_effect_condition_list_hook: 1
  fn install_bad_effect_filter_hook: 1
  install_bad_effect_filter_hook();: 1
  /api/ai/badfx: 1
  GetBadEffectConditionList: 3
  BADFX_HITS: 3
ci_patch20: all anchors ok
```
