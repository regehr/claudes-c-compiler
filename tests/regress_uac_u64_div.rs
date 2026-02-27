use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn int_to_ulong_promotion_before_u64_division() {
    if std::mem::size_of::<usize>() != 8 {
        return;
    }

    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!("ccc_regress_uac_u64_div_{}_{}", std::process::id(), stamp));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
unsigned long long seed;
int tf_0_var_84, tf_0_var_120;
int *tf_0_ptr_1;
void hash(long long *seed, int v) { *seed ^= v; }
int main() {
  tf_0_ptr_1 = &tf_0_var_120;
  *tf_0_ptr_1 = ~(tf_0_var_84 + 0) / (unsigned long)(int)18142741702065582329;
  hash(&seed, tf_0_var_120);
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
    assert_eq!(String::from_utf8_lossy(&run.stdout), "1096172786\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
