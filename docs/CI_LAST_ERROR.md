# CI 最近一次失败日志

- run: 45841281482
- sha: 7f1a3cf4fcc0c9817b8dcae08a621dca356a489d

## patch 输出
```
patched src/lib.rs: 1009399 -> 1011547 bytes
  mod matsuri_bridge: 1 line(s)
  /api/ai/status: 2 line(s)
  /api/ai/init: 2 line(s)
```
## cargo error 行
```
285:warning: unused doc comment
297:warning: unused doc comment
308:warning: unused variable: `chara_id`
316:warning: unused variable: `ramen_gauge_gains`
322:warning: unused variable: `next_turn_race`
346:warning: unused variable: `limit_break`
360:warning: unused variable: `resp`
374:warning: unused variable: `api`
380:warning: unused variable: `api`
487:warning: unused variable: `ds_class`
493:warning: unused variable: `is_literal`
```
## cargo 日志尾部
```

warning: direct cast of function item into an integer
     --> src/lib.rs:13791:61
      |
13791 |             let ok = interceptor_hook(ae, sqlite3_exec_hook as usize);
      |                                                             ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
13791 |             let ok = interceptor_hook(ae, sqlite3_exec_hook as *const () as usize);
      |                                                             ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:20738:46
      |
20738 |     let jump_result = unsafe { sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1) };
      |                                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>

warning: creating a mutable reference to mutable static
     --> src/lib.rs:20880:46
      |
20880 |     let jump_result = unsafe { sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1) };
      |                                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>

warning: creating a mutable reference to mutable static
     --> src/lib.rs:22042:45
      |
22042 |     let jmp_result = unsafe { sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1) };
      |                                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>

warning: creating a mutable reference to mutable static
     --> src/lib.rs:22225:45
      |
22225 |     let jmp_result = unsafe { sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1) };
      |                                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>

warning: direct cast of function item into an integer
     --> src/lib.rs:22694:72
      |
22694 |         let trampoline = interceptor_get_trampoline(exec_training_hook as usize);
      |                                                                        ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
22694 |         let trampoline = interceptor_get_trampoline(exec_training_hook as *const () as usize);
      |                                                                        ++++++++++++

warning: direct cast of function item into an integer
     --> src/lib.rs:22786:71
      |
22786 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as usize);
      |                                                                       ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
22786 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as *const () as usize);
      |                                                                       ++++++++++++

warning: direct cast of function item into an integer
     --> src/lib.rs:22834:27
      |
22834 |         failure_rate_hook as usize,
      |                           ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
22834 |         failure_rate_hook as *const () as usize,
      |                           ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:22835:9
      |
22835 |         &mut ORIG_FAILURE_RATE_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
22835 |         &raw mut ORIG_FAILURE_RATE_PROLOGUE,
      |          +++

warning: direct cast of function item into an integer
     --> src/lib.rs:22869:28
      |
22869 |         exec_training_hook as usize,
      |                            ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
22869 |         exec_training_hook as *const () as usize,
      |                            ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:22870:9
      |
22870 |         &mut ORIG_EXEC_TRAINING_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
22870 |         &raw mut ORIG_EXEC_TRAINING_PROLOGUE,
      |          +++

warning: `hachimi_ura` (lib) generated 176 warnings (run `cargo fix --lib -p hachimi_ura` to apply 82 suggestions)
    Finished `release` profile [optimized] target(s) in 1m 53s
```
