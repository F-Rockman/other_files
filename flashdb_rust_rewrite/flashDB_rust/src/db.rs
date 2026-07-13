//! Base database initialization and deinit, mirrors `src/fdb.c`.
//!
//! Provides [`db_init`], [`db_init_finish`], [`db_deinit`], and [`db_path`] as
//! free functions operating on [`FdbDb`].  KVDB and TSDB call these during
//! their own init flows.

use std::path::Path;

use crate::def::{DbType, FdbDb};
use crate::error::FdbError;

pub fn db_init(
    db: &mut FdbDb,
    name: &str,
    path: &Path,
    type_: DbType,
    file_mode: bool,
    sec_size: u32,
    max_size: u32,
) -> FdbError {
    if db.init_ok {
        return FdbError::NoErr;
    }

    db.name = name.to_string();
    db.type_ = type_;
    db.dir = path.to_path_buf();
    db.file_mode = file_mode;
    db.sec_size = sec_size;
    db.max_size = max_size;

    if file_mode {
        if sec_size == 0 {
            return FdbError::InitFailed;
        }
        if max_size == 0 {
            return FdbError::InitFailed;
        }
        if path.as_os_str().is_empty() {
            return FdbError::InitFailed;
        }
    }

    if sec_size == 0 || (sec_size & (sec_size - 1)) != 0 {
        return FdbError::InitFailed;
    }

    if max_size % sec_size != 0 {
        return FdbError::InitFailed;
    }

    if max_size / sec_size < 2 {
        return FdbError::InitFailed;
    }

    FdbError::NoErr
}

pub fn db_init_finish(db: &mut FdbDb, result: FdbError) {
    if result.is_ok() {
        db.init_ok = true;
    }
}

pub fn db_deinit(db: &mut FdbDb) {
    db.init_ok = false;
}

pub fn db_path(db: &FdbDb) -> &Path {
    &db.dir
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn make_db() -> FdbDb {
        FdbDb::new("", DbType::Kv, std::path::PathBuf::new())
    }

    #[test]
    fn db_init_succeeds_with_valid_config() {
        let dir = tempdir().unwrap();
        let mut db = make_db();
        let result = db_init(
            &mut db,
            "testdb",
            dir.path(),
            DbType::Kv,
            true,
            4096,
            8192,
        );
        assert!(result.is_ok());
        assert_eq!(db.name, "testdb");
        assert_eq!(db.sec_size, 4096);
        assert_eq!(db.max_size, 8192);
        assert!(db.file_mode);
    }

    #[test]
    fn db_init_fails_when_already_initialized() {
        let dir = tempdir().unwrap();
        let mut db = make_db();
        db_init(&mut db, "testdb", dir.path(), DbType::Kv, true, 4096, 8192);
        db.init_ok = true;
        let result = db_init(
            &mut db,
            "other",
            dir.path(),
            DbType::Kv,
            true,
            4096,
            8192,
        );
        assert!(result.is_ok());
    }

    #[test]
    fn db_init_fails_for_non_power_of_two_sec_size() {
        let dir = tempdir().unwrap();
        let mut db = make_db();
        let result = db_init(
            &mut db,
            "testdb",
            dir.path(),
            DbType::Kv,
            true,
            3000,
            8192,
        );
        assert_eq!(result, FdbError::InitFailed);
    }

    #[test]
    fn db_init_fails_when_max_not_multiple_of_sec() {
        let dir = tempdir().unwrap();
        let mut db = make_db();
        let result = db_init(
            &mut db,
            "testdb",
            dir.path(),
            DbType::Kv,
            true,
            4096,
            5000,
        );
        assert_eq!(result, FdbError::InitFailed);
    }

    #[test]
    fn db_init_fails_with_fewer_than_two_sectors() {
        let dir = tempdir().unwrap();
        let mut db = make_db();
        let result = db_init(
            &mut db,
            "testdb",
            dir.path(),
            DbType::Kv,
            true,
            4096,
            4096,
        );
        assert_eq!(result, FdbError::InitFailed);
    }

    #[test]
    fn db_init_fails_for_zero_sec_size_in_file_mode() {
        let dir = tempdir().unwrap();
        let mut db = make_db();
        let result = db_init(
            &mut db,
            "testdb",
            dir.path(),
            DbType::Kv,
            true,
            0,
            8192,
        );
        assert_eq!(result, FdbError::InitFailed);
    }

    #[test]
    fn db_init_fails_for_empty_path_in_file_mode() {
        let mut db = make_db();
        let result = db_init(
            &mut db,
            "testdb",
            Path::new(""),
            DbType::Kv,
            true,
            4096,
            8192,
        );
        assert_eq!(result, FdbError::InitFailed);
    }

    #[test]
    fn db_init_finish_sets_init_ok_on_success() {
        let mut db = make_db();
        db_init_finish(&mut db, FdbError::NoErr);
        assert!(db.init_ok);
    }

    #[test]
    fn db_init_finish_does_not_set_init_ok_on_failure() {
        let mut db = make_db();
        db_init_finish(&mut db, FdbError::InitFailed);
        assert!(!db.init_ok);
    }

    #[test]
    fn db_deinit_clears_init_ok() {
        let mut db = make_db();
        db.init_ok = true;
        db_deinit(&mut db);
        assert!(!db.init_ok);
    }

    #[test]
    fn db_path_returns_dir() {
        let dir = tempdir().unwrap();
        let db = FdbDb::new("testdb", DbType::Kv, dir.path());
        assert_eq!(db_path(&db), dir.path());
    }
}
