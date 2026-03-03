use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn packed_small_struct_assign_must_not_clobber_adjacent_global() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_small_struct_packed_assign_clobber_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
struct __attribute__((packed)) S { unsigned char b[5]; } g = {{1, 2, 3, 4, 5}};
short marker = 4;
struct S h;
struct S *p = &g;
struct S ret(void) { return h; }
int main(void) {
  *p = ret();
  printf("checksum = %X\n", marker);
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
    assert_eq!(run.stdout, b"checksum = 4\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
