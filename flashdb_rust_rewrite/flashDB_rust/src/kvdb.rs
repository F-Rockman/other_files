use crate::def::{KvStatus, SectorStoreStatus, SectorDirtyStatus, COMBINED_NONE};

pub const KV_NAME_MAX: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct KvAddr {
    pub start: u32,
    pub value: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct KvNode {
    pub status: KvStatus,
    pub crc_is_ok: bool,
    pub name_len: u8,
    pub magic: u32,
    pub len: u32,
    pub value_len: u32,
    pub name: [u8; KV_NAME_MAX],
    pub addr: KvAddr,
}

impl KvNode {
    pub fn name_str(&self) -> &str {
        let len = self.name_len as usize;
        if len > KV_NAME_MAX {
            return "";
        }
        std::str::from_utf8(&self.name[..len]).unwrap_or("")
    }

    pub fn set_name(&mut self, name: &str) {
        let bytes = name.as_bytes();
        let len = bytes.len().min(KV_NAME_MAX);
        self.name.fill(0);
        self.name[..len].copy_from_slice(&bytes[..len]);
        self.name_len = len as u8;
    }

    pub fn new() -> Self {
        KvNode {
            status: KvStatus::Unused,
            crc_is_ok: false,
            name_len: 0,
            magic: 0,
            len: 0,
            value_len: 0,
            name: [0u8; KV_NAME_MAX],
            addr: KvAddr { start: 0, value: 0 },
        }
    }
}

impl Default for KvNode {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct KvdbSecStatus {
    pub store: SectorStoreStatus,
    pub dirty: SectorDirtyStatus,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct KvdbSecInfo {
    pub check_ok: bool,
    pub status: KvdbSecStatus,
    pub addr: u32,
    pub magic: u32,
    pub combined: u32,
    pub remain: usize,
    pub empty_kv: u32,
}

impl KvdbSecInfo {
    pub fn new() -> Self {
        KvdbSecInfo {
            check_ok: false,
            status: KvdbSecStatus {
                store: SectorStoreStatus::Unused,
                dirty: SectorDirtyStatus::Unused,
            },
            addr: 0,
            magic: 0,
            combined: COMBINED_NONE,
            remain: 0,
            empty_kv: 0,
        }
    }
}

impl Default for KvdbSecInfo {
    fn default() -> Self {
        Self::new()
    }
}

pub const KV_STATUS_TABLE_SIZE: usize = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct KvHdrData {
    pub status_table: [u8; KV_STATUS_TABLE_SIZE],
    pub magic: u32,
    pub len: u32,
    pub crc32: u32,
    pub name_len: u8,
    pub value_len: u32,
}

impl KvHdrData {
    pub fn new() -> Self {
        KvHdrData {
            status_table: [0u8; KV_STATUS_TABLE_SIZE],
            magic: 0,
            len: 0,
            crc32: 0,
            name_len: 0,
            value_len: 0,
        }
    }
}

impl Default for KvHdrData {
    fn default() -> Self {
        Self::new()
    }
}

use crate::def::{DefaultKv, FdbDb, KV_MAGIC, SEC_MAGIC};
use crate::error::FdbError;
use crate::file_backend::{file_read, file_write, file_erase};
use crate::low_lvl::{FAILED_ADDR, wg_align, get_status, write_status_to_flash, set_status, align_down, BYTE_ERASED, DATA_UNUSED};
use crate::utils::{calc_crc32, continue_ff_addr};

const KV_MAGIC_OFFSET: u32 = 4;
const SECTOR_HDR_DATA_SIZE: u32 = 16;
const KV_HDR_DATA_SIZE: u32 = 24;
const SECTOR_COMBINED: u32 = 0x0000_0000;
const SECTOR_DIRTY_OFFSET: u32 = 1;

pub fn find_next_kv_addr(db: &Kvdb, start: u32, end: u32) -> u32 {
    let mut buf = [0u8; 32];
    let start_bak = start;
    let mut cur = start;

    while cur < end && cur + 32 < end {
        if file_read(&db.parent, cur, &mut buf).is_err() {
            return FAILED_ADDR;
        }
        let mut i: usize = 0;
        while i < 28 && cur + (i as u32) < end {
            let magic = buf[i] as u32
                | ((buf[i + 1] as u32) << 8)
                | ((buf[i + 2] as u32) << 16)
                | ((buf[i + 3] as u32) << 24);
            if magic == KV_MAGIC && cur + i as u32 >= start_bak + KV_MAGIC_OFFSET {
                return cur + i as u32 - KV_MAGIC_OFFSET;
            }
            i += 1;
        }
        cur += 28;
    }

    FAILED_ADDR
}

pub fn get_next_kv_addr(db: &Kvdb, sector: &KvdbSecInfo, pre_kv: &KvNode) -> u32 {
    if sector.status.store == SectorStoreStatus::Empty {
        return FAILED_ADDR;
    }

    if pre_kv.addr.start == FAILED_ADDR {
        return sector.addr + SECTOR_HDR_DATA_SIZE;
    }

    if pre_kv.addr.start > sector.addr + db.parent.sec_size {
        return FAILED_ADDR;
    }

    let addr = if pre_kv.crc_is_ok {
        pre_kv.addr.start + pre_kv.len
    } else {
        pre_kv.addr.start + wg_align(1)
    };

    let addr = find_next_kv_addr(
        db,
        addr,
        sector.addr + db.parent.sec_size - SECTOR_HDR_DATA_SIZE,
    );

    if addr == FAILED_ADDR
        || addr > sector.addr + db.parent.sec_size
        || pre_kv.len == 0
    {
        FAILED_ADDR
    } else {
        addr
    }
}

pub fn read_kv(db: &mut Kvdb, kv: &mut KvNode) -> FdbError {
    let mut hdr_buf = [0u8; KV_HDR_DATA_SIZE as usize];
    if file_read(&db.parent, kv.addr.start, &mut hdr_buf).is_err() {
        kv.crc_is_ok = false;
        return FdbError::ReadErr;
    }

    let status_byte = hdr_buf[0];
    let status_idx = get_status(&[status_byte], KvStatus::STATUS_NUM as u32);
    kv.status = KvStatus::from_byte(status_idx as u8).unwrap_or(KvStatus::ErrHdr);
    kv.len = u32::from_le_bytes([hdr_buf[8], hdr_buf[9], hdr_buf[10], hdr_buf[11]]);

    let hdr_crc32 = u32::from_le_bytes([hdr_buf[12], hdr_buf[13], hdr_buf[14], hdr_buf[15]]);
    let hdr_name_len = hdr_buf[16];
    let hdr_value_len = u32::from_le_bytes([hdr_buf[20], hdr_buf[21], hdr_buf[22], hdr_buf[23]]);

    if kv.len == u32::MAX || kv.len > db.parent.max_size || kv.len < KV_HDR_DATA_SIZE {
        kv.len = KV_HDR_DATA_SIZE;
        if kv.status != KvStatus::ErrHdr {
            kv.status = KvStatus::ErrHdr;
            let mut st = [status_byte];
            let _ = write_status_to_flash(
                &mut st,
                KvStatus::STATUS_NUM as u32,
                KvStatus::ErrHdr as u32,
                kv.addr.start,
                |off, byte| {
                    let b = [byte];
                    let r = file_write(&db.parent, kv.addr.start + off, &b, true);
                    if r.is_err() { Err(r) } else { Ok(()) }
                },
            );
        }
        kv.crc_is_ok = false;
        return FdbError::ReadErr;
    }

    let mut calc_crc = calc_crc32(0, &hdr_buf[16..20]);
    calc_crc = calc_crc32(calc_crc, &hdr_buf[20..24]);

    let crc_data_len = (kv.len - KV_HDR_DATA_SIZE) as usize;
    let mut buf = [0u8; 32];
    let mut len = 0usize;
    while len < crc_data_len {
        let size = if len + 32 < crc_data_len { 32 } else { crc_data_len - len };
        let read_size = wg_align(size as u32) as usize;
        let read_addr = kv.addr.start + KV_HDR_DATA_SIZE + len as u32;
        if file_read(&db.parent, read_addr, &mut buf[..read_size]).is_err() {
            kv.crc_is_ok = false;
            return FdbError::ReadErr;
        }
        calc_crc = calc_crc32(calc_crc, &buf[..size]);
        len += size;
    }

    if calc_crc != hdr_crc32 {
        kv.crc_is_ok = false;
        let name_len = hdr_name_len.min(KV_NAME_MAX as u8) as usize;
        let name_addr = kv.addr.start + KV_HDR_DATA_SIZE;
        let read_len = wg_align(name_len as u32) as usize;
        kv.name.fill(0);
        let _ = file_read(&db.parent, name_addr, &mut kv.name[..read_len]);
        FdbError::ReadErr
    } else {
        kv.crc_is_ok = true;
        let name_addr = kv.addr.start + KV_HDR_DATA_SIZE;
        let read_len = wg_align(hdr_name_len as u32) as usize;
        kv.name.fill(0);
        let _ = file_read(&db.parent, name_addr, &mut kv.name[..read_len]);
        kv.addr.value = name_addr + wg_align(hdr_name_len as u32);
        kv.value_len = hdr_value_len;
        kv.name_len = hdr_name_len;
        if kv.name_len as usize >= KV_NAME_MAX {
            kv.name[KV_NAME_MAX - 1] = 0;
        } else {
            kv.name[kv.name_len as usize] = 0;
        }
        FdbError::NoErr
    }
}

pub fn read_sector_info(db: &mut Kvdb, addr: u32, sector: &mut KvdbSecInfo, traversal: bool) -> FdbError {
    assert!(addr % db.parent.sec_size == 0);

    if let Some(cached) = get_sector_from_cache(db, addr) {
        if !traversal || (traversal && cached.empty_kv != FAILED_ADDR) {
            *sector = cached;
            return FdbError::NoErr;
        }
    }

    let mut sec_hdr = [0u8; SECTOR_HDR_DATA_SIZE as usize];
    let _ = file_read(&db.parent, addr, &mut sec_hdr);

    sector.status.store = SectorStoreStatus::Unused;
    sector.status.dirty = SectorDirtyStatus::Unused;
    sector.addr = addr;
    sector.magic = u32::from_le_bytes([sec_hdr[4], sec_hdr[5], sec_hdr[6], sec_hdr[7]]);

    if sector.magic != SEC_MAGIC {
        sector.check_ok = false;
        sector.combined = COMBINED_NONE;
        return FdbError::InitFailed;
    }

    let combined_val = u32::from_le_bytes([sec_hdr[8], sec_hdr[9], sec_hdr[10], sec_hdr[11]]);
    if combined_val != COMBINED_NONE && combined_val != SECTOR_COMBINED {
        sector.check_ok = false;
        sector.combined = COMBINED_NONE;
        return FdbError::InitFailed;
    }

    sector.check_ok = true;
    sector.combined = combined_val;

    let store_byte = sec_hdr[0];
    let dirty_byte = sec_hdr[1];
    sector.status.store = SectorStoreStatus::from_byte(
        get_status(&[store_byte], SectorStoreStatus::STATUS_NUM as u32) as u8
    ).unwrap_or(SectorStoreStatus::Unused);
    sector.status.dirty = SectorDirtyStatus::from_byte(
        get_status(&[dirty_byte], SectorDirtyStatus::STATUS_NUM as u32) as u8
    ).unwrap_or(SectorDirtyStatus::Unused);

    if traversal {
        sector.remain = 0;
        sector.empty_kv = sector.addr + SECTOR_HDR_DATA_SIZE;

        if sector.status.store == SectorStoreStatus::Empty {
            sector.remain = (db.parent.sec_size - SECTOR_HDR_DATA_SIZE) as usize;
        } else if sector.status.store == SectorStoreStatus::Using {
            sector.remain = (db.parent.sec_size - SECTOR_HDR_DATA_SIZE) as usize;
            let mut kv_obj = KvNode::new();
            kv_obj.addr.start = sector.addr + SECTOR_HDR_DATA_SIZE;

            loop {
                let res = read_kv(db, &mut kv_obj);

                if !kv_obj.crc_is_ok {
                    if kv_obj.status != KvStatus::PreWrite && kv_obj.status != KvStatus::ErrHdr {
                        sector.remain = 0;
                        if res.is_err() {
                            update_sector_cache(db, sector);
                            return FdbError::ReadErr;
                        }
                    }
                }
                sector.empty_kv += kv_obj.len;
                if sector.remain >= kv_obj.len as usize {
                    sector.remain -= kv_obj.len as usize;
                } else {
                    sector.remain = 0;
                }

                let sec_for_next = KvdbSecInfo {
                    check_ok: sector.check_ok,
                    status: sector.status,
                    addr: sector.addr,
                    magic: sector.magic,
                    combined: sector.combined,
                    remain: sector.remain,
                    empty_kv: sector.empty_kv,
                };
                let next_addr = get_next_kv_addr(db, &sec_for_next, &kv_obj);
                if next_addr == FAILED_ADDR {
                    break;
                }
                kv_obj.addr.start = next_addr;
            }

            let ff_addr = continue_ff_addr(&db.parent, sector.empty_kv, sector.addr + db.parent.sec_size);
            if sector.empty_kv != ff_addr {
                sector.empty_kv = ff_addr;
                sector.remain = (db.parent.sec_size as usize).saturating_sub((ff_addr - sector.addr) as usize);
            }
        }

        update_sector_cache(db, sector);
    } else {
        let cached = get_sector_from_cache(db, sector.addr);
        if cached.is_none() {
            sector.empty_kv = FAILED_ADDR;
            sector.remain = 0;
            update_sector_cache(db, sector);
        }
    }

    FdbError::NoErr
}

pub fn get_next_sector_addr(db: &Kvdb, pre_sec: &KvdbSecInfo, traversed_len: u32) -> u32 {
    let cur_block_size = if pre_sec.combined == COMBINED_NONE {
        db.parent.sec_size
    } else {
        pre_sec.combined * db.parent.sec_size
    };

    if traversed_len + cur_block_size <= db.parent.max_size {
        if pre_sec.addr + cur_block_size < db.parent.max_size {
            pre_sec.addr + cur_block_size
        } else {
            0
        }
    } else {
        FAILED_ADDR
    }
}

fn get_sector_from_cache(db: &Kvdb, addr: u32) -> Option<KvdbSecInfo> {
    for cached in &db.sector_cache_table {
        if cached.addr == addr && cached.check_ok {
            return Some(*cached);
        }
    }
    None
}

fn update_sector_cache(db: &mut Kvdb, sector: &KvdbSecInfo) {
    for cached in &mut db.sector_cache_table {
        if cached.addr == sector.addr {
            *cached = *sector;
            return;
        }
    }
    for cached in &mut db.sector_cache_table {
        if !cached.check_ok {
            *cached = *sector;
            return;
        }
    }
}

pub fn update_kv_cache(db: &mut Kvdb, name: &[u8], addr: u32) {
    let name_crc = (calc_crc32(0, name) >> 16) as u16;
    let mut empty_index: Option<usize> = None;
    let mut min_activity_index: Option<usize> = None;
    let mut min_activity: u16 = 0xFFFF;

    for i in 0..KV_CACHE_TABLE_SIZE {
        let entry = &mut db.kv_cache_table[i];
        if addr != crate::low_lvl::DATA_UNUSED {
            if entry.name_crc == name_crc {
                entry.addr = addr;
                return;
            } else if entry.addr == crate::low_lvl::DATA_UNUSED && empty_index.is_none() {
                empty_index = Some(i);
            } else if entry.addr != crate::low_lvl::DATA_UNUSED {
                if entry.active > 0 {
                    entry.active -= 1;
                }
                if entry.active < min_activity {
                    min_activity_index = Some(i);
                    min_activity = entry.active;
                }
            }
        } else if entry.name_crc == name_crc {
            entry.addr = crate::low_lvl::DATA_UNUSED;
            entry.active = 0;
            return;
        }
    }

    let target = empty_index.or(min_activity_index);
    if let Some(idx) = target {
        db.kv_cache_table[idx].addr = addr;
        db.kv_cache_table[idx].name_crc = name_crc;
        db.kv_cache_table[idx].active = KV_CACHE_TABLE_SIZE as u16;
    }
}

pub fn get_kv_from_cache(db: &mut Kvdb, name: &[u8]) -> Option<u32> {
    let name_crc = (calc_crc32(0, name) >> 16) as u16;

    for i in 0..KV_CACHE_TABLE_SIZE {
        let (entry_addr, entry_name_crc, entry_active) = {
            let entry = &db.kv_cache_table[i];
            (entry.addr, entry.name_crc, entry.active)
        };
        if entry_addr != crate::low_lvl::DATA_UNUSED && entry_name_crc == name_crc {
            let mut saved_name = [0u8; KV_NAME_MAX];
            let read_addr = entry_addr + KV_HDR_DATA_SIZE;
            let _ = file_read(&db.parent, read_addr, &mut saved_name);
            if name.len() <= KV_NAME_MAX && &saved_name[..name.len()] == name {
                let new_active = if entry_active >= 0xFFFF - KV_CACHE_TABLE_SIZE as u16 {
                    0xFFFF
                } else {
                    entry_active + KV_CACHE_TABLE_SIZE as u16
                };
                db.kv_cache_table[i].active = new_active;
                return Some(entry_addr);
            }
        }
    }

    None
}

pub fn kv_iterator<F>(db: &mut Kvdb, kv: &mut KvNode, mut callback: F)
where
    F: FnMut(&KvNode) -> bool,
{
    let mut sec_addr = db.parent.oldest_addr;
    let mut traversed_len: u32 = 0;

    loop {
        traversed_len += db.parent.sec_size;
        let mut sector = KvdbSecInfo::new();
        if read_sector_info(db, sec_addr, &mut sector, false) != FdbError::NoErr {
            sec_addr = get_next_sector_addr(db, &sector, traversed_len);
            if sec_addr == FAILED_ADDR {
                break;
            }
            continue;
        }
        if sector.status.store == SectorStoreStatus::Using
            || sector.status.store == SectorStoreStatus::Full
        {
            kv.addr.start = sector.addr + SECTOR_HDR_DATA_SIZE;
            loop {
                read_kv(db, kv);
                if callback(kv) {
                    return;
                }
                let next = get_next_kv_addr(db, &sector, kv);
                if next == FAILED_ADDR {
                    break;
                }
                kv.addr.start = next;
            }
        }
        sec_addr = get_next_sector_addr(db, &sector, traversed_len);
        if sec_addr == FAILED_ADDR {
            break;
        }
    }
}

pub fn find_kv_no_cache(db: &mut Kvdb, key: &[u8], kv: &mut KvNode) -> bool {
    let mut find_ok = false;
    let key_owned: Vec<u8> = key.to_vec();
    kv_iterator(db, kv, |cur_kv| {
        if key_owned.len() != cur_kv.name_len as usize {
            return false;
        }
        if cur_kv.crc_is_ok
            && cur_kv.status == KvStatus::Write
            && &cur_kv.name[..key_owned.len()] == &key_owned[..]
        {
            find_ok = true;
            return true;
        }
        false
    });
    find_ok
}

pub fn find_kv(db: &mut Kvdb, key: &[u8], kv: &mut KvNode) -> bool {
    if let Some(addr) = get_kv_from_cache(db, key) {
        kv.addr.start = addr;
        read_kv(db, kv);
        return true;
    }

    let find_ok = find_kv_no_cache(db, key, kv);

    if find_ok {
        update_kv_cache(db, key, kv.addr.start);
    }

    find_ok
}

pub fn fdb_is_str(value: &[u8]) -> bool {
    value.iter().all(|&ch| ch.wrapping_sub(b' ') < 127u8 - b' ')
}

pub fn fdb_kv_to_blob(kv: &KvNode, blob: &mut crate::blob::Blob) {
    blob.saved.meta_addr = kv.addr.start;
    blob.saved.addr = kv.addr.value;
    blob.saved.len = kv.value_len as usize;
}

pub fn get_kv(db: &mut Kvdb, key: &[u8], value_buf: Option<&mut [u8]>, value_len: Option<&mut usize>) -> usize {
    let mut kv = KvNode::new();
    let mut read_len: usize = 0;

    if find_kv(db, key, &mut kv) {
        if let Some(out_len) = value_len {
            *out_len = kv.value_len as usize;
        }
        let vlen = kv.value_len as usize;
        read_len = if value_buf.as_deref().map_or(0, |b| b.len()) > vlen {
            vlen
        } else {
            value_buf.as_deref().map_or(0, |b| b.len())
        };
        if let Some(buf) = value_buf {
            let buf_slice = &mut buf[..read_len];
            let _ = file_read(&db.parent, kv.addr.value, buf_slice);
        }
    } else if let Some(out_len) = value_len {
        *out_len = 0;
    }

    read_len
}

pub fn fdb_kv_get_obj(db: &mut Kvdb, key: &[u8], kv: &mut KvNode) -> bool {
    if !db.parent.init_ok {
        return false;
    }

    db.parent.run_lock();
    let find_ok = find_kv(db, key, kv);
    db.parent.run_unlock();

    find_ok
}

pub fn fdb_kv_get_blob(db: &mut Kvdb, key: &[u8], blob: &mut crate::blob::Blob) -> usize {
    if !db.parent.init_ok {
        return 0;
    }

    db.parent.run_lock();
    let buf_len = blob.buf.len();
    let read_len = {
        let buf = if buf_len > 0 {
            Some(&mut blob.buf[..])
        } else {
            None
        };
        get_kv(db, key, buf, Some(&mut blob.saved.len))
    };
    db.parent.run_unlock();

    read_len
}

pub const STR_KV_VALUE_MAX_SIZE: usize = 128;

pub fn fdb_kv_get(db: &mut Kvdb, key: &[u8]) -> Option<Vec<u8>> {
    let value = vec![0u8; STR_KV_VALUE_MAX_SIZE];
    let mut blob = crate::blob::Blob::make(value, STR_KV_VALUE_MAX_SIZE);

    let get_size = fdb_kv_get_blob(db, key, &mut blob);
    if get_size > 0 {
        let mut value = blob.buf;
        value.truncate(get_size);
        if fdb_is_str(&value) {
            return Some(value);
        } else if blob.saved.len > STR_KV_VALUE_MAX_SIZE {
            return None;
        } else {
            return None;
        }
    }

    None
}

pub fn write_kv_hdr(db: &mut Kvdb, addr: u32, kv_hdr: &KvHdrData) -> FdbError {
    let mut status_table = kv_hdr.status_table;
    let result = write_status_to_flash(
        &mut status_table,
        KvStatus::STATUS_NUM as u32,
        KvStatus::PreWrite as u32,
        addr,
        |offset, byte| {
            if file_write(&db.parent, offset, &[byte], false) == FdbError::NoErr {
                Ok(())
            } else {
                Err(FdbError::WriteErr)
            }
        },
    );
    if result.is_err() {
        return FdbError::WriteErr;
    }

    let mut rest = [0u8; (KV_HDR_DATA_SIZE - KV_MAGIC_OFFSET) as usize];
    rest[0..4].copy_from_slice(&kv_hdr.magic.to_le_bytes());
    rest[4..8].copy_from_slice(&kv_hdr.len.to_le_bytes());
    rest[8..12].copy_from_slice(&kv_hdr.crc32.to_le_bytes());
    rest[12] = kv_hdr.name_len;
    rest[16..20].copy_from_slice(&kv_hdr.value_len.to_le_bytes());

    if file_write(&db.parent, addr + KV_MAGIC_OFFSET, &rest, false) != FdbError::NoErr {
        return FdbError::WriteErr;
    }

    FdbError::NoErr
}

pub fn format_sector(db: &mut Kvdb, addr: u32, combined_value: u32) -> FdbError {
    assert!(addr % db.parent.sec_size == 0);

    let sec_size = db.parent.sec_size;
    if file_erase(&db.parent, addr, sec_size as usize) != FdbError::NoErr {
        return FdbError::EraseErr;
    }

    let mut sec_hdr = [BYTE_ERASED; SECTOR_HDR_DATA_SIZE as usize];

    set_status(&mut sec_hdr[0..1], SectorStoreStatus::STATUS_NUM as u32, SectorStoreStatus::Empty as u32);
    set_status(&mut sec_hdr[1..2], SectorDirtyStatus::STATUS_NUM as u32, SectorDirtyStatus::False as u32);

    sec_hdr[4..8].copy_from_slice(&SEC_MAGIC.to_le_bytes());
    sec_hdr[8..12].copy_from_slice(&combined_value.to_le_bytes());
    sec_hdr[12..16].copy_from_slice(&DATA_UNUSED.to_le_bytes());

    if file_write(&db.parent, addr, &sec_hdr, true) != FdbError::NoErr {
        return FdbError::WriteErr;
    }

    let mut sector = KvdbSecInfo::new();
    sector.addr = addr;
    sector.check_ok = false;
    sector.empty_kv = FAILED_ADDR;
    update_sector_cache(db, &sector);

    FdbError::NoErr
}

pub const SEC_REMAIN_THRESHOLD: usize = KV_HDR_DATA_SIZE as usize + KV_NAME_MAX;
pub const GC_EMPTY_SEC_THRESHOLD: usize = 1;

pub fn update_sec_status(db: &mut Kvdb, sector: &mut KvdbSecInfo, new_kv_len: usize, is_full: &mut bool) -> FdbError {
    let mut status_table = [0u8; 1];

    if sector.status.store == SectorStoreStatus::Empty {
        let result = write_status_to_flash(
            &mut status_table,
            SectorStoreStatus::STATUS_NUM as u32,
            SectorStoreStatus::Using as u32,
            sector.addr,
            |offset, byte| {
                if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                    Ok(())
                } else {
                    Err(FdbError::WriteErr)
                }
            },
        );
        if result.is_err() {
            return FdbError::WriteErr;
        }
        sector.status.store = SectorStoreStatus::Using;
        update_sector_status_store_cache(db, sector.addr, SectorStoreStatus::Using);
    } else if sector.status.store == SectorStoreStatus::Using {
        if sector.remain < SEC_REMAIN_THRESHOLD
            || sector.remain.saturating_sub(new_kv_len) < SEC_REMAIN_THRESHOLD
        {
            let result = write_status_to_flash(
                &mut status_table,
                SectorStoreStatus::STATUS_NUM as u32,
                SectorStoreStatus::Full as u32,
                sector.addr,
                |offset, byte| {
                    if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                        Ok(())
                    } else {
                        Err(FdbError::WriteErr)
                    }
                },
            );
            if result.is_err() {
                return FdbError::WriteErr;
            }
            sector.status.store = SectorStoreStatus::Full;
            update_sector_status_store_cache(db, sector.addr, SectorStoreStatus::Full);
            *is_full = true;
        } else {
            *is_full = false;
        }
    }

    FdbError::NoErr
}

fn update_sector_status_store_cache(db: &mut Kvdb, addr: u32, store: SectorStoreStatus) {
    for cached in &mut db.sector_cache_table {
        if cached.addr == addr {
            cached.status.store = store;
            return;
        }
    }
}

pub fn sector_iterator<F>(
    db: &mut Kvdb,
    sector: &mut KvdbSecInfo,
    status: SectorStoreStatus,
    traversal_kv: bool,
    mut callback: F,
) where
    F: FnMut(&mut KvdbSecInfo) -> bool,
{
    let mut sec_addr = db.parent.oldest_addr;
    let mut traversed_len = 0u32;

    loop {
        traversed_len += db.parent.sec_size;
        read_sector_info(db, sec_addr, sector, false);
        if status == SectorStoreStatus::Unused || status == sector.status.store {
            if traversal_kv {
                read_sector_info(db, sec_addr, sector, true);
            }
            if callback(sector) {
                return;
            }
        }
        let next = get_next_sector_addr(db, sector, traversed_len);
        if next == FAILED_ADDR {
            break;
        }
        sec_addr = next;
    }
}

pub fn alloc_kv(db: &mut Kvdb, sector: &mut KvdbSecInfo, kv_size: usize) -> u32 {
    let mut empty_kv = FAILED_ADDR;
    let mut empty_sector: usize = 0;
    let mut using_sector: usize = 0;
    let gc_request = db.gc_request;

    sector_iterator(
        db,
        sector,
        SectorStoreStatus::Unused,
        false,
        |sec| {
            if sec.check_ok && sec.status.store == SectorStoreStatus::Empty {
                empty_sector += 1;
            } else if sec.check_ok && sec.status.store == SectorStoreStatus::Using {
                using_sector += 1;
            }
            false
        },
    );

    if using_sector > 0 {
        sector_iterator(
            db,
            sector,
            SectorStoreStatus::Using,
            true,
            |sec| {
                if sec.check_ok
                    && sec.remain > kv_size + SEC_REMAIN_THRESHOLD
                    && (sec.status.dirty == SectorDirtyStatus::False
                        || (sec.status.dirty == SectorDirtyStatus::True && !gc_request))
                {
                    empty_kv = sec.empty_kv;
                    return true;
                }
                false
            },
        );
    }

    if empty_sector > 0 && empty_kv == FAILED_ADDR {
        if empty_sector > GC_EMPTY_SEC_THRESHOLD || gc_request {
            sector_iterator(
                db,
                sector,
                SectorStoreStatus::Empty,
                true,
                |sec| {
                    if sec.check_ok
                        && sec.remain > kv_size + SEC_REMAIN_THRESHOLD
                        && (sec.status.dirty == SectorDirtyStatus::False
                            || (sec.status.dirty == SectorDirtyStatus::True && !gc_request))
                    {
                        empty_kv = sec.empty_kv;
                        return true;
                    }
                    false
                },
            );
        } else {
            db.gc_request = true;
        }
    }

    empty_kv
}

pub fn del_kv(db: &mut Kvdb, key: Option<&[u8]>, old_kv: Option<&KvNode>, complete_del: bool) -> FdbError {
    let mut kv = KvNode::new();

    if old_kv.is_none() {
        let key_bytes = match key {
            Some(k) => k,
            None => return FdbError::KvNameErr,
        };
        if !find_kv(db, key_bytes, &mut kv) {
            return FdbError::KvNameErr;
        }
    } else {
        kv = *old_kv.unwrap();
    }

    let kv_addr_start = kv.addr.start;
    let sec_size = db.parent.sec_size;
    let mut status_table = [0u8; 1];

    let mut result = FdbError::NoErr;

    if !complete_del {
        let r = write_status_to_flash(
            &mut status_table,
            KvStatus::STATUS_NUM as u32,
            KvStatus::PreDelete as u32,
            kv_addr_start,
            |offset, byte| {
                if file_write(&db.parent, offset, &[byte], false) == FdbError::NoErr {
                    Ok(())
                } else {
                    Err(FdbError::WriteErr)
                }
            },
        );
        if r.is_err() {
            result = FdbError::WriteErr;
        }
        db.last_is_complete_del = true;
    } else {
        let r = write_status_to_flash(
            &mut status_table,
            KvStatus::STATUS_NUM as u32,
            KvStatus::Deleted as u32,
            kv_addr_start,
            |offset, byte| {
                if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                    Ok(())
                } else {
                    Err(FdbError::WriteErr)
                }
            },
        );
        if r.is_err() {
            result = FdbError::WriteErr;
        }

        if !db.last_is_complete_del && result == FdbError::NoErr {
            if let Some(key_bytes) = key {
                update_kv_cache(db, key_bytes, DATA_UNUSED);
            } else {
                let name_len = (kv.name_len as usize).min(KV_NAME_MAX);
                update_kv_cache(db, &kv.name[..name_len], DATA_UNUSED);
            }
        }

        db.last_is_complete_del = false;
    }

    if result == FdbError::NoErr {
        let dirty_status_addr = align_down(kv_addr_start, sec_size) + SECTOR_DIRTY_OFFSET;
        let mut dirty_table = [0u8; 1];
        if file_read(&db.parent, dirty_status_addr, &mut dirty_table) == FdbError::NoErr {
            let dirty_status = get_status(&dirty_table, SectorDirtyStatus::STATUS_NUM as u32);
            if dirty_status == SectorDirtyStatus::False as u32 {
                let r = write_status_to_flash(
                    &mut dirty_table,
                    SectorDirtyStatus::STATUS_NUM as u32,
                    SectorDirtyStatus::True as u32,
                    dirty_status_addr,
                    |offset, byte| {
                        if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                            Ok(())
                        } else {
                            Err(FdbError::WriteErr)
                        }
                    },
                );
                if r.is_err() {
                    result = FdbError::WriteErr;
                }

                let sec_addr = align_down(kv_addr_start, sec_size);
                for cached in &mut db.sector_cache_table {
                    if cached.addr == sec_addr && cached.check_ok {
                        cached.status.dirty = SectorDirtyStatus::True;
                        break;
                    }
                }
            }
        }
    }

    result
}

fn update_sector_empty_addr_cache(db: &mut Kvdb, sec_addr: u32, empty_addr: u32) {
    let sec_size = db.parent.sec_size;
    for cached in &mut db.sector_cache_table {
        if cached.addr == sec_addr && cached.check_ok {
            cached.empty_kv = empty_addr;
            cached.remain = (sec_size as usize)
                .saturating_sub((empty_addr - sec_addr) as usize);
            return;
        }
    }
}

pub fn move_kv(db: &mut Kvdb, kv: &KvNode) -> FdbError {
    let mut result = FdbError::NoErr;
    let mut sector = KvdbSecInfo::new();

    if kv.status == KvStatus::Write {
        del_kv(db, None, Some(kv), false);
    }

    let kv_addr = alloc_kv(db, &mut sector, kv.len as usize);
    if kv_addr == FAILED_ADDR {
        return FdbError::SavedFull;
    }

    if db.in_recovery_check && kv.status == KvStatus::PreDelete {
        let name_len = (kv.name_len as usize).min(KV_NAME_MAX);
        let mut kv_bak = KvNode::new();
        if find_kv_no_cache(db, &kv.name[..name_len], &mut kv_bak) {
            del_kv(db, None, Some(kv), true);
            return FdbError::NoErr;
        }
    }

    let mut is_full = false;
    let _ = update_sec_status(db, &mut sector, kv.len as usize, &mut is_full);

    let mut status_table = [0u8; 1];
    let _ = write_status_to_flash(
        &mut status_table,
        KvStatus::STATUS_NUM as u32,
        KvStatus::PreWrite as u32,
        kv_addr,
        |offset, byte| {
            if file_write(&db.parent, offset, &[byte], false) == FdbError::NoErr {
                Ok(())
            } else {
                Err(FdbError::WriteErr)
            }
        },
    );

    let kv_len = kv.len - KV_MAGIC_OFFSET;
    let mut buf = [0u8; 32];
    let mut len: u32 = 0;
    while len < kv_len {
        let size: u32 = if len + 32 < kv_len { 32 } else { kv_len - len };
        let read_addr = kv.addr.start + KV_MAGIC_OFFSET + len;
        let write_addr = kv_addr + KV_MAGIC_OFFSET + len;
        let read_len = wg_align(size) as usize;
        if file_read(&db.parent, read_addr, &mut buf[..read_len]) != FdbError::NoErr {
            result = FdbError::ReadErr;
            break;
        }
        if file_write(&db.parent, write_addr, &buf[..size as usize], true) != FdbError::NoErr {
            result = FdbError::WriteErr;
            break;
        }
        len += size;
    }

    if result == FdbError::NoErr {
        let _ = write_status_to_flash(
            &mut status_table,
            KvStatus::STATUS_NUM as u32,
            KvStatus::Write as u32,
            kv_addr,
            |offset, byte| {
                if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                    Ok(())
                } else {
                    Err(FdbError::WriteErr)
                }
            },
        );

        let sec_addr = align_down(kv_addr, db.parent.sec_size);
        let empty_addr = kv_addr + KV_HDR_DATA_SIZE + wg_align(kv.name_len as u32) + wg_align(kv.value_len);
        update_sector_empty_addr_cache(db, sec_addr, empty_addr);
        let name_len = (kv.name_len as usize).min(KV_NAME_MAX);
        update_kv_cache(db, &kv.name[..name_len], kv_addr);
    }

    del_kv(db, None, Some(kv), true);

    result
}

pub fn gc_collect_by_free_size(db: &mut Kvdb, free_size: usize) {
    let mut sector = KvdbSecInfo::new();
    let mut empty_sec_num: usize = 0;
    let mut empty_sec_addr: u32 = 0;

    sector_iterator(
        db,
        &mut sector,
        SectorStoreStatus::Empty,
        false,
        |sec| {
            if sec.check_ok {
                empty_sec_num += 1;
                empty_sec_addr = sec.addr;
            }
            false
        },
    );

    if empty_sec_num <= GC_EMPTY_SEC_THRESHOLD {
        let mut last_gc_sec_addr: u32 = empty_sec_addr;
        let mut sec_addr = db.parent.oldest_addr;
        let mut traversed_len: u32 = 0;
        loop {
            traversed_len += db.parent.sec_size;
            read_sector_info(db, sec_addr, &mut sector, false);
            let stop = do_gc(db, &mut sector, free_size, &mut last_gc_sec_addr);
            if stop {
                break;
            }
            let next = get_next_sector_addr(db, &sector, traversed_len);
            if next == FAILED_ADDR {
                break;
            }
            sec_addr = next;
        }
    }

    db.gc_request = false;
}

fn do_gc(
    db: &mut Kvdb,
    sector: &mut KvdbSecInfo,
    setting_free_size: usize,
    last_gc_sec_addr: &mut u32,
) -> bool {
    if sector.check_ok
        && (sector.status.dirty == SectorDirtyStatus::True
            || sector.status.dirty == SectorDirtyStatus::Gc)
    {
        let mut dirty_table = [0u8; 1];
        let dirty_addr = sector.addr + SECTOR_DIRTY_OFFSET;
        let _ = write_status_to_flash(
            &mut dirty_table,
            SectorDirtyStatus::STATUS_NUM as u32,
            SectorDirtyStatus::Gc as u32,
            dirty_addr,
            |offset, byte| {
                if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                    Ok(())
                } else {
                    Err(FdbError::WriteErr)
                }
            },
        );

        let mut kv = KvNode::new();
        kv.addr.start = sector.addr + SECTOR_HDR_DATA_SIZE;
        loop {
            read_kv(db, &mut kv);
            if kv.crc_is_ok
                && (kv.status == KvStatus::Write || kv.status == KvStatus::PreDelete)
            {
                if move_kv(db, &kv) != FdbError::NoErr {
                    break;
                }
            }
            let next = get_next_kv_addr(db, sector, &kv);
            if next == FAILED_ADDR {
                break;
            }
            kv.addr.start = next;
        }

        format_sector(db, sector.addr, COMBINED_NONE);

        let prev_last_gc = *last_gc_sec_addr;
        *last_gc_sec_addr = sector.addr;
        db.parent.oldest_addr = get_next_sector_addr(db, sector, 0);

        let mut last_gc_sector = KvdbSecInfo::new();
        if read_sector_info(db, prev_last_gc, &mut last_gc_sector, true) == FdbError::NoErr {
            if last_gc_sector.remain > setting_free_size {
                return true;
            }
        }
    }

    false
}

pub fn gc_collect(db: &mut Kvdb) {
    let max_size = db.parent.max_size;
    gc_collect_by_free_size(db, max_size as usize);
}

pub fn new_kv(db: &mut Kvdb, sector: &mut KvdbSecInfo, kv_size: usize) -> u32 {
    let mut already_gc = false;

    loop {
        let empty_kv = alloc_kv(db, sector, kv_size);
        if empty_kv == FAILED_ADDR {
            if db.gc_request && !already_gc {
                gc_collect_by_free_size(db, kv_size);
                already_gc = true;
                continue;
            } else if already_gc {
                db.gc_request = false;
            }
            return FAILED_ADDR;
        }
        return empty_kv;
    }
}

pub fn new_kv_ex(db: &mut Kvdb, sector: &mut KvdbSecInfo, key_len: usize, buf_len: usize) -> u32 {
    let kv_len = KV_HDR_DATA_SIZE + wg_align(key_len as u32) + wg_align(buf_len as u32);
    new_kv(db, sector, kv_len as usize)
}

pub fn create_kv_blob(
    db: &mut Kvdb,
    sector: &mut KvdbSecInfo,
    key: &[u8],
    value: &[u8],
) -> FdbError {
    if key.len() > KV_NAME_MAX {
        return FdbError::KvNameErr;
    }

    let name_len = key.len() as u32;
    let value_len = value.len() as u32;
    let kv_total_len = KV_HDR_DATA_SIZE + wg_align(name_len) + wg_align(value_len);

    if kv_total_len > db.parent.sec_size - SECTOR_HDR_DATA_SIZE {
        return FdbError::SavedFull;
    }

    let mut kv_addr = sector.empty_kv;
    if kv_addr == FAILED_ADDR {
        kv_addr = new_kv(db, sector, kv_total_len as usize);
    }

    if kv_addr == FAILED_ADDR {
        return FdbError::SavedFull;
    }

    let mut is_full = false;
    let mut result = update_sec_status(db, sector, kv_total_len as usize, &mut is_full);
    if result != FdbError::NoErr {
        return result;
    }

    let mut crc32 = 0u32;
    crc32 = calc_crc32(crc32, &name_len.to_le_bytes());
    crc32 = calc_crc32(crc32, &value_len.to_le_bytes());
    crc32 = calc_crc32(crc32, key);
    let name_padding = wg_align(name_len) - name_len;
    for _ in 0..name_padding {
        crc32 = calc_crc32(crc32, &[BYTE_ERASED]);
    }
    crc32 = calc_crc32(crc32, value);
    let value_padding = wg_align(value_len) - value_len;
    for _ in 0..value_padding {
        crc32 = calc_crc32(crc32, &[BYTE_ERASED]);
    }

    let mut kv_hdr = KvHdrData::new();
    kv_hdr.status_table = [BYTE_ERASED; KV_STATUS_TABLE_SIZE];
    kv_hdr.magic = KV_MAGIC;
    kv_hdr.name_len = name_len as u8;
    kv_hdr.value_len = value_len;
    kv_hdr.len = kv_total_len;
    kv_hdr.crc32 = crc32;

    result = write_kv_hdr(db, kv_addr, &kv_hdr);
    if result != FdbError::NoErr {
        return result;
    }

    let name_padded_len = wg_align(name_len) as usize;
    let mut name_buf = vec![BYTE_ERASED; name_padded_len];
    name_buf[..key.len()].copy_from_slice(key);
    if file_write(&db.parent, kv_addr + KV_HDR_DATA_SIZE, &name_buf, false) != FdbError::NoErr {
        return FdbError::WriteErr;
    }

    if !is_full {
        let empty_addr = kv_addr + KV_HDR_DATA_SIZE + wg_align(name_len) + wg_align(value_len);
        update_sector_empty_addr_cache(db, sector.addr, empty_addr);
    }
    update_kv_cache(db, key, kv_addr);

    let value_padded_len = wg_align(value_len) as usize;
    let mut value_buf = vec![BYTE_ERASED; value_padded_len];
    value_buf[..value.len()].copy_from_slice(value);
    if file_write(
        &db.parent,
        kv_addr + KV_HDR_DATA_SIZE + wg_align(name_len),
        &value_buf,
        false,
    ) != FdbError::NoErr
    {
        return FdbError::WriteErr;
    }

    let mut status_table = [0u8; 1];
    let _ = write_status_to_flash(
        &mut status_table,
        KvStatus::STATUS_NUM as u32,
        KvStatus::Write as u32,
        kv_addr,
        |offset, byte| {
            if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                Ok(())
            } else {
                Err(FdbError::WriteErr)
            }
        },
    );

    if is_full {
        db.gc_request = true;
    }

    FdbError::NoErr
}

pub fn fdb_kv_del(db: &mut Kvdb, key: &[u8]) -> FdbError {
    if !db.parent.init_ok {
        return FdbError::InitFailed;
    }

    db.parent.run_lock();
    let result = del_kv(db, Some(key), None, true);
    db.parent.run_unlock();

    result
}

pub fn set_kv(db: &mut Kvdb, key: &[u8], value: Option<&[u8]>) -> FdbError {
    if value.is_none() {
        return del_kv(db, Some(key), None, true);
    }

    let value = value.unwrap();

    let mut cur_sector = db.cur_sector;
    let mut cur_kv = db.cur_kv;

    if new_kv_ex(db, &mut cur_sector, key.len(), value.len()) == FAILED_ADDR {
        db.cur_sector = cur_sector;
        db.cur_kv = cur_kv;
        return FdbError::SavedFull;
    }

    let kv_is_found = find_kv(db, key, &mut cur_kv);

    let mut result = FdbError::NoErr;
    if kv_is_found {
        result = del_kv(db, Some(key), Some(&cur_kv), false);
    }

    if result == FdbError::NoErr {
        result = create_kv_blob(db, &mut cur_sector, key, value);
    }

    if kv_is_found && result == FdbError::NoErr {
        result = del_kv(db, Some(key), Some(&cur_kv), true);
    }

    if db.gc_request {
        let free_size = KV_HDR_DATA_SIZE as usize
            + wg_align(key.len() as u32) as usize
            + wg_align(value.len() as u32) as usize;
        gc_collect_by_free_size(db, free_size);
    }

    db.cur_sector = cur_sector;
    db.cur_kv = cur_kv;

    result
}

pub fn fdb_kv_set_blob(db: &mut Kvdb, key: &[u8], blob: &crate::blob::Blob) -> FdbError {
    if !db.parent.init_ok {
        return FdbError::InitFailed;
    }

    db.parent.run_lock();
    let value = if blob.buf.is_empty() {
        None
    } else {
        Some(&blob.buf[..])
    };
    let result = set_kv(db, key, value);
    db.parent.run_unlock();

    result
}

pub fn fdb_kv_set(db: &mut Kvdb, key: &[u8], value: Option<&str>) -> FdbError {
    match value {
        Some(v) => {
            let blob = crate::blob::Blob::make(v.as_bytes().to_vec(), v.len());
            fdb_kv_set_blob(db, key, &blob)
        }
        None => fdb_kv_del(db, key),
    }
}

pub fn fdb_kv_set_default(db: &mut Kvdb) -> FdbError {
    db.parent.run_lock();

    for i in 0..KV_CACHE_TABLE_SIZE {
        db.kv_cache_table[i].addr = DATA_UNUSED;
    }

    let max_size = db.parent.max_size;
    let sec_size = db.parent.sec_size;
    let mut addr: u32 = 0;
    while addr < max_size {
        let result = format_sector(db, addr, COMBINED_NONE);
        if result != FdbError::NoErr {
            db.parent.oldest_addr = 0;
            db.parent.run_unlock();
            return result;
        }
        addr += sec_size;
    }

    let default_kvs = db.default_kvs.clone();
    let mut result = FdbError::NoErr;
    for node in &default_kvs.kvs {
        let mut sector = KvdbSecInfo::new();
        sector.empty_kv = FAILED_ADDR;
        result = create_kv_blob(db, &mut sector, node.key.as_bytes(), &node.value);
        if result != FdbError::NoErr {
            break;
        }
    }

    db.parent.oldest_addr = 0;
    db.parent.run_unlock();

    result
}

const FDB_STR_KV_VALUE_MAX_SIZE: usize = 128;
const VER_NUM_KV_NAME: &str = "__ver_num__";

pub fn fdb_kv_print(db: &mut Kvdb) {
    if !db.parent.init_ok {
        return;
    }

    db.parent.run_lock();

    let mut kvs: Vec<KvNode> = Vec::new();
    let mut kv = KvNode::new();
    kv_iterator(db, &mut kv, |cur_kv| {
        if cur_kv.crc_is_ok {
            kvs.push(*cur_kv);
        }
        false
    });

    let mut using_size: usize = 0;
    for k in &kvs {
        using_size += k.len as usize;
        if k.status != KvStatus::Write {
            continue;
        }

        let name_len = k.name_len as usize;
        print!("{}=", std::str::from_utf8(&k.name[..name_len]).unwrap_or(""));

        let value_len = k.value_len as usize;
        if value_len < FDB_STR_KV_VALUE_MAX_SIZE {
            let mut buf = vec![0u8; value_len];
            let _ = file_read(&db.parent, k.addr.value, &mut buf);
            if fdb_is_str(&buf) {
                if let Ok(s) = std::str::from_utf8(&buf) {
                    print!("{}", s);
                }
            } else {
                print!("blob @0x{:08X} {}bytes", k.addr.value, k.value_len);
            }
        } else {
            print!("blob @0x{:08X} {}bytes", k.addr.value, k.value_len);
        }
        println!();
    }

    let sector_num = db.parent.max_size / db.parent.sec_size;
    let total_using =
        using_size + ((sector_num - GC_EMPTY_SEC_THRESHOLD as u32) as usize) * SECTOR_HDR_DATA_SIZE as usize;
    let total_size = db.parent.max_size - db.parent.sec_size * GC_EMPTY_SEC_THRESHOLD as u32;
    println!("\nmode: next generation");
    println!("size: {}/{} bytes.", total_using, total_size);

    db.parent.run_unlock();
}

pub fn kv_auto_update(db: &mut Kvdb) {
    let setting_ver_num = db.ver_num as usize;

    let mut saved_ver_buf = [0u8; std::mem::size_of::<usize>()];
    let read_len = get_kv(
        db,
        VER_NUM_KV_NAME.as_bytes(),
        Some(&mut saved_ver_buf),
        None,
    );
    if read_len > 0 {
        let saved_ver_num = usize::from_le_bytes(saved_ver_buf);
        if saved_ver_num != setting_ver_num {
            let default_kvs = db.default_kvs.clone();
            let mut cur_sector = db.cur_sector;
            let mut cur_kv = db.cur_kv;

            for node in &default_kvs.kvs {
                if !find_kv(db, node.key.as_bytes(), &mut cur_kv) {
                    cur_sector.empty_kv = FAILED_ADDR;
                    let _ = create_kv_blob(db, &mut cur_sector, node.key.as_bytes(), &node.value);
                }
            }

            db.cur_sector = cur_sector;
            db.cur_kv = cur_kv;

            let ver_bytes = setting_ver_num.to_le_bytes();
            let _ = set_kv(db, VER_NUM_KV_NAME.as_bytes(), Some(&ver_bytes));
        }
    } else {
        let ver_bytes = setting_ver_num.to_le_bytes();
        let _ = set_kv(db, VER_NUM_KV_NAME.as_bytes(), Some(&ver_bytes));
    }
}

fn check_oldest_addr_cb(db: &mut Kvdb) -> u32 {
    let mut oldest_addr: u32 = 0;
    let mut last_status = SectorStoreStatus::Empty;
    let mut sector = KvdbSecInfo::new();

    sector_iterator(
        db,
        &mut sector,
        SectorStoreStatus::Unused,
        false,
        |sec| {
            if last_status == SectorStoreStatus::Empty
                && (sec.status.store == SectorStoreStatus::Full
                    || sec.status.store == SectorStoreStatus::Using)
            {
                oldest_addr = sec.addr;
            }
            last_status = sec.status.store;
            false
        },
    );

    oldest_addr
}

fn check_sec_hdr_cb(db: &mut Kvdb) -> usize {
    let mut failed_count: usize = 0;
    let mut bad_sectors: Vec<u32> = Vec::new();
    let mut sector = KvdbSecInfo::new();

    sector_iterator(
        db,
        &mut sector,
        SectorStoreStatus::Unused,
        false,
        |sec| {
            if !sec.check_ok {
                failed_count += 1;
                bad_sectors.push(sec.addr);
            }
            false
        },
    );

    let not_formatable = db.parent.not_formatable;
    for addr in &bad_sectors {
        if not_formatable {
            break;
        }
        let _ = format_sector(db, *addr, COMBINED_NONE);
    }

    failed_count
}

fn check_and_recovery_gc_cb(db: &mut Kvdb) {
    let mut gc_sectors: Vec<u32> = Vec::new();
    let mut sector = KvdbSecInfo::new();

    sector_iterator(
        db,
        &mut sector,
        SectorStoreStatus::Unused,
        false,
        |sec| {
            if sec.check_ok && sec.status.dirty == SectorDirtyStatus::Gc {
                gc_sectors.push(sec.addr);
            }
            false
        },
    );

    if !gc_sectors.is_empty() {
        db.gc_request = true;
        gc_collect(db);
    }
}

fn check_and_recovery_kv_cb(db: &mut Kvdb) -> bool {
    let mut kvs_to_recover: Vec<KvNode> = Vec::new();
    let mut kvs_to_cache: Vec<KvNode> = Vec::new();
    let mut kv = KvNode::new();

    kv_iterator(db, &mut kv, |cur_kv| {
        if cur_kv.crc_is_ok && cur_kv.status == KvStatus::PreDelete {
            kvs_to_recover.push(*cur_kv);
        } else if cur_kv.status == KvStatus::PreWrite {
            kvs_to_recover.push(*cur_kv);
        } else if cur_kv.crc_is_ok && cur_kv.status == KvStatus::Write {
            kvs_to_cache.push(*cur_kv);
        }
        false
    });

    for k in &kvs_to_cache {
        let name_len = k.name_len as usize;
        update_kv_cache(db, &k.name[..name_len], k.addr.start);
    }

    let mut need_retry = false;
    for k in &kvs_to_recover {
        if k.status == KvStatus::PreDelete {
            if move_kv(db, k) != FdbError::NoErr {
                need_retry = true;
                break;
            }
        } else if k.status == KvStatus::PreWrite {
            let mut status_table = [0u8; KV_STATUS_TABLE_SIZE];
            let _ = write_status_to_flash(
                &mut status_table,
                KvStatus::STATUS_NUM as u32,
                KvStatus::ErrHdr as u32,
                k.addr.start,
                |offset, byte| {
                    if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                        Ok(())
                    } else {
                        Err(FdbError::WriteErr)
                    }
                },
            );
            need_retry = true;
            break;
        }
    }

    need_retry
}

pub fn _fdb_kv_load(db: &mut Kvdb) -> FdbError {
    db.in_recovery_check = true;

    let check_failed_count = check_sec_hdr_cb(db);
    if db.parent.not_formatable && check_failed_count > 0 {
        db.in_recovery_check = false;
        return FdbError::ReadErr;
    }

    let sector_num = db.parent.max_size / db.parent.sec_size;
    if check_failed_count as u32 == sector_num {
        let _ = fdb_kv_set_default(db);
    }

    check_and_recovery_gc_cb(db);

    loop {
        let need_retry = check_and_recovery_kv_cb(db);
        if db.gc_request {
            gc_collect(db);
            continue;
        }
        if !need_retry {
            break;
        }
    }

    db.parent.oldest_addr = check_oldest_addr_cb(db);
    db.in_recovery_check = false;

    FdbError::NoErr
}

pub const FDB_KVDB_CTRL_SET_SEC_SIZE: u32 = 0x00;
pub const FDB_KVDB_CTRL_GET_SEC_SIZE: u32 = 0x01;
pub const FDB_KVDB_CTRL_SET_LOCK: u32 = 0x02;
pub const FDB_KVDB_CTRL_SET_UNLOCK: u32 = 0x03;
pub const FDB_KVDB_CTRL_SET_FILE_MODE: u32 = 0x09;
pub const FDB_KVDB_CTRL_SET_MAX_SIZE: u32 = 0x0A;
pub const FDB_KVDB_CTRL_SET_NOT_FORMAT: u32 = 0x0B;

pub enum KvdbCtrlArg<'a> {
    SecSize(u32),
    GetSecSize(&'a mut u32),
    Lock(Box<dyn FnMut() + Send>),
    Unlock(Box<dyn FnMut() + Send>),
    FileMode(bool),
    MaxSize(u32),
    NotFormat(bool),
}

pub fn fdb_kvdb_control(db: &mut Kvdb, cmd: u32, arg: KvdbCtrlArg<'_>) {
    match cmd {
        x if x == FDB_KVDB_CTRL_SET_SEC_SIZE => {
            debug_assert!(!db.parent.init_ok);
            if let KvdbCtrlArg::SecSize(v) = arg {
                db.parent.sec_size = v;
            }
        }
        x if x == FDB_KVDB_CTRL_GET_SEC_SIZE => {
            if let KvdbCtrlArg::GetSecSize(out) = arg {
                *out = db.parent.sec_size;
            }
        }
        x if x == FDB_KVDB_CTRL_SET_LOCK => {
            if let KvdbCtrlArg::Lock(f) = arg {
                db.parent.lock = Some(f);
            }
        }
        x if x == FDB_KVDB_CTRL_SET_UNLOCK => {
            if let KvdbCtrlArg::Unlock(f) = arg {
                db.parent.unlock = Some(f);
            }
        }
        x if x == FDB_KVDB_CTRL_SET_FILE_MODE => {
            debug_assert!(!db.parent.init_ok);
            if let KvdbCtrlArg::FileMode(v) = arg {
                db.parent.file_mode = v;
            }
        }
        x if x == FDB_KVDB_CTRL_SET_MAX_SIZE => {
            debug_assert!(!db.parent.init_ok);
            if let KvdbCtrlArg::MaxSize(v) = arg {
                db.parent.max_size = v;
            }
        }
        x if x == FDB_KVDB_CTRL_SET_NOT_FORMAT => {
            debug_assert!(!db.parent.init_ok);
            if let KvdbCtrlArg::NotFormat(v) = arg {
                db.parent.not_formatable = v;
            }
        }
        _ => {}
    }
}

pub fn fdb_kvdb_init(
    db: &mut Kvdb,
    name: &str,
    path: &std::path::Path,
    default_kv: Option<&DefaultKv>,
) -> FdbError {
    let sec_size = db.parent.sec_size;
    let max_size = db.parent.max_size;
    let file_mode = db.parent.file_mode;

    let result = crate::db::db_init(
        &mut db.parent,
        name,
        path,
        crate::def::DbType::Kv,
        file_mode,
        sec_size,
        max_size,
    );
    if result != FdbError::NoErr {
        crate::db::db_init_finish(&mut db.parent, result);
        return result;
    }

    db.parent.run_lock();

    db.gc_request = false;
    db.in_recovery_check = false;
    db.default_kvs = match default_kv {
        Some(d) => d.clone(),
        None => DefaultKv::new(),
    };

    db.parent.oldest_addr = 0;
    db.parent.oldest_addr = check_oldest_addr_cb(db);

    let sector_num = db.parent.max_size / db.parent.sec_size;
    debug_assert!(GC_EMPTY_SEC_THRESHOLD > 0 && (GC_EMPTY_SEC_THRESHOLD as u32) < sector_num);

    for i in 0..SECTOR_CACHE_TABLE_SIZE {
        db.sector_cache_table[i].check_ok = false;
        db.sector_cache_table[i].empty_kv = FAILED_ADDR;
        db.sector_cache_table[i].addr = DATA_UNUSED;
    }
    for i in 0..KV_CACHE_TABLE_SIZE {
        db.kv_cache_table[i].addr = DATA_UNUSED;
    }

    db.parent.run_unlock();

    let result = _fdb_kv_load(db);

    db.parent.run_lock();
    if result == FdbError::NoErr {
        kv_auto_update(db);
    }
    db.parent.run_unlock();

    crate::db::db_init_finish(&mut db.parent, result);

    result
}

pub fn fdb_kvdb_deinit(db: &mut Kvdb) -> FdbError {
    crate::db::db_deinit(&mut db.parent);
    FdbError::NoErr
}

pub const KV_CACHE_TABLE_SIZE: usize = 64;
pub const SECTOR_CACHE_TABLE_SIZE: usize = 8;

#[derive(Debug)]
pub struct Kvdb {
    pub parent: FdbDb,
    pub default_kvs: DefaultKv,
    pub gc_request: bool,
    pub in_recovery_check: bool,
    pub cur_kv: KvNode,
    pub cur_sector: KvdbSecInfo,
    pub last_is_complete_del: bool,
    pub kv_cache_table: Vec<KvCacheNode>,
    pub sector_cache_table: Vec<KvdbSecInfo>,
    pub ver_num: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct KvCacheNode {
    pub name_crc: u16,
    pub active: u16,
    pub addr: u32,
}

impl KvCacheNode {
    pub fn new() -> Self {
        KvCacheNode {
            name_crc: 0,
            active: 0,
            addr: crate::low_lvl::DATA_UNUSED,
        }
    }
}

impl Default for KvCacheNode {
    fn default() -> Self {
        Self::new()
    }
}

impl Kvdb {
    pub fn new(parent: FdbDb) -> Self {
        Kvdb {
            parent,
            default_kvs: DefaultKv::new(),
            gc_request: false,
            in_recovery_check: false,
            cur_kv: KvNode::new(),
            cur_sector: KvdbSecInfo::new(),
            last_is_complete_del: false,
            kv_cache_table: vec![KvCacheNode::new(); KV_CACHE_TABLE_SIZE],
            sector_cache_table: vec![KvdbSecInfo::new(); SECTOR_CACHE_TABLE_SIZE],
            ver_num: 0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct KvIterator {
    pub curr_kv: KvNode,
    pub iterated_cnt: u32,
    pub iterated_obj_bytes: usize,
    pub iterated_value_bytes: usize,
    pub sector_addr: u32,
    pub traversed_len: u32,
}

impl KvIterator {
    pub fn new() -> Self {
        KvIterator {
            curr_kv: KvNode::new(),
            iterated_cnt: 0,
            iterated_obj_bytes: 0,
            iterated_value_bytes: 0,
            sector_addr: 0,
            traversed_len: 0,
        }
    }
}

impl Default for KvIterator {
    fn default() -> Self {
        Self::new()
    }
}

pub fn fdb_kv_iterator_init(db: &Kvdb, itr: &mut KvIterator) {
    itr.curr_kv.addr.start = 0;
    itr.iterated_cnt = 0;
    itr.iterated_obj_bytes = 0;
    itr.iterated_value_bytes = 0;
    itr.traversed_len = 0;
    itr.sector_addr = db.parent.oldest_addr;
}

pub fn fdb_kv_iterate(db: &mut Kvdb, itr: &mut KvIterator) -> bool {
    loop {
        let mut sector = KvdbSecInfo::new();
        if read_sector_info(db, itr.sector_addr, &mut sector, false) == FdbError::NoErr {
            if sector.status.store == SectorStoreStatus::Using
                || sector.status.store == SectorStoreStatus::Full
            {
                let enter_inner;
                if itr.curr_kv.addr.start == 0 {
                    itr.curr_kv.addr.start = sector.addr + SECTOR_HDR_DATA_SIZE;
                    enter_inner = true;
                } else {
                    let next = get_next_kv_addr(db, &sector, &itr.curr_kv);
                    if next == FAILED_ADDR {
                        enter_inner = false;
                    } else {
                        itr.curr_kv.addr.start = next;
                        enter_inner = true;
                    }
                }
                if enter_inner {
                    loop {
                        read_kv(db, &mut itr.curr_kv);
                        if itr.curr_kv.status == KvStatus::Write && itr.curr_kv.crc_is_ok {
                            itr.iterated_cnt += 1;
                            itr.iterated_obj_bytes += itr.curr_kv.len as usize;
                            itr.iterated_value_bytes += itr.curr_kv.value_len as usize;
                            return true;
                        }
                        let next = get_next_kv_addr(db, &sector, &itr.curr_kv);
                        if next == FAILED_ADDR {
                            break;
                        }
                        itr.curr_kv.addr.start = next;
                    }
                }
            }
        }
        itr.curr_kv.addr.start = 0;
        itr.traversed_len += db.parent.sec_size;
        let next_sec = get_next_sector_addr(db, &sector, itr.traversed_len);
        if next_sec == FAILED_ADDR {
            return false;
        }
        itr.sector_addr = next_sec;
    }
}

pub fn fdb_kvdb_check(db: &mut Kvdb) -> FdbError {
    if !db.parent.init_ok {
        return FdbError::InitFailed;
    }

    db.parent.run_lock();

    let mut sec_addr = db.parent.oldest_addr;
    let mut traversed_len: u32 = 0;
    let mut result;
    let mut sector = KvdbSecInfo::new();
    let mut kv = KvNode::new();

    loop {
        traversed_len += db.parent.sec_size;
        result = read_sector_info(db, sec_addr, &mut sector, false);
        if result == FdbError::NoErr {
            if sector.status.store == SectorStoreStatus::Using
                || sector.status.store == SectorStoreStatus::Full
            {
                kv.addr.start = sector.addr + SECTOR_HDR_DATA_SIZE;
                loop {
                    result = read_kv(db, &mut kv);
                    let next = get_next_kv_addr(db, &sector, &kv);
                    if next == FAILED_ADDR || result != FdbError::NoErr {
                        break;
                    }
                    kv.addr.start = next;
                }
            }
        }
        let next_sec = get_next_sector_addr(db, &sector, traversed_len);
        if next_sec == FAILED_ADDR || result != FdbError::NoErr {
            break;
        }
        sec_addr = next_sec;
    }

    db.parent.run_unlock();

    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kv_node_default_is_unused() {
        let node = KvNode::new();
        assert_eq!(node.status, KvStatus::Unused);
        assert_eq!(node.name_len, 0);
        assert_eq!(node.name_str(), "");
    }

    #[test]
    fn kv_node_set_and_read_name() {
        let mut node = KvNode::new();
        node.set_name("foo");
        assert_eq!(node.name_len, 3);
        assert_eq!(node.name_str(), "foo");
    }

    #[test]
    fn kv_node_name_truncated_to_max() {
        let mut node = KvNode::new();
        let long_name: String = "x".repeat(KV_NAME_MAX + 10);
        node.set_name(&long_name);
        assert_eq!(node.name_len as usize, KV_NAME_MAX);
    }
}
