use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn stmt_expr_typeof_nested_comparison_must_infer_int() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_stmt_expr_typeof_nested_cmp_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
unsigned long long seed;
int var_35 = 80976248578483579;
void hash(long long *seed, int v) { *seed ^= v; }
int main(void) {
  var_35 &= ({
    __typeof__(0) _a = ~0;
    __typeof__(({
      __typeof__(4ULL) _a;
      _a < _a;
    })) _b = 0;
    _a < _b;
  });
  hash(&seed, var_35);
  printf("%llu\n", seed);
  return 0;
}
"#;
    fs::write(&src_path, src).expect("write source");

    let ccc = env!("CARGO_BIN_EXE_ccc");
    let compile = Command::new(ccc)
        .args(["-O2", "-std=c99"])
        .arg(&src_path)
        .args(["-o"])
        .arg(&bin_path)
        .output()
        .expect("run ccc");
    assert!(
        compile.status.success(),
        "ccc compile failed: {}",
        String::from_utf8_lossy(&compile.stderr)
    );
    let stderr = String::from_utf8_lossy(&compile.stderr);
    assert!(
        !stderr.contains("could not resolve type of 'typeof' expression"),
        "unexpected typeof fallback warning: {stderr}"
    );

    let run = Command::new(&bin_path).output().expect("run compiled program");
    assert!(run.status.success(), "compiled program failed");
    assert_eq!(String::from_utf8_lossy(&run.stdout), "1\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
