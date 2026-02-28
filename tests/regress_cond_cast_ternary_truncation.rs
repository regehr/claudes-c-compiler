use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn casted_ternary_in_condition_must_truncate_before_truthiness() {
    if std::mem::size_of::<usize>() != 8 {
        return;
    }

    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_cond_cast_ternary_truncation_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
unsigned long long seed;
char tf_4_array_6_2_3, tf_4_array_6_2_5_3;
int main() {
  if (8 ^ tf_4_array_6_2_5_3)
    if ((char)(tf_4_array_6_2_3 ? 512 : 512))
      printf("%llu\n", seed);
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
    assert!(run.stdout.is_empty(), "program should not print anything");

    let _ = fs::remove_dir_all(&tmp_dir);
}
