use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn const_cast_to_bool_must_normalize_to_zero_or_one() {
    if std::mem::size_of::<usize>() != 8 {
        return;
    }

    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_const_bool_cast_normalization_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
int main() {
  unsigned i = 0;
  for (; i < 24; i += (_Bool)61 + 0) {
  }
  printf("%u\n", i);
}
"#;
    fs::write(&src_path, src).expect("write source");

    let ccc = env!("CARGO_BIN_EXE_ccc");
    let compile_status = Command::new(ccc)
        .args(["-std=c99", "-w"])
        .arg(&src_path)
        .args(["-o"])
        .arg(&bin_path)
        .status()
        .expect("run ccc");
    assert!(compile_status.success(), "ccc compile failed");

    let run = Command::new(&bin_path).output().expect("run compiled program");
    assert!(run.status.success(), "compiled program failed");
    assert_eq!(String::from_utf8_lossy(&run.stdout), "24\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
