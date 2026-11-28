# CI 最近一次失败日志

- run: 46129389671
- sha: 3cb3e4f0388b3afe07062430a1cac6ea3ff7132d

## 大小
```
```
## cargo error
```
328:error[E0133]: call to unsafe function `ura_log` is unsafe and requires unsafe function or block
509:error: could not compile `hachimi_ura` (lib) due to 1 previous error; 26 warnings emitted
```
## cargo 尾部
```
      |
16629 |     unsafe {
      |     ------ because it's nested under this `unsafe` block
...
16864 |                     unsafe {
      |                     ^^^^^^ unnecessary `unsafe` block

warning: variable does not need to be mutable
     --> src/lib.rs:17532:9
      |
17532 |     let mut dream_overflow = false;
      |         ----^^^^^^^^^^^^^^
      |         |
      |         help: remove this `mut`

warning: value assigned to `min_level` is never read
     --> src/lib.rs:17708:9
      |
17708 |         min_level = 0;
      |         ^^^^^^^^^^^^^
      |
      = help: maybe it is overwritten before being read?
      = note: `#[warn(unused_assignments)]` (part of `#[warn(unused)]`) on by default

warning: variable does not need to be mutable
     --> src/lib.rs:20232:20
      |
20232 |                 Ok(mut stmt) => stmt.column_names().iter().map(|s| s.to_string()).collect(),
      |                    ----^^^^
      |                    |
      |                    help: remove this `mut`

warning: value assigned to `all_events_count` is never read
     --> src/lib.rs:25202:37
      |
25202 |       let mut all_events_count: i32 = 0;
      |                                       ^ this value is reassigned later and never used
...
25241 | /             all_events_count = conn
25242 | |                 .query_row("SELECT COUNT(*) FROM single_mode_story_data", [], |r| {
25243 | |                     r.get(0)
25244 | |                 })
25245 | |                 .unwrap_or(0);
      | |_____________________________- `all_events_count` is overwritten here before the previous value is read

warning: unused variable: `ds_class`
     --> src/lib.rs:25731:9
      |
25731 |     let ds_class = get_class_from_object(ds_obj);
      |         ^^^^^^^^ help: if this is intentional, prefix it with an underscore: `_ds_class`

warning: unused variable: `is_literal`
     --> src/lib.rs:26753:13
      |
26753 |         let is_literal = match field_get_flags {
      |             ^^^^^^^^^^ help: if this is intentional, prefix it with an underscore: `_is_literal`

For more information about this error, try `rustc --explain E0133`.
warning: `hachimi_ura` (lib) generated 26 warnings
error: could not compile `hachimi_ura` (lib) due to 1 previous error; 26 warnings emitted
```
