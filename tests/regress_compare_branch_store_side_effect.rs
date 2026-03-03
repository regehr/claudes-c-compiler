use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn compare_branch_fusion_must_preserve_stored_boolean_side_effect() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_compare_branch_store_side_effect_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);

char g_c;
int g_out;
int *g_ptr;

void f(char *p, int x) {
  for (;;) {
    x = *p <= 1;
    if (x)
      break;
  }
  g_ptr = &x;
  if (x)
    g_out = 7;
}

int main(void) {
  f(&g_c, 0);
  printf("%d\n", g_out);
  return 0;
}
"#;
    fs::write(&src_path, src).expect("write source");

    let ccc = env!("CARGO_BIN_EXE_ccc");
    let compile_status = Command::new(ccc)
        .args(["-O2", "-std=c99", "-w"])
        .arg(&src_path)
        .args(["-o"])
        .arg(&bin_path)
        .status()
        .expect("run ccc");
    assert!(compile_status.success(), "ccc compile failed");

    let run = Command::new(&bin_path).output().expect("run compiled program");
    assert!(run.status.success(), "compiled program failed");
    assert_eq!(String::from_utf8_lossy(&run.stdout), "7\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
