use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn u32_neg_then_cast_to_i32_must_sign_extend_before_long_ops() {
    if std::mem::size_of::<usize>() != 8 {
        return;
    }

    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_u32_neg_cast_to_i32_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
unsigned long long seed;
int tf_2_var_94, tf_2_var_134;
unsigned tf_2_struct_obj_2_1;
void hash(long long *seed, int v) { *seed ^= v; }
int main() {
  tf_2_struct_obj_2_1 = 9035291;
  tf_2_var_134 = 0 > ((int)-tf_2_struct_obj_2_1 | (long)tf_2_var_94);
  hash(&seed, tf_2_var_134);
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
    assert_eq!(String::from_utf8_lossy(&run.stdout), "1\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
