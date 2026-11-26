# CI 最近一次失败日志

- run: 45906405732
- sha: a4c63669dbde3f16e33dd741c0a27272921f170f

## 大小
```
so_size=14376744
gamedata_bytes=7669787
```
## cargo error 行
```
```
## cargo 日志尾部
```
     --> src/lib.rs:22860:45
      |
22860 |     let jmp_result = unsafe { sys_sigsetjmp(SIGSEGV_JMP_BUF.as_mut_ptr(), 1) };
      |                                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>

warning: direct cast of function item into an integer
     --> src/lib.rs:23329:72
      |
23329 |         let trampoline = interceptor_get_trampoline(exec_training_hook as usize);
      |                                                                        ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
23329 |         let trampoline = interceptor_get_trampoline(exec_training_hook as *const () as usize);
      |                                                                        ++++++++++++

warning: direct cast of function item into an integer
     --> src/lib.rs:23421:71
      |
23421 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as usize);
      |                                                                       ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
23421 |         let trampoline = interceptor_get_trampoline(failure_rate_hook as *const () as usize);
      |                                                                       ++++++++++++

warning: direct cast of function item into an integer
     --> src/lib.rs:23469:27
      |
23469 |         failure_rate_hook as usize,
      |                           ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
23469 |         failure_rate_hook as *const () as usize,
      |                           ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:23470:9
      |
23470 |         &mut ORIG_FAILURE_RATE_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
23470 |         &raw mut ORIG_FAILURE_RATE_PROLOGUE,
      |          +++

warning: direct cast of function item into an integer
     --> src/lib.rs:23504:28
      |
23504 |         exec_training_hook as usize,
      |                            ^^^^^^^^
      |
help: first cast to a pointer `as *const ()`
      |
23504 |         exec_training_hook as *const () as usize,
      |                            ++++++++++++

warning: creating a mutable reference to mutable static
     --> src/lib.rs:23505:9
      |
23505 |         &mut ORIG_EXEC_TRAINING_PROLOGUE,
      |         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ mutable reference to mutable static
      |
      = note: mutable references to mutable statics are dangerous; it's undefined behavior if any other pointer to the static is used or if any other reference is created for the static while the mutable reference lives
      = note: for more information, see <https://doc.rust-lang.org/edition-guide/rust-2024/static-mut-references.html>
help: use `&raw mut` instead to create a raw pointer
      |
23505 |         &raw mut ORIG_EXEC_TRAINING_PROLOGUE,
      |          +++

warning: `hachimi_ura` (lib) generated 180 warnings (run `cargo fix --lib -p hachimi_ura` to apply 86 suggestions)
    Finished `release` profile [optimized] target(s) in 1m 34s
```
