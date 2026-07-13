//! FlashDB core definitions shared by KVDB and TSDB.
//!
//! Mirrors shared enums and constants from `inc/fdb_def.h`. Enum discriminants
//! are kept identical to the C enums so on-flash status bytes remain
//! compatible with the original implementation.

/// Database type, mirrors `fdb_db_type`.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DbType {
    Kv = 0,
    Ts = 1,
}

impl DbType {
    #[inline]
    pub fn is_kv(self) -> bool {
        matches!(self, DbType::Kv)
    }

    #[inline]
    pub fn is_ts(self) -> bool {
        matches!(self, DbType::Ts)
    }
}

/// KV node status, mirrors `enum fdb_kv_status`.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum KvStatus {
    Unused = 0,
    PreWrite = 1,
    Write = 2,
    PreDelete = 3,
    Deleted = 4,
    ErrHdr = 5,
}

impl KvStatus {
    pub const STATUS_NUM: u8 = 6;

    #[inline]
    pub fn is_active(self) -> bool {
        matches!(
            self,
            KvStatus::PreWrite | KvStatus::Write | KvStatus::PreDelete
        )
    }

    #[inline]
    pub fn is_readable(self) -> bool {
        matches!(self, KvStatus::Write)
    }

    #[inline]
    pub fn from_byte(byte: u8) -> Option<KvStatus> {
        match byte {
            0 => Some(KvStatus::Unused),
            1 => Some(KvStatus::PreWrite),
            2 => Some(KvStatus::Write),
            3 => Some(KvStatus::PreDelete),
            4 => Some(KvStatus::Deleted),
            5 => Some(KvStatus::ErrHdr),
            _ => None,
        }
    }
}

/// TSL (time-series log) node status, mirrors `enum fdb_tsl_status`.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TslStatus {
    Unused = 0,
    PreWrite = 1,
    Write = 2,
    UserStatus1 = 3,
    Deleted = 4,
    UserStatus2 = 5,
}

impl TslStatus {
    pub const STATUS_NUM: u8 = 6;

    #[inline]
    pub fn is_readable(self) -> bool {
        matches!(self, TslStatus::Write)
    }

    #[inline]
    pub fn is_user(self) -> bool {
        matches!(self, TslStatus::UserStatus1 | TslStatus::UserStatus2)
    }

    #[inline]
    pub fn from_byte(byte: u8) -> Option<TslStatus> {
        match byte {
            0 => Some(TslStatus::Unused),
            1 => Some(TslStatus::PreWrite),
            2 => Some(TslStatus::Write),
            3 => Some(TslStatus::UserStatus1),
            4 => Some(TslStatus::Deleted),
            5 => Some(TslStatus::UserStatus2),
            _ => None,
        }
    }
}

use std::path::PathBuf;

pub const KV_NAME_MAX: usize = 64;
pub const KV_CACHE_TABLE_SIZE: usize = 64;
pub const SECTOR_CACHE_TABLE_SIZE: usize = 8;
pub const FILE_CACHE_TABLE_SIZE: usize = 2;
pub const WRITE_GRAN: u32 = 1;

pub const SEC_MAGIC: u32 = 0x3042_4446;
pub const KV_MAGIC: u32 = 0x3030_564B;
pub const COMBINED_NONE: u32 = 0xFFFF_FFFF;
pub const TIME_UNUSED: FdbTime = -1;

pub type FdbTime = i64;
pub type GetTimeFn = fn() -> FdbTime;

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SectorStoreStatus {
    Unused = 0,
    Empty = 1,
    Using = 2,
    Full = 3,
}

impl SectorStoreStatus {
    pub const STATUS_NUM: u8 = 4;

    pub fn from_byte(byte: u8) -> Option<SectorStoreStatus> {
        match byte {
            0 => Some(SectorStoreStatus::Unused),
            1 => Some(SectorStoreStatus::Empty),
            2 => Some(SectorStoreStatus::Using),
            3 => Some(SectorStoreStatus::Full),
            _ => None,
        }
    }
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SectorDirtyStatus {
    Unused = 0,
    False = 1,
    True = 2,
    Gc = 3,
}

impl SectorDirtyStatus {
    pub const STATUS_NUM: u8 = 4;

    pub fn from_byte(byte: u8) -> Option<SectorDirtyStatus> {
        match byte {
            0 => Some(SectorDirtyStatus::Unused),
            1 => Some(SectorDirtyStatus::False),
            2 => Some(SectorDirtyStatus::True),
            3 => Some(SectorDirtyStatus::Gc),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct DefaultKvNode {
    pub key: String,
    pub value: Vec<u8>,
}

#[derive(Debug, Clone, Default)]
pub struct DefaultKv {
    pub kvs: Vec<DefaultKvNode>,
}

impl DefaultKv {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn from_pairs<I, S>(pairs: I) -> Self
    where
        I: IntoIterator<Item = (S, Vec<u8>)>,
        S: Into<String>,
    {
        let kvs = pairs
            .into_iter()
            .map(|(k, v)| DefaultKvNode {
                key: k.into(),
                value: v,
            })
            .collect();
        Self { kvs }
    }
}

pub struct FdbDb {
    pub name: String,
    pub type_: DbType,
    pub dir: PathBuf,
    pub sec_size: u32,
    pub max_size: u32,
    pub oldest_addr: u32,
    pub init_ok: bool,
    pub file_mode: bool,
    pub not_formatable: bool,
    pub lock: Option<Box<dyn FnMut() + Send>>,
    pub unlock: Option<Box<dyn FnMut() + Send>>,
    pub user_data: Option<Box<dyn std::any::Any + Send>>,
}

impl std::fmt::Debug for FdbDb {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FdbDb")
            .field("name", &self.name)
            .field("type_", &self.type_)
            .field("dir", &self.dir)
            .field("sec_size", &self.sec_size)
            .field("max_size", &self.max_size)
            .field("oldest_addr", &self.oldest_addr)
            .field("init_ok", &self.init_ok)
            .field("file_mode", &self.file_mode)
            .field("not_formatable", &self.not_formatable)
            .field("has_lock", &self.lock.is_some())
            .field("has_unlock", &self.unlock.is_some())
            .field("has_user_data", &self.user_data.is_some())
            .finish()
    }
}

impl FdbDb {
    pub fn new(name: impl Into<String>, type_: DbType, dir: impl Into<PathBuf>) -> Self {
        FdbDb {
            name: name.into(),
            type_,
            dir: dir.into(),
            sec_size: 0,
            max_size: 0,
            oldest_addr: 0,
            init_ok: false,
            file_mode: false,
            not_formatable: false,
            lock: None,
            unlock: None,
            user_data: None,
        }
    }

    pub fn run_lock(&mut self) {
        if let Some(lock) = self.lock.as_mut() {
            lock();
        }
    }

    pub fn run_unlock(&mut self) {
        if let Some(unlock) = self.unlock.as_mut() {
            unlock();
        }
    }
}

impl Default for FdbDb {
    fn default() -> Self {
        FdbDb::new("", DbType::Kv, PathBuf::new())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn db_type_values_match_c_enum() {
        assert_eq!(DbType::Kv as u8, 0);
        assert_eq!(DbType::Ts as u8, 1);
        assert!(DbType::Kv.is_kv());
        assert!(DbType::Ts.is_ts());
    }

    #[test]
    fn kv_status_round_trips_through_byte() {
        for raw in 0..KvStatus::STATUS_NUM {
            let status = KvStatus::from_byte(raw).expect("in-range byte");
            assert_eq!(status as u8, raw);
        }
        assert!(KvStatus::from_byte(KvStatus::STATUS_NUM).is_none());
    }

    #[test]
    fn tsl_status_round_trips_through_byte() {
        for raw in 0..TslStatus::STATUS_NUM {
            let status = TslStatus::from_byte(raw).expect("in-range byte");
            assert_eq!(status as u8, raw);
        }
        assert!(TslStatus::from_byte(TslStatus::STATUS_NUM).is_none());
    }

    #[test]
    fn write_statuses_are_readable() {
        assert!(KvStatus::Write.is_readable());
        assert!(TslStatus::Write.is_readable());
        assert!(!KvStatus::Deleted.is_readable());
        assert!(!TslStatus::Unused.is_readable());
    }
}
