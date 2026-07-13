//! FlashDB error codes.
//!
//! Mirrors `fdb_err_t` from `inc/fdb_def.h`. Numeric values are identical to
//! the C enum so persisted status codes stay compatible.

use std::fmt;

/// FlashDB error code.
///
/// `FdbError::NoErr` (value `0`) represents success, matching `FDB_NO_ERR`.
#[repr(i32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FdbError {
    NoErr = 0,
    EraseErr = 1,
    ReadErr = 2,
    WriteErr = 3,
    KvNameErr = 4,
    KvNameExist = 5,
    SavedFull = 6,
    InitFailed = 7,
}

impl FdbError {
    #[inline]
    pub fn is_ok(self) -> bool {
        matches!(self, FdbError::NoErr)
    }

    #[inline]
    pub fn is_err(self) -> bool {
        !self.is_ok()
    }

    #[inline]
    pub fn to_result(self) -> Result<(), FdbError> {
        if self.is_ok() {
            Ok(())
        } else {
            Err(self)
        }
    }
}

impl fmt::Display for FdbError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FdbError::NoErr => f.write_str("FDB_NO_ERR"),
            FdbError::EraseErr => f.write_str("FDB_ERASE_ERR"),
            FdbError::ReadErr => f.write_str("FDB_READ_ERR"),
            FdbError::WriteErr => f.write_str("FDB_WRITE_ERR"),
            FdbError::KvNameErr => f.write_str("FDB_KV_NAME_ERR"),
            FdbError::KvNameExist => f.write_str("FDB_KV_NAME_EXIST"),
            FdbError::SavedFull => f.write_str("FDB_SAVED_FULL"),
            FdbError::InitFailed => f.write_str("FDB_INIT_FAILED"),
        }
    }
}

impl std::error::Error for FdbError {}

impl Default for FdbError {
    #[inline]
    fn default() -> Self {
        FdbError::NoErr
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_err_is_zero_and_ok() {
        assert_eq!(FdbError::NoErr as i32, 0);
        assert!(FdbError::NoErr.is_ok());
        assert!(!FdbError::NoErr.is_err());
        assert!(FdbError::NoErr.to_result().is_ok());
    }

    #[test]
    fn failures_map_to_err_result() {
        assert!(FdbError::WriteErr.to_result().is_err());
        assert!(FdbError::InitFailed.to_result().is_err());
    }

    #[test]
    fn values_match_c_enum() {
        assert_eq!(FdbError::EraseErr as i32, 1);
        assert_eq!(FdbError::ReadErr as i32, 2);
        assert_eq!(FdbError::WriteErr as i32, 3);
        assert_eq!(FdbError::KvNameErr as i32, 4);
        assert_eq!(FdbError::KvNameExist as i32, 5);
        assert_eq!(FdbError::SavedFull as i32, 6);
        assert_eq!(FdbError::InitFailed as i32, 7);
    }
}
