use crate::def::{FdbDb, FdbTime, GetTimeFn, SectorStoreStatus, TslStatus, TIME_UNUSED};
use crate::error::FdbError;
use crate::file_backend::{file_read, file_write, file_erase};
use crate::low_lvl::{align_down, get_status, set_status, wg_align, write_status_to_flash, FAILED_ADDR, DATA_UNUSED, BYTE_ERASED};

const SECTOR_MAGIC_WORD: u32 = 0x304C5354;
const SECTOR_HDR_DATA_SIZE: u32 = 44;
const LOG_IDX_DATA_SIZE: u32 = 24;
const LOG_IDX_TS_OFFSET: u32 = 8;

const SECTOR_MAGIC_OFFSET: usize = 1;
const SECTOR_START_TIME_OFFSET: usize = 5;
const SECTOR_END0_TIME_OFFSET: usize = 13;
const SECTOR_END0_IDX_OFFSET: usize = 21;
const SECTOR_END0_STATUS_OFFSET: usize = 25;
const SECTOR_END1_TIME_OFFSET: usize = 26;
const SECTOR_END1_IDX_OFFSET: usize = 34;
const SECTOR_END1_STATUS_OFFSET: usize = 38;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct TslAddr {
    pub index: u32,
    pub log: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Tsl {
    pub status: TslStatus,
    pub time: FdbTime,
    pub log_len: u32,
    pub addr: TslAddr,
}

impl Tsl {
    pub fn new() -> Self {
        Tsl {
            status: TslStatus::Unused,
            time: TIME_UNUSED,
            log_len: 0,
            addr: TslAddr {
                index: 0,
                log: 0,
            },
        }
    }
}

impl Default for Tsl {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct TsdbSecInfo {
    pub check_ok: bool,
    pub status: SectorStoreStatus,
    pub addr: u32,
    pub magic: u32,
    pub start_time: FdbTime,
    pub end_time: FdbTime,
    pub end_idx: u32,
    pub end_info_stat: [TslStatus; 2],
    pub remain: usize,
    pub empty_idx: u32,
    pub empty_data: u32,
}

impl TsdbSecInfo {
    pub fn new() -> Self {
        TsdbSecInfo {
            check_ok: false,
            status: SectorStoreStatus::Unused,
            addr: 0,
            magic: 0,
            start_time: TIME_UNUSED,
            end_time: TIME_UNUSED,
            end_idx: FAILED_ADDR,
            end_info_stat: [TslStatus::Unused; 2],
            remain: 0,
            empty_idx: 0,
            empty_data: 0,
        }
    }
}

impl Default for TsdbSecInfo {
    fn default() -> Self {
        Self::new()
    }
}

pub struct Tsdb {
    pub parent: FdbDb,
    pub cur_sec: TsdbSecInfo,
    pub last_time: FdbTime,
    pub get_time: Option<GetTimeFn>,
    pub max_len: usize,
    pub rollover: bool,
}

impl std::fmt::Debug for Tsdb {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Tsdb")
            .field("parent", &self.parent)
            .field("cur_sec", &self.cur_sec)
            .field("last_time", &self.last_time)
            .field("has_get_time", &self.get_time.is_some())
            .field("max_len", &self.max_len)
            .field("rollover", &self.rollover)
            .finish()
    }
}

impl Tsdb {
    pub fn new() -> Self {
        Tsdb {
            parent: FdbDb::new("", crate::def::DbType::Ts, std::path::PathBuf::new()),
            cur_sec: TsdbSecInfo::new(),
            last_time: 0,
            get_time: None,
            max_len: 0,
            rollover: true,
        }
    }
}

impl Default for Tsdb {
    fn default() -> Self {
        Self::new()
    }
}

pub fn read_tsl(db: &Tsdb, tsl: &mut Tsl) -> FdbError {
    let mut buf = [0u8; LOG_IDX_DATA_SIZE as usize];
    if file_read(&db.parent, tsl.addr.index, &mut buf).is_err() {
        return FdbError::ReadErr;
    }

    let status_idx = get_status(&buf[0..1], TslStatus::STATUS_NUM as u32);
    tsl.status = TslStatus::from_byte(status_idx as u8).unwrap_or(TslStatus::Unused);

    if tsl.status == TslStatus::PreWrite || tsl.status == TslStatus::Unused {
        tsl.log_len = db.max_len as u32;
        tsl.addr.log = DATA_UNUSED;
        tsl.time = 0;
    } else {
        tsl.time = i64::from_le_bytes(buf[8..16].try_into().unwrap());
        tsl.log_len = u32::from_le_bytes(buf[16..20].try_into().unwrap());
        tsl.addr.log = u32::from_le_bytes(buf[20..24].try_into().unwrap());
    }

    FdbError::NoErr
}

pub fn get_next_sector_addr(db: &Tsdb, pre_sec: &TsdbSecInfo, traversed_len: u32) -> u32 {
    if traversed_len + db.parent.sec_size <= db.parent.max_size {
        if pre_sec.addr + db.parent.sec_size < db.parent.max_size {
            pre_sec.addr + db.parent.sec_size
        } else {
            0
        }
    } else {
        FAILED_ADDR
    }
}

pub fn get_next_tsl_addr(sector: &TsdbSecInfo, pre_tsl: &Tsl) -> u32 {
    if sector.status == SectorStoreStatus::Empty {
        return FAILED_ADDR;
    }

    if pre_tsl.addr.index + LOG_IDX_DATA_SIZE <= sector.end_idx {
        pre_tsl.addr.index + LOG_IDX_DATA_SIZE
    } else {
        FAILED_ADDR
    }
}

pub fn get_last_sector_addr(db: &Tsdb, pre_sec: &TsdbSecInfo, traversed_len: u32) -> u32 {
    if traversed_len + db.parent.sec_size <= db.parent.max_size {
        if pre_sec.addr >= db.parent.sec_size {
            pre_sec.addr - db.parent.sec_size
        } else {
            db.parent.max_size - db.parent.sec_size
        }
    } else {
        FAILED_ADDR
    }
}

pub fn get_last_tsl_addr(sector: &TsdbSecInfo, pre_tsl: &Tsl) -> u32 {
    if sector.status == SectorStoreStatus::Empty {
        return FAILED_ADDR;
    }

    if pre_tsl.addr.index >= sector.addr + SECTOR_HDR_DATA_SIZE + LOG_IDX_DATA_SIZE {
        pre_tsl.addr.index - LOG_IDX_DATA_SIZE
    } else {
        FAILED_ADDR
    }
}

pub fn read_sector_info(db: &Tsdb, addr: u32, sector: &mut TsdbSecInfo, traversal: bool) -> FdbError {
    let mut sec_hdr = [0u8; SECTOR_HDR_DATA_SIZE as usize];
    let _ = file_read(&db.parent, addr, &mut sec_hdr);

    sector.addr = addr;
    sector.magic = u32::from_le_bytes([
        sec_hdr[SECTOR_MAGIC_OFFSET],
        sec_hdr[SECTOR_MAGIC_OFFSET + 1],
        sec_hdr[SECTOR_MAGIC_OFFSET + 2],
        sec_hdr[SECTOR_MAGIC_OFFSET + 3],
    ]);

    if sector.magic != SECTOR_MAGIC_WORD {
        sector.check_ok = false;
        return FdbError::InitFailed;
    }

    sector.check_ok = true;

    let store_status_idx = get_status(&sec_hdr[0..1], SectorStoreStatus::STATUS_NUM as u32);
    sector.status = SectorStoreStatus::from_byte(store_status_idx as u8)
        .unwrap_or(SectorStoreStatus::Unused);

    sector.start_time = i64::from_le_bytes(
        sec_hdr[SECTOR_START_TIME_OFFSET..SECTOR_START_TIME_OFFSET + 8]
            .try_into()
            .unwrap(),
    );

    let end0_status_idx = get_status(
        &sec_hdr[SECTOR_END0_STATUS_OFFSET..SECTOR_END0_STATUS_OFFSET + 1],
        TslStatus::STATUS_NUM as u32,
    );
    sector.end_info_stat[0] =
        TslStatus::from_byte(end0_status_idx as u8).unwrap_or(TslStatus::Unused);

    let end1_status_idx = get_status(
        &sec_hdr[SECTOR_END1_STATUS_OFFSET..SECTOR_END1_STATUS_OFFSET + 1],
        TslStatus::STATUS_NUM as u32,
    );
    sector.end_info_stat[1] =
        TslStatus::from_byte(end1_status_idx as u8).unwrap_or(TslStatus::Unused);

    if sector.end_info_stat[0] == TslStatus::Write {
        sector.end_time = i64::from_le_bytes(
            sec_hdr[SECTOR_END0_TIME_OFFSET..SECTOR_END0_TIME_OFFSET + 8]
                .try_into()
                .unwrap(),
        );
        sector.end_idx = u32::from_le_bytes(
            sec_hdr[SECTOR_END0_IDX_OFFSET..SECTOR_END0_IDX_OFFSET + 4]
                .try_into()
                .unwrap(),
        );
    } else if sector.end_info_stat[1] == TslStatus::Write {
        sector.end_time = i64::from_le_bytes(
            sec_hdr[SECTOR_END1_TIME_OFFSET..SECTOR_END1_TIME_OFFSET + 8]
                .try_into()
                .unwrap(),
        );
        sector.end_idx = u32::from_le_bytes(
            sec_hdr[SECTOR_END1_IDX_OFFSET..SECTOR_END1_IDX_OFFSET + 4]
                .try_into()
                .unwrap(),
        );
    }

    sector.empty_idx = sector.addr + SECTOR_HDR_DATA_SIZE;
    sector.empty_data = sector.addr + db.parent.sec_size;
    sector.remain = (sector.empty_data - sector.empty_idx) as usize;

    if sector.status == SectorStoreStatus::Using && traversal {
        let mut tsl = Tsl::new();
        tsl.addr.index = sector.empty_idx;

        loop {
            if read_tsl(db, &mut tsl) != FdbError::NoErr {
                break;
            }
            if tsl.status == TslStatus::Unused {
                break;
            }
            if tsl.status != TslStatus::PreWrite {
                sector.end_time = tsl.time;
            }
            sector.end_idx = tsl.addr.index;
            sector.empty_idx += LOG_IDX_DATA_SIZE;
            sector.empty_data = sector.empty_data.saturating_sub(wg_align(tsl.log_len));
            tsl.addr.index += LOG_IDX_DATA_SIZE;
            let consumed = LOG_IDX_DATA_SIZE + wg_align(tsl.log_len);
            if sector.remain > consumed as usize {
                sector.remain -= consumed as usize;
            } else {
                sector.remain = 0;
                return FdbError::ReadErr;
            }
        }
    }

    FdbError::NoErr
}

pub fn format_sector(db: &mut Tsdb, addr: u32) -> FdbError {
    assert!(addr % db.parent.sec_size == 0);

    let sec_size = db.parent.sec_size;
    if file_erase(&db.parent, addr, sec_size as usize) != FdbError::NoErr {
        return FdbError::EraseErr;
    }

    let mut sec_hdr = [BYTE_ERASED; SECTOR_HDR_DATA_SIZE as usize];
    set_status(
        &mut sec_hdr[0..1],
        SectorStoreStatus::STATUS_NUM as u32,
        SectorStoreStatus::Empty as u32,
    );
    sec_hdr[SECTOR_MAGIC_OFFSET..SECTOR_MAGIC_OFFSET + 4]
        .copy_from_slice(&SECTOR_MAGIC_WORD.to_le_bytes());

    if file_write(&db.parent, addr, &sec_hdr, true) != FdbError::NoErr {
        return FdbError::WriteErr;
    }

    FdbError::NoErr
}

pub fn sector_iterator<F>(
    db: &Tsdb,
    sector: &mut TsdbSecInfo,
    status: SectorStoreStatus,
    traversal: bool,
    mut callback: F,
) where
    F: FnMut(&mut TsdbSecInfo) -> bool,
{
    let mut sec_addr = sector.addr;
    let mut traversed_len = 0u32;

    loop {
        read_sector_info(db, sec_addr, sector, false);
        if status == SectorStoreStatus::Unused || status == sector.status {
            if traversal {
                read_sector_info(db, sec_addr, sector, true);
            }
            if callback(sector) {
                return;
            }
        }
        traversed_len += db.parent.sec_size;
        let next = get_next_sector_addr(db, sector, traversed_len);
        if next == FAILED_ADDR {
            break;
        }
        sec_addr = next;
    }
}

pub fn write_tsl(db: &mut Tsdb, blob: &[u8], time: FdbTime) -> FdbError {
    let idx_addr = db.cur_sec.empty_idx;
    let log_addr = db.cur_sec.empty_data - wg_align(blob.len() as u32);

    let mut idx_buf = [0u8; LOG_IDX_DATA_SIZE as usize];
    idx_buf[8..16].copy_from_slice(&time.to_le_bytes());
    idx_buf[16..20].copy_from_slice(&(blob.len() as u32).to_le_bytes());
    idx_buf[20..24].copy_from_slice(&log_addr.to_le_bytes());

    let mut status_table = [0u8; 1];
    if write_status_to_flash(
        &mut status_table,
        TslStatus::STATUS_NUM as u32,
        TslStatus::PreWrite as u32,
        idx_addr,
        |offset, byte| {
            if file_write(&db.parent, offset, &[byte], false) == FdbError::NoErr {
                Ok(())
            } else {
                Err(FdbError::WriteErr)
            }
        },
    )
    .is_err()
    {
        return FdbError::WriteErr;
    }

    if file_write(
        &db.parent,
        idx_addr + LOG_IDX_TS_OFFSET,
        &idx_buf[LOG_IDX_TS_OFFSET as usize..],
        false,
    ) != FdbError::NoErr
    {
        return FdbError::WriteErr;
    }

    let padded_len = wg_align(blob.len() as u32) as usize;
    let mut blob_buf = vec![BYTE_ERASED; padded_len];
    blob_buf[..blob.len()].copy_from_slice(blob);
    if file_write(&db.parent, log_addr, &blob_buf, false) != FdbError::NoErr {
        return FdbError::WriteErr;
    }

    let mut status_table = [0u8; 1];
    if write_status_to_flash(
        &mut status_table,
        TslStatus::STATUS_NUM as u32,
        TslStatus::Write as u32,
        idx_addr,
        |offset, byte| {
            if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                Ok(())
            } else {
                Err(FdbError::WriteErr)
            }
        },
    )
    .is_err()
    {
        return FdbError::WriteErr;
    }

    FdbError::NoErr
}

fn update_sec_status(db: &mut Tsdb, blob_size: usize, cur_time: FdbTime) -> FdbError {
    let needed = LOG_IDX_DATA_SIZE as usize + wg_align(blob_size as u32) as usize;

    if db.cur_sec.status == SectorStoreStatus::Using && db.cur_sec.remain < needed {
        let cur_sec_addr = db.cur_sec.addr;
        let end_index_temp = db.cur_sec.empty_idx - LOG_IDX_DATA_SIZE;
        let last_time = db.last_time;

        let time_align = wg_align(8u32) as usize;
        let mut time_buf = vec![BYTE_ERASED; time_align];
        time_buf[..8].copy_from_slice(&last_time.to_le_bytes());

        let idx_align = wg_align(4u32) as usize;
        let mut index_buf = vec![BYTE_ERASED; idx_align];
        index_buf[..4].copy_from_slice(&end_index_temp.to_le_bytes());

        if db.cur_sec.end_info_stat[0] == TslStatus::Unused {
            let mut end_status = [0u8; 1];
            if write_status_to_flash(
                &mut end_status,
                TslStatus::STATUS_NUM as u32,
                TslStatus::PreWrite as u32,
                cur_sec_addr + SECTOR_END0_STATUS_OFFSET as u32,
                |offset, byte| {
                    if file_write(&db.parent, offset, &[byte], false) == FdbError::NoErr {
                        Ok(())
                    } else {
                        Err(FdbError::WriteErr)
                    }
                },
            )
            .is_err()
            {
                return FdbError::WriteErr;
            }
            if file_write(
                &db.parent,
                cur_sec_addr + SECTOR_END0_TIME_OFFSET as u32,
                &time_buf,
                false,
            ) != FdbError::NoErr
            {
                return FdbError::WriteErr;
            }
            if file_write(
                &db.parent,
                cur_sec_addr + SECTOR_END0_IDX_OFFSET as u32,
                &index_buf,
                false,
            ) != FdbError::NoErr
            {
                return FdbError::WriteErr;
            }
            let mut end_status = [0u8; 1];
            if write_status_to_flash(
                &mut end_status,
                TslStatus::STATUS_NUM as u32,
                TslStatus::Write as u32,
                cur_sec_addr + SECTOR_END0_STATUS_OFFSET as u32,
                |offset, byte| {
                    if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                        Ok(())
                    } else {
                        Err(FdbError::WriteErr)
                    }
                },
            )
            .is_err()
            {
                return FdbError::WriteErr;
            }
        } else if db.cur_sec.end_info_stat[1] == TslStatus::Unused {
            let mut end_status = [0u8; 1];
            if write_status_to_flash(
                &mut end_status,
                TslStatus::STATUS_NUM as u32,
                TslStatus::PreWrite as u32,
                cur_sec_addr + SECTOR_END1_STATUS_OFFSET as u32,
                |offset, byte| {
                    if file_write(&db.parent, offset, &[byte], false) == FdbError::NoErr {
                        Ok(())
                    } else {
                        Err(FdbError::WriteErr)
                    }
                },
            )
            .is_err()
            {
                return FdbError::WriteErr;
            }
            if file_write(
                &db.parent,
                cur_sec_addr + SECTOR_END1_TIME_OFFSET as u32,
                &time_buf,
                false,
            ) != FdbError::NoErr
            {
                return FdbError::WriteErr;
            }
            if file_write(
                &db.parent,
                cur_sec_addr + SECTOR_END1_IDX_OFFSET as u32,
                &index_buf,
                false,
            ) != FdbError::NoErr
            {
                return FdbError::WriteErr;
            }
            let mut end_status = [0u8; 1];
            if write_status_to_flash(
                &mut end_status,
                TslStatus::STATUS_NUM as u32,
                TslStatus::Write as u32,
                cur_sec_addr + SECTOR_END1_STATUS_OFFSET as u32,
                |offset, byte| {
                    if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                        Ok(())
                    } else {
                        Err(FdbError::WriteErr)
                    }
                },
            )
            .is_err()
            {
                return FdbError::WriteErr;
            }
        }

        let mut status_table = [0u8; 1];
        if write_status_to_flash(
            &mut status_table,
            SectorStoreStatus::STATUS_NUM as u32,
            SectorStoreStatus::Full as u32,
            cur_sec_addr,
            |offset, byte| {
                if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                    Ok(())
                } else {
                    Err(FdbError::WriteErr)
                }
            },
        )
        .is_err()
        {
            return FdbError::WriteErr;
        }
        db.cur_sec.status = SectorStoreStatus::Full;

        let new_sec_addr = if cur_sec_addr + db.parent.sec_size < db.parent.max_size {
            cur_sec_addr + db.parent.sec_size
        } else if db.rollover {
            0
        } else {
            return FdbError::SavedFull;
        };

        let mut sec_info = TsdbSecInfo::new();
        let _ = read_sector_info(db, new_sec_addr, &mut sec_info, false);

        if sec_info.status != SectorStoreStatus::Empty {
            db.parent.oldest_addr = if new_sec_addr + db.parent.sec_size < db.parent.max_size {
                new_sec_addr + db.parent.sec_size
            } else {
                0
            };
            if format_sector(db, new_sec_addr) != FdbError::NoErr {
                return FdbError::EraseErr;
            }
            let _ = read_sector_info(db, new_sec_addr, &mut sec_info, false);
        }
        db.cur_sec = sec_info;
    } else if db.cur_sec.status == SectorStoreStatus::Full {
        return FdbError::SavedFull;
    }

    if db.cur_sec.status == SectorStoreStatus::Empty {
        db.cur_sec.status = SectorStoreStatus::Using;
        db.cur_sec.start_time = cur_time;

        let mut status_table = [0u8; 1];
        if write_status_to_flash(
            &mut status_table,
            SectorStoreStatus::STATUS_NUM as u32,
            SectorStoreStatus::Using as u32,
            db.cur_sec.addr,
            |offset, byte| {
                if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                    Ok(())
                } else {
                    Err(FdbError::WriteErr)
                }
            },
        )
        .is_err()
        {
            return FdbError::WriteErr;
        }

        let time_align = wg_align(8u32) as usize;
        let mut time_buf = vec![BYTE_ERASED; time_align];
        time_buf[..8].copy_from_slice(&cur_time.to_le_bytes());
        if file_write(
            &db.parent,
            db.cur_sec.addr + SECTOR_START_TIME_OFFSET as u32,
            &time_buf,
            true,
        ) != FdbError::NoErr
        {
            return FdbError::WriteErr;
        }
    }

    FdbError::NoErr
}

fn tsl_append(db: &mut Tsdb, blob: &[u8], timestamp: Option<FdbTime>) -> FdbError {
    let cur_time = match timestamp {
        Some(t) => t,
        None => match db.get_time {
            Some(f) => f(),
            None => return FdbError::WriteErr,
        },
    };

    if blob.len() > db.max_len {
        return FdbError::WriteErr;
    }

    if cur_time <= db.last_time {
        return FdbError::WriteErr;
    }

    let result = update_sec_status(db, blob.len(), cur_time);
    if result != FdbError::NoErr {
        return result;
    }

    let result = write_tsl(db, blob, cur_time);
    if result != FdbError::NoErr {
        return result;
    }

    db.cur_sec.end_idx = db.cur_sec.empty_idx;
    db.cur_sec.end_time = cur_time;
    db.cur_sec.empty_idx += LOG_IDX_DATA_SIZE;
    db.cur_sec.empty_data -= wg_align(blob.len() as u32);
    let consumed = LOG_IDX_DATA_SIZE as usize + wg_align(blob.len() as u32) as usize;
    if db.cur_sec.remain > consumed {
        db.cur_sec.remain -= consumed;
    } else {
        db.cur_sec.remain = 0;
    }
    db.last_time = cur_time;

    FdbError::NoErr
}

pub fn fdb_tsl_append(db: &mut Tsdb, blob: &[u8]) -> FdbError {
    if !db.parent.init_ok {
        return FdbError::InitFailed;
    }

    db.parent.run_lock();
    let result = tsl_append(db, blob, None);
    db.parent.run_unlock();

    result
}

pub fn fdb_tsl_append_with_ts(db: &mut Tsdb, blob: &[u8], timestamp: FdbTime) -> FdbError {
    if !db.parent.init_ok {
        return FdbError::InitFailed;
    }

    db.parent.run_lock();
    let result = tsl_append(db, blob, Some(timestamp));
    db.parent.run_unlock();

    result
}

pub fn fdb_tsl_iter<F>(db: &mut Tsdb, mut callback: F)
where
    F: FnMut(&Tsl) -> bool,
{
    if !db.parent.init_ok {
        return;
    }

    let mut sec_addr = db.parent.oldest_addr;
    let mut traversed_len = 0u32;
    let mut sector = TsdbSecInfo::new();
    let mut tsl = Tsl::new();

    db.parent.run_lock();

    loop {
        traversed_len += db.parent.sec_size;
        if read_sector_info(db, sec_addr, &mut sector, false) != FdbError::NoErr {
            let next = get_next_sector_addr(db, &sector, traversed_len);
            if next == FAILED_ADDR {
                break;
            }
            sec_addr = next;
            continue;
        }

        if sector.status == SectorStoreStatus::Using || sector.status == SectorStoreStatus::Full {
            if sector.status == SectorStoreStatus::Using {
                sector = db.cur_sec;
            }
            tsl.addr.index = sector.addr + SECTOR_HDR_DATA_SIZE;

            loop {
                read_tsl(db, &mut tsl);
                if callback(&tsl) {
                    db.parent.run_unlock();
                    return;
                }
                let next = get_next_tsl_addr(&sector, &tsl);
                if next == FAILED_ADDR {
                    break;
                }
                tsl.addr.index = next;
            }
        }

        let next = get_next_sector_addr(db, &sector, traversed_len);
        if next == FAILED_ADDR {
            break;
        }
        sec_addr = next;
    }

    db.parent.run_unlock();
}

pub fn fdb_tsl_iter_reverse<F>(db: &mut Tsdb, mut callback: F)
where
    F: FnMut(&Tsl) -> bool,
{
    if !db.parent.init_ok {
        return;
    }

    let mut sec_addr = db.cur_sec.addr;
    let mut traversed_len = 0u32;
    let mut sector = TsdbSecInfo::new();
    let mut tsl = Tsl::new();

    db.parent.run_lock();

    loop {
        traversed_len += db.parent.sec_size;
        if read_sector_info(db, sec_addr, &mut sector, false) != FdbError::NoErr {
            let next = get_last_sector_addr(db, &sector, traversed_len);
            if next == FAILED_ADDR {
                break;
            }
            sec_addr = next;
            continue;
        }

        if sector.status == SectorStoreStatus::Using || sector.status == SectorStoreStatus::Full {
            if sector.status == SectorStoreStatus::Using {
                sector = db.cur_sec;
            }
            tsl.addr.index = sector.end_idx;

            loop {
                read_tsl(db, &mut tsl);
                if callback(&tsl) {
                    db.parent.run_unlock();
                    return;
                }
                let next = get_last_tsl_addr(&sector, &tsl);
                if next == FAILED_ADDR {
                    break;
                }
                tsl.addr.index = next;
            }
        } else if sector.status == SectorStoreStatus::Empty || sector.status == SectorStoreStatus::Unused {
            db.parent.run_unlock();
            return;
        }

        let next = get_last_sector_addr(db, &sector, traversed_len);
        if next == FAILED_ADDR {
            break;
        }
        sec_addr = next;
    }

    db.parent.run_unlock();
}

fn search_start_tsl_addr(db: &Tsdb, start: u32, end: u32, from: FdbTime, to: FdbTime) -> u32 {
    let mut start = start;
    let mut end = end;
    let mut tsl = Tsl::new();

    loop {
        let mid = start + align_down((end - start) / 2, LOG_IDX_DATA_SIZE);
        tsl.addr.index = mid;
        read_tsl(db, &mut tsl);

        if tsl.time < from {
            start = tsl.addr.index + LOG_IDX_DATA_SIZE;
        } else if tsl.time > from {
            end = tsl.addr.index - LOG_IDX_DATA_SIZE;
        } else {
            return tsl.addr.index;
        }

        if start > end {
            if from > to {
                tsl.addr.index = start;
                read_tsl(db, &mut tsl);
                if tsl.time > from {
                    start -= LOG_IDX_DATA_SIZE;
                }
            }
            break;
        }
    }

    start
}

pub fn fdb_tsl_iter_by_time<F>(
    db: &mut Tsdb,
    from: FdbTime,
    to: FdbTime,
    mut callback: F,
) where
    F: FnMut(&Tsl) -> bool,
{
    if !db.parent.init_ok {
        return;
    }

    let forward = from <= to;
    let start_addr = if forward {
        db.parent.oldest_addr
    } else {
        db.cur_sec.addr
    };

    let mut sec_addr = start_addr;
    let mut traversed_len = 0u32;
    let mut sector = TsdbSecInfo::new();
    let mut tsl = Tsl::new();
    let mut found_start_tsl = false;

    db.parent.run_lock();

    loop {
        traversed_len += db.parent.sec_size;
        if read_sector_info(db, sec_addr, &mut sector, false) != FdbError::NoErr {
            let next = if forward {
                get_next_sector_addr(db, &sector, traversed_len)
            } else {
                get_last_sector_addr(db, &sector, traversed_len)
            };
            if next == FAILED_ADDR {
                break;
            }
            sec_addr = next;
            continue;
        }

        if sector.status == SectorStoreStatus::Using || sector.status == SectorStoreStatus::Full {
            if sector.status == SectorStoreStatus::Using {
                sector = db.cur_sec;
            }

            let in_range = found_start_tsl
                || (!found_start_tsl
                    && ((forward
                        && ((sec_addr == start_addr && from <= sector.start_time)
                            || from <= sector.end_time))
                        || (!forward
                            && ((sec_addr == start_addr && from >= sector.end_time)
                                || from >= sector.start_time))));

            if in_range {
                let start = sector.addr + SECTOR_HDR_DATA_SIZE;
                let end = sector.end_idx;

                found_start_tsl = true;
                tsl.addr.index = search_start_tsl_addr(db, start, end, from, to);

                loop {
                    read_tsl(db, &mut tsl);
                    if tsl.status != TslStatus::Unused {
                        let time_matches = if forward {
                            tsl.time >= from && tsl.time <= to
                        } else {
                            tsl.time <= from && tsl.time >= to
                        };
                        if time_matches {
                            if callback(&tsl) {
                                db.parent.run_unlock();
                                return;
                            }
                        } else {
                            db.parent.run_unlock();
                            return;
                        }
                    }
                    let next = if forward {
                        get_next_tsl_addr(&sector, &tsl)
                    } else {
                        get_last_tsl_addr(&sector, &tsl)
                    };
                    if next == FAILED_ADDR {
                        break;
                    }
                    tsl.addr.index = next;
                }
            }
        } else if sector.status == SectorStoreStatus::Empty {
            db.parent.run_unlock();
            return;
        }

        let next = if forward {
            get_next_sector_addr(db, &sector, traversed_len)
        } else {
            get_last_sector_addr(db, &sector, traversed_len)
        };
        if next == FAILED_ADDR {
            break;
        }
        sec_addr = next;
    }

    db.parent.run_unlock();
}

pub fn fdb_tsl_query_count(
    db: &mut Tsdb,
    from: FdbTime,
    to: FdbTime,
    status: TslStatus,
) -> usize {
    if !db.parent.init_ok {
        return 0;
    }

    let mut count: usize = 0;
    fdb_tsl_iter_by_time(db, from, to, |tsl: &Tsl| {
        if tsl.status == status {
            count += 1;
        }
        false
    });

    count
}

pub fn fdb_tsl_max_blob_count(db: &Tsdb) -> usize {
    let max_blob_len = db.max_len;
    let sec_size = db.parent.sec_size as usize - SECTOR_HDR_DATA_SIZE as usize;
    let blob_size = LOG_IDX_DATA_SIZE as usize + wg_align(max_blob_len as u32) as usize;
    let n_sec = db.parent.max_size / db.parent.sec_size;

    (n_sec as usize) * (sec_size / blob_size)
}

pub fn fdb_tsl_set_status(db: &mut Tsdb, tsl: &Tsl, status: TslStatus) -> FdbError {
    let mut status_table = [0u8; 1];
    if write_status_to_flash(
        &mut status_table,
        TslStatus::STATUS_NUM as u32,
        status as u32,
        tsl.addr.index,
        |offset, byte| {
            if file_write(&db.parent, offset, &[byte], true) == FdbError::NoErr {
                Ok(())
            } else {
                Err(FdbError::WriteErr)
            }
        },
    )
    .is_err()
    {
        return FdbError::WriteErr;
    }

    FdbError::NoErr
}

pub fn fdb_tsl_to_blob(tsl: &Tsl, blob: &mut crate::blob::Blob) {
    blob.saved.addr = tsl.addr.log;
    blob.saved.meta_addr = tsl.addr.index;
    blob.saved.len = tsl.log_len as usize;
}

fn tsl_format_all(db: &mut Tsdb) {
    let mut sector = TsdbSecInfo::new();
    sector.addr = 0;
    let mut traversed_len: u32 = 0;

    loop {
        let next;
        {
            read_sector_info(db, sector.addr, &mut sector, false);
            format_sector(db, sector.addr);
            traversed_len += db.parent.sec_size;
            next = get_next_sector_addr(db, &sector, traversed_len);
        }
        if next == FAILED_ADDR {
            break;
        }
        sector.addr = next;
    }

    db.parent.oldest_addr = 0;
    db.cur_sec.addr = 0;
    db.last_time = 0;
    let mut tmp_sec = TsdbSecInfo::new();
    let _ = read_sector_info(db, 0, &mut tmp_sec, false);
    db.cur_sec = tmp_sec;
}

pub fn fdb_tsl_clean(db: &mut Tsdb) {
    db.parent.run_lock();
    tsl_format_all(db);
    db.parent.run_unlock();
}

pub enum TsdbCtrl<'a> {
    SetSecSize(u32),
    GetSecSize(&'a mut u32),
    SetLock(Box<dyn FnMut() + Send>),
    SetUnlock(Box<dyn FnMut() + Send>),
    SetRollover(bool),
    GetRollover(&'a mut bool),
    GetLastTime(&'a mut FdbTime),
    SetFileMode(bool),
    SetMaxSize(u32),
    SetNotFormat(bool),
}

pub fn fdb_tsdb_control(db: &mut Tsdb, cmd: TsdbCtrl) {
    match cmd {
        TsdbCtrl::SetSecSize(v) => {
            assert!(!db.parent.init_ok);
            db.parent.sec_size = v;
        }
        TsdbCtrl::GetSecSize(out) => {
            *out = db.parent.sec_size;
        }
        TsdbCtrl::SetLock(f) => {
            db.parent.lock = Some(f);
        }
        TsdbCtrl::SetUnlock(f) => {
            db.parent.unlock = Some(f);
        }
        TsdbCtrl::SetRollover(v) => {
            assert!(db.parent.init_ok);
            db.rollover = v;
        }
        TsdbCtrl::GetRollover(out) => {
            *out = db.rollover;
        }
        TsdbCtrl::GetLastTime(out) => {
            *out = db.last_time;
        }
        TsdbCtrl::SetFileMode(v) => {
            assert!(!db.parent.init_ok);
            db.parent.file_mode = v;
        }
        TsdbCtrl::SetMaxSize(v) => {
            assert!(!db.parent.init_ok);
            db.parent.max_size = v;
        }
        TsdbCtrl::SetNotFormat(v) => {
            assert!(!db.parent.init_ok);
            db.parent.not_formatable = v;
        }
    }
}

pub fn fdb_tsdb_init(
    db: &mut Tsdb,
    name: &str,
    path: &std::path::Path,
    get_time: GetTimeFn,
    max_len: usize,
) -> FdbError {
    let sec_size = db.parent.sec_size;
    let max_size = db.parent.max_size;
    let file_mode = db.parent.file_mode;

    let result = crate::db::db_init(
        &mut db.parent,
        name,
        path,
        crate::def::DbType::Ts,
        file_mode,
        sec_size,
        max_size,
    );
    if result != FdbError::NoErr {
        crate::db::db_init_finish(&mut db.parent, result);
        return result;
    }

    db.parent.run_lock();

    db.get_time = Some(get_time);
    db.max_len = max_len;
    db.rollover = true;
    db.parent.oldest_addr = DATA_UNUSED;
    db.cur_sec.addr = DATA_UNUSED;
    assert!(max_len < db.parent.sec_size as usize);

    let mut check_failed = false;
    let mut empty_num: u32 = 0;
    let mut empty_addr: u32 = 0;

    let mut sec_addr: u32 = 0;
    let mut traversed_len: u32 = 0;
    loop {
        let mut sector = TsdbSecInfo::new();
        let rc = read_sector_info(db, sec_addr, &mut sector, false);
        if rc == FdbError::NoErr || rc == FdbError::InitFailed {
            if sector.check_ok {
                if sector.status == SectorStoreStatus::Using {
                    if db.cur_sec.addr == DATA_UNUSED {
                        db.cur_sec = sector;
                    } else {
                        check_failed = true;
                    }
                } else if sector.status == SectorStoreStatus::Empty {
                    empty_num += 1;
                    empty_addr = sector.addr;
                    if empty_num == 1 && db.cur_sec.addr == DATA_UNUSED {
                        db.cur_sec = sector;
                    }
                }
            } else {
                check_failed = true;
            }
        }

        if check_failed {
            break;
        }

        traversed_len += db.parent.sec_size;
        let next = get_next_sector_addr(db, &sector, traversed_len);
        if next == FAILED_ADDR {
            break;
        }
        sec_addr = next;
    }

    if check_failed {
        if db.parent.not_formatable {
            db.parent.run_unlock();
            let result = FdbError::ReadErr;
            crate::db::db_init_finish(&mut db.parent, result);
            return result;
        } else {
            tsl_format_all(db);
        }
    } else {
        let latest_addr;
        if empty_num > 0 {
            latest_addr = empty_addr;
        } else {
            if db.rollover {
                latest_addr = db.cur_sec.addr;
            } else {
                db.cur_sec.addr = db.parent.max_size - db.parent.sec_size;
                latest_addr = db.cur_sec.addr;
            }
        }
        if latest_addr + db.parent.sec_size >= db.parent.max_size {
            db.parent.oldest_addr = 0;
        } else {
            db.parent.oldest_addr = latest_addr + db.parent.sec_size;
        }
    }

    let cur_addr = db.cur_sec.addr;
    let mut tmp_sec = TsdbSecInfo::new();
    let _ = read_sector_info(db, cur_addr, &mut tmp_sec, true);
    db.cur_sec = tmp_sec;

    if db.cur_sec.status == SectorStoreStatus::Using {
        db.last_time = db.cur_sec.end_time;
    } else if db.cur_sec.status == SectorStoreStatus::Empty
        && db.parent.oldest_addr != db.cur_sec.addr
    {
        let addr = if db.cur_sec.addr == 0 {
            db.parent.max_size - db.parent.sec_size
        } else {
            db.cur_sec.addr - db.parent.sec_size
        };
        let mut sec = TsdbSecInfo::new();
        let _ = read_sector_info(db, addr, &mut sec, false);
        db.last_time = sec.end_time;
    }

    db.parent.run_unlock();

    crate::db::db_init_finish(&mut db.parent, FdbError::NoErr);

    FdbError::NoErr
}

pub fn fdb_tsdb_deinit(db: &mut Tsdb) -> FdbError {
    crate::db::db_deinit(&mut db.parent);
    FdbError::NoErr
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tsl_default_is_unused() {
        let tsl = Tsl::new();
        assert_eq!(tsl.status, TslStatus::Unused);
        assert_eq!(tsl.time, TIME_UNUSED);
        assert_eq!(tsl.log_len, 0);
    }

    #[test]
    fn tsdb_sec_info_default() {
        let sec = TsdbSecInfo::new();
        assert!(!sec.check_ok);
        assert_eq!(sec.status, SectorStoreStatus::Unused);
        assert_eq!(sec.end_idx, FAILED_ADDR);
        assert_eq!(sec.start_time, TIME_UNUSED);
        assert_eq!(sec.end_time, TIME_UNUSED);
    }

    #[test]
    fn tsdb_default_rollover_true() {
        let db = Tsdb::new();
        assert!(db.rollover);
        assert_eq!(db.last_time, 0);
        assert_eq!(db.max_len, 0);
        assert!(db.get_time.is_none());
    }
}
