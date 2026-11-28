# CI 最近一次失败日志

- run: 46182567009
- sha: 459e4ac65113ca13a0d966c2f466eed486c194f6

## 大小
```
so_size=12904320
```
## cargo error
```
```
## cargo 尾部
```
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
    Finished `release` profile [optimized] target(s) in 2m 02s
```
## patch 尾部
```
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
## verify 尾部
```
so_size=12904320
ok: api/ai/status
ok: api/ai/init
ok: api/ai/frame
ok: api/ai/decide
ok: api/ai/hooks
ok: api/ai/weights
ok: api/ai/tables
ok: api/ai/weightcalls
ok: api/ai/override
ok: api/ai/entry
ok: api/ai/matsuri
::error::缺端点 api/ai/badfx
```
