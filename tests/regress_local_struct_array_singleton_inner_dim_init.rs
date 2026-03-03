use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn local_struct_array_with_singleton_inner_dim_initializes_nested_struct() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_local_struct_array_singleton_inner_dim_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
struct S1 {
  signed f1;
  signed f2;
};
char g_2;
int main() {
  struct S1 l_17[][1] = {{}, {}, {}, {}, {}, {}, {}, {{9, 4}}};
  l_17[7][0].f2 && printf("checksum = %X\n", g_2);
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
    assert_eq!(run.stdout, b"checksum = 0\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
