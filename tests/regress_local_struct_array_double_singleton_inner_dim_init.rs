use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn local_struct_array_with_double_singleton_inner_dims_keeps_all_scalar_fields() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_local_struct_array_double_singleton_inner_dim_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
struct S0 {
  unsigned f0;
  signed f1;
} g_91, *g_1734 = &g_91;
int main() {
  struct S0 l_3197[][1][1] = {{{{8, 7}}}};
  *g_1734 = l_3197[0][0][0];
  unsigned crc = g_91.f1;
  printf("checksum = %X\n", crc);
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
    assert_eq!(run.stdout, b"checksum = 7\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
