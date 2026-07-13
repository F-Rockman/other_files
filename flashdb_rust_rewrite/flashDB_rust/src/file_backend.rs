//! File-backed storage backend, mirrors `src/fdb_file.c` (POSIX mode).
//!
//! Each sector is stored as a separate file named `{dir}/{db_name}.fdb.{index}`
//! where `index = align_down(addr, sec_size) / sec_size`.  Reads and writes
//! seek to `addr % sec_size` within the file.  Erase truncates the file and
//! fills it with `0xFF` (erased flash state).

use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;

use crate::def::FdbDb;
use crate::error::FdbError;
use crate::low_lvl::{align_down, BYTE_ERASED};

const ERASE_BUF_SIZE: usize = 32;

#[derive(Debug, Clone)]
pub struct FileBackend {
    dir: PathBuf,
    name: String,
    sec_size: u32,
}

impl FileBackend {
    pub fn new(dir: impl Into<PathBuf>, name: impl Into<String>, sec_size: u32) -> Self {
        FileBackend {
            dir: dir.into(),
            name: name.into(),
            sec_size,
        }
    }

    pub fn dir(&self) -> &std::path::Path {
        &self.dir
    }

    pub fn sec_size(&self) -> u32 {
        self.sec_size
    }

    pub fn file_path(&self, addr: u32) -> PathBuf {
        let sec_addr = align_down(addr, self.sec_size);
        let index = sec_addr / self.sec_size;
        let file_name = format!("{}.fdb.{}", self.name, index);
        self.dir.join(file_name)
    }

    pub fn read(&self, addr: u32, buf: &mut [u8]) -> Result<(), FdbError> {
        let offset = (addr % self.sec_size) as u64;
        let path = self.file_path(addr);
        let mut file = File::open(&path).map_err(|_| FdbError::ReadErr)?;
        file.seek(SeekFrom::Start(offset))
            .map_err(|_| FdbError::ReadErr)?;
        file.read_exact(buf).map_err(|_| FdbError::ReadErr)?;
        Ok(())
    }

    pub fn write(&self, addr: u32, data: &[u8], sync: bool) -> Result<(), FdbError> {
        let offset = (addr % self.sec_size) as u64;
        let path = self.file_path(addr);
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .open(&path)
            .map_err(|_| FdbError::WriteErr)?;
        file.seek(SeekFrom::Start(offset))
            .map_err(|_| FdbError::WriteErr)?;
        file.write_all(data).map_err(|_| FdbError::WriteErr)?;
        if sync {
            file.sync_all().map_err(|_| FdbError::WriteErr)?;
        }
        Ok(())
    }

    pub fn erase(&self, addr: u32, size: usize) -> Result<(), FdbError> {
        let path = self.file_path(addr);
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(&path)
            .map_err(|_| FdbError::EraseErr)?;

        let mut buf = [BYTE_ERASED; ERASE_BUF_SIZE];
        let mut remaining = size;
        while remaining >= ERASE_BUF_SIZE {
            file.write_all(&buf).map_err(|_| FdbError::EraseErr)?;
            remaining -= ERASE_BUF_SIZE;
        }
        if remaining > 0 {
            buf[..remaining].fill(BYTE_ERASED);
            file.write_all(&buf[..remaining])
                .map_err(|_| FdbError::EraseErr)?;
        }
        file.sync_all().map_err(|_| FdbError::EraseErr)?;
        Ok(())
    }

    pub fn write_aligned(
        &self,
        addr: u32,
        data: &[u8],
        sync: bool,
    ) -> Result<(), FdbError> {
        let aligned_len = crate::low_lvl::wg_align(data.len() as u32) as usize;
        if aligned_len == data.len() {
            self.write(addr, data, sync)
        } else {
            let mut padded = Vec::with_capacity(aligned_len);
            padded.extend_from_slice(data);
            padded.resize(aligned_len, 0);
            self.write(addr, &padded, sync)
        }
    }
}

pub fn file_read(db: &FdbDb, addr: u32, buf: &mut [u8]) -> FdbError {
    let backend = FileBackend::new(&db.dir, &db.name, db.sec_size);
    match backend.read(addr, buf) {
        Ok(()) => FdbError::NoErr,
        Err(e) => e,
    }
}

pub fn file_write(db: &FdbDb, addr: u32, buf: &[u8], sync: bool) -> FdbError {
    let backend = FileBackend::new(&db.dir, &db.name, db.sec_size);
    match backend.write(addr, buf, sync) {
        Ok(()) => FdbError::NoErr,
        Err(e) => e,
    }
}

pub fn file_erase(db: &FdbDb, addr: u32, size: usize) -> FdbError {
    let backend = FileBackend::new(&db.dir, &db.name, db.sec_size);
    match backend.erase(addr, size) {
        Ok(()) => FdbError::NoErr,
        Err(e) => e,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn make_backend(dir: &std::path::Path) -> FileBackend {
        FileBackend::new(dir, "testdb", 4096)
    }

    #[test]
    fn file_path_uses_sector_index() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        let path0 = backend.file_path(0);
        let path1 = backend.file_path(4096);
        let path2 = backend.file_path(8192);
        assert_eq!(path0.file_name().unwrap(), "testdb.fdb.0");
        assert_eq!(path1.file_name().unwrap(), "testdb.fdb.1");
        assert_eq!(path2.file_name().unwrap(), "testdb.fdb.2");
    }

    #[test]
    fn file_path_aligns_down_to_sector() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        let path_mid = backend.file_path(2048);
        let path_start = backend.file_path(0);
        assert_eq!(path_mid, path_start);
    }

    #[test]
    fn write_then_read_round_trips() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        let data = b"hello flashdb";
        backend.write(0, data, true).unwrap();
        let mut buf = vec![0u8; data.len()];
        backend.read(0, &mut buf).unwrap();
        assert_eq!(&buf, data);
    }

    #[test]
    fn write_at_offset_within_sector() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        backend.write(100, b"world", true).unwrap();
        let mut buf = vec![0u8; 5];
        backend.read(100, &mut buf).unwrap();
        assert_eq!(&buf, b"world");
    }

    #[test]
    fn erase_fills_with_0xff() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        backend.write(0, b"some data", true).unwrap();
        backend.erase(0, 64).unwrap();
        let mut buf = vec![0u8; 64];
        backend.read(0, &mut buf).unwrap();
        assert!(buf.iter().all(|&b| b == BYTE_ERASED));
    }

    #[test]
    fn erase_writes_exact_size() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        backend.erase(0, 100).unwrap();
        let metadata = std::fs::metadata(backend.file_path(0)).unwrap();
        assert_eq!(metadata.len(), 100);
    }

    #[test]
    fn write_across_sectors_uses_different_files() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        backend.write(0, b"sector0", true).unwrap();
        backend.write(4096, b"sector1", true).unwrap();
        let mut buf0 = vec![0u8; 7];
        let mut buf1 = vec![0u8; 7];
        backend.read(0, &mut buf0).unwrap();
        backend.read(4096, &mut buf1).unwrap();
        assert_eq!(&buf0, b"sector0");
        assert_eq!(&buf1, b"sector1");
    }

    #[test]
    fn read_nonexistent_file_returns_read_err() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        let mut buf = [0u8; 4];
        let result = backend.read(0, &mut buf);
        assert!(matches!(result, Err(FdbError::ReadErr)));
    }

    #[test]
    fn write_aligned_pads_to_granularity() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        let data = b"abc";
        backend.write_aligned(0, data, true).unwrap();
        let aligned_len = crate::low_lvl::wg_align(data.len() as u32) as usize;
        let mut buf = vec![0u8; aligned_len];
        backend.read(0, &mut buf).unwrap();
        assert_eq!(&buf[..data.len()], data);
    }

    #[test]
    fn sync_flag_does_not_error() {
        let dir = tempdir().unwrap();
        let backend = make_backend(dir.path());
        backend.write(0, b"synced", true).unwrap();
        backend.write(0, b"nosync", false).unwrap();
        let mut buf = vec![0u8; 6];
        backend.read(0, &mut buf).unwrap();
        assert_eq!(&buf, b"nosync");
    }
}
