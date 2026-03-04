use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn array_subscript_u32_must_not_be_loaded_as_u64_in_long_comparison() {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_array_subscript_u32_to_long_cmp_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
int g_130_1, func_39_i, main_l_42 = 7;
int *g_911 = &g_130_1;
long g_1414_4 = 8;
char func_39_p_40;
void func_39(int *p_41) {
  unsigned l_1416[5];
  for (; func_39_i < 5; func_39_i++)
    l_1416[func_39_i] = *g_911 = *p_41;
  func_39_p_40 = g_1414_4 < l_1416[2];
  *g_911 &= func_39_p_40;
}
int main() {
  func_39(&main_l_42);
  unsigned crc = g_130_1;
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
    assert_eq!(run.stdout, b"checksum = 0\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
