use std::fs;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn bitfield_compound_assign_with_unsigned_short_rhs_must_zero_extend_rhs() {
    if std::mem::size_of::<usize>() != 8 {
        return;
    }

    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before unix epoch")
        .as_nanos();
    let tmp_dir = std::env::temp_dir().join(format!(
        "ccc_regress_bitfield_compound_assign_unsigned_short_{}_{}",
        std::process::id(),
        stamp
    ));
    fs::create_dir_all(&tmp_dir).expect("create temp dir");

    let src_path = tmp_dir.join("repro.c");
    let bin_path = tmp_dir.join("repro.out");

    let src = r#"int printf(const char *, ...);
struct { signed f : 18; } g;
char c = -97;
int main(void) {
  unsigned short x = c;
  g.f ^= x;
  printf("%d\n", g.f);
}
"#;
    fs::write(&src_path, src).expect("write source");

    let ccc = env!("CARGO_BIN_EXE_ccc");
    let compile_status = Command::new(ccc)
        .args(["-std=c99", "-w", "-O0"])
        .arg(&src_path)
        .args(["-o"])
        .arg(&bin_path)
        .status()
        .expect("run ccc");
    assert!(compile_status.success(), "ccc compile failed");

    let run = Command::new(&bin_path).output().expect("run compiled program");
    assert!(run.status.success(), "compiled program failed");
    assert_eq!(String::from_utf8_lossy(&run.stdout), "65439\n");

    let _ = fs::remove_dir_all(&tmp_dir);
}
