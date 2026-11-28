# CI 最近一次失败日志

- run: 46185470215
- sha: 0aef83a9947ec2ada0cfc60ac05d57e0a942557f

## 大小
```
```
## cargo error
```
328:error[E0133]: call to unsafe function `interceptor_get_trampoline` is unsafe and requires unsafe function or block
336:error[E0133]: call to unsafe function `std::intrinsics::transmute` is unsafe and requires unsafe function or block
517:error: could not compile `hachimi_ura` (lib) due to 2 previous errors; 26 warnings emitted
```
## cargo 尾部
```
      |                    help: remove this `mut`

warning: value assigned to `all_events_count` is never read
     --> src/lib.rs:25436:37
      |
25436 |       let mut all_events_count: i32 = 0;
      |                                       ^ this value is reassigned later and never used
...
25475 | /             all_events_count = conn
25476 | |                 .query_row("SELECT COUNT(*) FROM single_mode_story_data", [], |r| {
25477 | |                     r.get(0)
25478 | |                 })
25479 | |                 .unwrap_or(0);
      | |_____________________________- `all_events_count` is overwritten here before the previous value is read

warning: unused variable: `ds_class`
     --> src/lib.rs:25965:9
      |
25965 |     let ds_class = get_class_from_object(ds_obj);
      |         ^^^^^^^^ help: if this is intentional, prefix it with an underscore: `_ds_class`

warning: unused variable: `is_literal`
     --> src/lib.rs:26987:13
      |
26987 |         let is_literal = match field_get_flags {
      |             ^^^^^^^^^^ help: if this is intentional, prefix it with an underscore: `_is_literal`

For more information about this error, try `rustc --explain E0133`.
warning: `hachimi_ura` (lib) generated 26 warnings
error: could not compile `hachimi_ura` (lib) due to 2 previous errors; 26 warnings emitted
```
## patch 尾部
```
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
patched src/lib.rs: 1140195 -> 1143966 bytes
  fn get_filter_data_list_hook: 1
  fn install_filter_data_list_hook: 1
  install_filter_data_list_hook();: 1
  /api/ai/filter: 1
  GetFilterDataList: 4
  next_w[b] = 100_000: 1
ci_patch21: all anchors ok
```
## verify 尾部
```
```
