use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn local_union_array_with_singleton_inner_dim_initializes_nested_struct_member() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_local_union_array_singleton_inner_dim_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
struct S1 {
  int f0;
  int f1;
};
union U3 {
  struct S1 f0;
};
int main() {
  union U3 a[][1] = {{}, {}, {}, {}, {}, {}, {}, {}, {}, {{{0, 2}}}};
  printf("checksum = %X\n", (unsigned)a[9][0].f0.f1);
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
    assert_eq!(run.stdout, b"checksum = 2\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
