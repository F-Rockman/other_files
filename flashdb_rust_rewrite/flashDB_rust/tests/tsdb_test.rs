#![allow(dead_code)]

use flashdb::blob::Blob;
use flashdb::def::{FdbTime, TslStatus};
use flashdb::error::FdbError;
use flashdb::low_lvl::wg_align;
use flashdb::tsdb::{self, Tsdb, TsdbCtrl, Tsl};
use flashdb::utils;
use std::cell::Cell;
use tempfile::TempDir;

const TEST_TS_COUNT_BASE: i64 = 256;
const TEST_TS_USER_STATUS1_COUNT: i64 = TEST_TS_COUNT_BASE / 2;
const TEST_TS_DELETED_COUNT: i64 = TEST_TS_COUNT_BASE - TEST_TS_USER_STATUS1_COUNT;
const TEST_SECTOR_SIZE: u32 = 4096;
const TEST_TIME_STEP: FdbTime = 2;
const TEST_ITER1_SECTORS: u32 = 5;
const LOGBUF_SIZE: usize = 10;
const SEC_TIME_UNUSED: FdbTime = 0x7FFFFFFF;

const SECTOR_HDR_DATA_SIZE: u32 = 44;
const LOG_IDX_DATA_SIZE: u32 = 24;

thread_local! {
    static CUR_TIMES: Cell<FdbTime> = Cell::new(0);
}

fn get_time() -> FdbTime {
    CUR_TIMES.with(|c| {
        let v = c.get() + TEST_TIME_STEP;
        c.set(v);
        v
    })
}

fn reset_cur_times() {
    CUR_TIMES.with(|c| c.set(0));
}

const fn tsdb_test_count() -> i64 {
    let logbuf_aligned = wg_align(LOGBUF_SIZE as u32);
    let per_sector_str =
        (TEST_SECTOR_SIZE - SECTOR_HDR_DATA_SIZE) / (LOG_IDX_DATA_SIZE + logbuf_aligned);
    let computed = per_sector_str * 14;
    if computed < TEST_TS_COUNT_BASE as u32 {
        computed as i64
    } else {
        TEST_TS_COUNT_BASE
    }
}

const fn tsdb_iter1_count() -> u32 {
    let int_aligned = wg_align(4);
    let per_sector =
        (TEST_SECTOR_SIZE - SECTOR_HDR_DATA_SIZE) / (LOG_IDX_DATA_SIZE + int_aligned);
    TEST_ITER1_SECTORS * per_sector
}

#[derive(Debug, Clone)]
struct TestTlsData {
    data: i32,
    time: FdbTime,
}

#[derive(Debug, Clone, Copy)]
struct TestTlsSector {
    addr: u32,
    start_time: FdbTime,
    end_time: FdbTime,
}

impl TestTlsSector {
    fn unused() -> Self {
        Self {
            addr: 0,
            start_time: SEC_TIME_UNUSED,
            end_time: 0,
        }
    }
}

fn init_tsdb() -> (TempDir, Tsdb) {
    reset_cur_times();
    let dir = TempDir::new().unwrap();
    let sec_size = TEST_SECTOR_SIZE;
    let db_size = sec_size * 16;

    let mut db = Tsdb::new();

    tsdb::fdb_tsdb_control(&mut db, TsdbCtrl::SetSecSize(sec_size));
    tsdb::fdb_tsdb_control(&mut db, TsdbCtrl::SetFileMode(true));
    tsdb::fdb_tsdb_control(&mut db, TsdbCtrl::SetMaxSize(db_size));

    let result = tsdb::fdb_tsdb_init(&mut db, "test_ts", dir.path(), get_time, 128);
    assert_eq!(result, FdbError::NoErr, "fdb_tsdb_init failed");
    (dir, db)
}

fn fdb_reboot(dir: &TempDir, db: &mut Tsdb) {
    tsdb::fdb_tsdb_deinit(db);
    let result = tsdb::fdb_tsdb_init(db, "test_ts", dir.path(), get_time, 128);
    assert_eq!(result, FdbError::NoErr);
}

#[test]
fn test_fdb_tsdb_init_ex() {
    let (_dir, _db) = init_tsdb();
}

#[test]
fn test_fdb_tsdb_deinit() {
    let (_dir, mut db) = init_tsdb();
    assert_eq!(tsdb::fdb_tsdb_deinit(&mut db), FdbError::NoErr);
}

#[test]
fn test_fdb_tsl_append() {
    let (_dir, mut db) = init_tsdb();
    let count = tsdb_test_count();
    let mut i: i64 = 0;
    while i < count * TEST_TIME_STEP {
        i += TEST_TIME_STEP;
        let logbuf = format!("{}", i);
        let result = tsdb::fdb_tsl_append(&mut db, logbuf.as_bytes());
        assert_eq!(result, FdbError::NoErr);
    }
}

fn append_test_data(db: &mut Tsdb, count: i64) {
    let mut i: i64 = 0;
    while i < count * TEST_TIME_STEP {
        i += TEST_TIME_STEP;
        let logbuf = format!("{}", i);
        let result = tsdb::fdb_tsl_append(db, logbuf.as_bytes());
        assert_eq!(result, FdbError::NoErr);
    }
}

fn append_test_data_i32(db: &mut Tsdb, count: u32) {
    for data in 0..count {
        let buf = data.to_ne_bytes();
        let result = tsdb::fdb_tsl_append(db, &buf);
        assert_eq!(result, FdbError::NoErr);
    }
}

fn collect_all_tsls(db: &mut Tsdb) -> Vec<Tsl> {
    let mut result = Vec::new();
    tsdb::fdb_tsl_iter(db, |tsl| {
        result.push(*tsl);
        false
    });
    result
}

fn read_tsl_blob_str(db: &Tsdb, tsl: &Tsl) -> String {
    let mut blob = Blob::make(vec![0u8; tsl.log_len as usize], tsl.log_len as usize);
    tsdb::fdb_tsl_to_blob(tsl, &mut blob);
    let read_len = utils::fdb_blob_read(&db.parent, &mut blob);
    let end = blob.buf[..read_len]
        .iter()
        .position(|&b| b == 0)
        .unwrap_or(read_len);
    String::from_utf8_lossy(&blob.buf[..end]).to_string()
}

fn read_tsl_blob_i32(db: &Tsdb, tsl: &Tsl) -> i32 {
    let mut blob = Blob::make(vec![0u8; 4], 4);
    tsdb::fdb_tsl_to_blob(tsl, &mut blob);
    utils::fdb_blob_read(&db.parent, &mut blob);
    i32::from_ne_bytes([blob.buf[0], blob.buf[1], blob.buf[2], blob.buf[3]])
}

fn query_cb(db: &mut Tsdb, from: FdbTime, to: FdbTime) -> Vec<TestTlsData> {
    let mut tsls = Vec::new();
    tsdb::fdb_tsl_iter_by_time(db, from, to, |tsl| {
        tsls.push(*tsl);
        false
    });
    let mut result = Vec::new();
    for tsl in &tsls {
        let data = read_tsl_blob_i32(db, tsl);
        result.push(TestTlsData {
            data,
            time: tsl.time,
        });
    }
    result
}

fn get_sector_info_cb(
    db: &mut Tsdb,
    secs_info: &mut [TestTlsSector],
    db_start: &mut FdbTime,
    db_end: &mut FdbTime,
) {
    tsdb::fdb_tsl_iter_by_time(db, 0, SEC_TIME_UNUSED, |tsl| {
        let i = (tsl.addr.log / TEST_SECTOR_SIZE) as usize;
        if i < secs_info.len() {
            secs_info[i].addr = (tsl.addr.log / TEST_SECTOR_SIZE) * TEST_SECTOR_SIZE;
            if secs_info[i].start_time > tsl.time {
                secs_info[i].start_time = tsl.time;
            }
            if secs_info[i].end_time < tsl.time {
                secs_info[i].end_time = tsl.time;
            }
            if *db_start > tsl.time {
                *db_start = tsl.time;
            }
            if *db_end < tsl.time {
                *db_end = tsl.time;
            }
            false
        } else {
            true
        }
    });
}

fn test_tsdb_data_by_time(
    db: &mut Tsdb,
    from: FdbTime,
    to: FdbTime,
    db_start_time: FdbTime,
    db_end_time: FdbTime,
) {
    let mut cur_time = from;
    let mut valid_to = to;

    if from <= to {
        if from < db_start_time {
            cur_time = db_start_time;
        }
        if to > db_end_time {
            valid_to = db_end_time;
        }
    } else {
        if from > db_end_time {
            cur_time = db_end_time;
        }
        if to < db_start_time {
            valid_to = db_start_time;
        }
    }

    let tsl_list = query_cb(db, from, to);
    let tsl_num = tsl_list.len();

    let mut j: usize = 0;
    if from <= to {
        let mut i = cur_time;
        while i <= valid_to {
            if i % TEST_TIME_STEP == 0 {
                j += 1;
            }
            i += 1;
        }
    } else {
        let mut i = cur_time;
        while i >= valid_to {
            if i % TEST_TIME_STEP == 0 {
                j += 1;
            }
            i -= 1;
        }
    }
    assert_eq!(tsl_num, j, "tsl number mismatch for from={}, to={}", from, to);

    let mut last_tsl_time: FdbTime = 0;
    for tls in &tsl_list {
        if from <= to {
            let expected = ((cur_time + TEST_TIME_STEP - 1) / TEST_TIME_STEP) * TEST_TIME_STEP;
            assert_eq!(
                tls.time, expected,
                "tsl time mismatch (forward) cur_time={}",
                cur_time
            );
            cur_time += TEST_TIME_STEP;
        } else {
            let expected = (cur_time / TEST_TIME_STEP) * TEST_TIME_STEP;
            assert_eq!(
                tls.time, expected,
                "tsl time mismatch (reverse) cur_time={}",
                cur_time
            );
            cur_time -= TEST_TIME_STEP;
        }
        last_tsl_time = tls.time;
    }

    if tsl_num > 0 {
        if from <= to {
            let expected = (valid_to / TEST_TIME_STEP) * TEST_TIME_STEP;
            assert_eq!(last_tsl_time, expected, "last tsl time mismatch (forward)");
        } else {
            let expected = ((valid_to + TEST_TIME_STEP - 1) / TEST_TIME_STEP) * TEST_TIME_STEP;
            assert_eq!(last_tsl_time, expected, "last tsl time mismatch (reverse)");
        }
    }
}

#[test]
fn test_fdb_tsl_iter() {
    let (dir, mut db) = init_tsdb();
    append_test_data(&mut db, tsdb_test_count());
    fdb_reboot(&dir, &mut db);
    let tsls = collect_all_tsls(&mut db);
    for tsl in &tsls {
        let data = read_tsl_blob_str(&db, tsl);
        let expected: i64 = data.parse().unwrap_or(-1);
        assert_eq!(tsl.time, expected, "tsl.time mismatch for data={}", data);
    }
}

#[test]
fn test_fdb_tsl_iter_by_time() {
    let (dir, mut db) = init_tsdb();
    append_test_data(&mut db, tsdb_test_count());
    fdb_reboot(&dir, &mut db);
    let from: FdbTime = 0;
    let to: FdbTime = tsdb_test_count() * TEST_TIME_STEP - 1;
    let mut cur = from;
    while cur <= to {
        let target = cur;
        let mut matched = Vec::new();
        tsdb::fdb_tsl_iter_by_time(&mut db, cur, cur, |tsl| {
            matched.push(*tsl);
            false
        });
        for tsl in &matched {
            assert_eq!(tsl.time, target, "time mismatch for cur={}", cur);
        }
        cur += TEST_TIME_STEP;
    }
    let tsls = collect_all_tsls(&mut db);
    for tsl in &tsls {
        let data = read_tsl_blob_str(&db, tsl);
        let expected: i64 = data.parse().unwrap_or(-1);
        assert_eq!(tsl.time, expected, "full range mismatch for data={}", data);
    }
}

#[test]
fn test_fdb_tsl_query_count() {
    let (dir, mut db) = init_tsdb();
    append_test_data(&mut db, tsdb_test_count());
    fdb_reboot(&dir, &mut db);
    let from: FdbTime = 0;
    let to: FdbTime = tsdb_test_count() * TEST_TIME_STEP;
    let count = tsdb::fdb_tsl_query_count(&mut db, from, to, TslStatus::Write);
    assert_eq!(count, tsdb_test_count() as usize);
}

#[test]
fn test_fdb_tsl_set_status() {
    let (dir, mut db) = init_tsdb();
    append_test_data(&mut db, tsdb_test_count());
    fdb_reboot(&dir, &mut db);
    let from: FdbTime = 0;
    let to: FdbTime = tsdb_test_count() * TEST_TIME_STEP;

    let mut tsls = Vec::new();
    tsdb::fdb_tsl_iter_by_time(&mut db, from, to, |tsl| {
        tsls.push(*tsl);
        false
    });
    for tsl in &tsls {
        if tsl.time >= 0 && tsl.time <= TEST_TS_USER_STATUS1_COUNT * TEST_TIME_STEP {
            assert_eq!(
                tsdb::fdb_tsl_set_status(&mut db, tsl, TslStatus::UserStatus1),
                FdbError::NoErr
            );
        } else {
            assert_eq!(
                tsdb::fdb_tsl_set_status(&mut db, tsl, TslStatus::Deleted),
                FdbError::NoErr
            );
        }
    }

    assert_eq!(
        tsdb::fdb_tsl_query_count(&mut db, from, to, TslStatus::UserStatus1),
        TEST_TS_USER_STATUS1_COUNT as usize
    );
    assert_eq!(
        tsdb::fdb_tsl_query_count(&mut db, from, to, TslStatus::Deleted),
        TEST_TS_DELETED_COUNT as usize
    );
}

#[test]
fn test_fdb_tsl_clean() {
    let (dir, mut db) = init_tsdb();
    append_test_data(&mut db, tsdb_test_count());

    reset_cur_times();
    fdb_reboot(&dir, &mut db);
    tsdb::fdb_tsl_clean(&mut db);

    let count = collect_all_tsls(&mut db).len();
    assert_eq!(count, 0);

    fdb_reboot(&dir, &mut db);
    let count = collect_all_tsls(&mut db).len();
    assert_eq!(count, 0);
}

#[test]
fn test_fdb_tsl_clean_again() {
    let (dir, mut db) = init_tsdb();
    append_test_data(&mut db, tsdb_test_count());
    fdb_reboot(&dir, &mut db);

    let from: FdbTime = 0;
    let to: FdbTime = tsdb_test_count() * TEST_TIME_STEP;
    let mut tsls = Vec::new();
    tsdb::fdb_tsl_iter_by_time(&mut db, from, to, |tsl| {
        tsls.push(*tsl);
        false
    });
    for tsl in &tsls {
        if tsl.time >= 0 && tsl.time <= TEST_TS_USER_STATUS1_COUNT * TEST_TIME_STEP {
            tsdb::fdb_tsl_set_status(&mut db, tsl, TslStatus::UserStatus1);
        } else {
            tsdb::fdb_tsl_set_status(&mut db, tsl, TslStatus::Deleted);
        }
    }

    reset_cur_times();
    fdb_reboot(&dir, &mut db);
    tsdb::fdb_tsl_clean(&mut db);

    let count = collect_all_tsls(&mut db).len();
    assert_eq!(count, 0);

    fdb_reboot(&dir, &mut db);
    let count = collect_all_tsls(&mut db).len();
    assert_eq!(count, 0);
}

fn test_fdb_tsl_sector_bound_test(
    db: &mut Tsdb,
    secs_info: &[TestTlsSector],
    start_sec_index: usize,
    end_sec_index: usize,
    db_start_time: FdbTime,
    db_end_time: FdbTime,
) {
    let s = &secs_info[start_sec_index];
    let e = &secs_info[end_sec_index];
    test_tsdb_data_by_time(db, s.start_time - 1, e.end_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time - 1, e.end_time + 0, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time - 1, e.end_time - 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time + 0, e.end_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time + 0, e.end_time + 0, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time + 0, e.end_time - 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time + 1, e.end_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time + 1, e.end_time + 0, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.start_time + 1, e.end_time - 1, db_start_time, db_end_time);

    test_tsdb_data_by_time(db, s.end_time - 1, e.start_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time - 1, e.start_time + 0, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time - 1, e.start_time - 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time + 0, e.start_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time + 0, e.start_time + 0, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time + 0, e.start_time - 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time + 1, e.start_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time + 1, e.start_time + 0, db_start_time, db_end_time);
    test_tsdb_data_by_time(db, s.end_time + 1, e.start_time - 1, db_start_time, db_end_time);
}

#[test]
fn test_fdb_tsl_iter_by_time_1() {
    let (dir, mut db) = init_tsdb();
    tsdb::fdb_tsl_clean(&mut db);
    append_test_data_i32(&mut db, tsdb_iter1_count());
    fdb_reboot(&dir, &mut db);

    let mut secs_info: [TestTlsSector; 10] = [TestTlsSector::unused(); 10];
    for i in 0..10 {
        secs_info[i].addr = TEST_SECTOR_SIZE * i as u32;
        secs_info[i].start_time = SEC_TIME_UNUSED;
        secs_info[i].end_time = 0;
    }
    let mut db_start_time: FdbTime = SEC_TIME_UNUSED;
    let mut db_end_time: FdbTime = 0;
    get_sector_info_cb(&mut db, &mut secs_info, &mut db_start_time, &mut db_end_time);

    assert_ne!(secs_info[2].start_time, SEC_TIME_UNUSED);

    test_tsdb_data_by_time(&mut db, db_start_time - 1, db_end_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, db_start_time - 2, db_start_time - 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, db_start_time - 1, db_start_time - 2, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, db_end_time + 1, db_end_time + 2, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, db_end_time + 2, db_end_time + 1, db_start_time, db_end_time);

    test_tsdb_data_by_time(&mut db, secs_info[0].start_time - 1, secs_info[0].end_time, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, secs_info[0].start_time, secs_info[0].end_time, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, secs_info[0].start_time, secs_info[0].end_time + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, secs_info[0].end_time + 1, secs_info[0].start_time, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, secs_info[0].end_time, secs_info[0].start_time, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, secs_info[0].end_time, secs_info[0].start_time - 1, db_start_time, db_end_time);

    let mut last_idx = 0;
    for i in 0..10 {
        if secs_info[i].end_time == 0 {
            last_idx = i;
            break;
        }
    }
    assert!(last_idx >= 3);
    let last_start = secs_info[last_idx].start_time;
    let last_end = secs_info[last_idx].end_time;
    test_tsdb_data_by_time(&mut db, last_start - 1, last_end, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, last_start, last_end, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, last_start, last_end + 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, last_end + 1, last_start, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, last_end, last_start, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, last_end, last_start - 1, db_start_time, db_end_time);

    test_tsdb_data_by_time(&mut db, secs_info[0].start_time + 1, secs_info[0].end_time - 1, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, secs_info[0].end_time - 1, secs_info[0].start_time + 1, db_start_time, db_end_time);

    test_tsdb_data_by_time(&mut db, secs_info[0].start_time, secs_info[0].end_time, db_start_time, db_end_time);
    test_tsdb_data_by_time(&mut db, secs_info[0].end_time, secs_info[0].start_time, db_start_time, db_end_time);

    test_fdb_tsl_sector_bound_test(&mut db, &secs_info, 0, 0, db_start_time, db_end_time);
    test_fdb_tsl_sector_bound_test(&mut db, &secs_info, 0, 1, db_start_time, db_end_time);
    test_fdb_tsl_sector_bound_test(&mut db, &secs_info, 1, 0, db_start_time, db_end_time);
    test_fdb_tsl_sector_bound_test(&mut db, &secs_info, 1, 1, db_start_time, db_end_time);

    test_fdb_tsl_sector_bound_test(&mut db, &secs_info, 0, 2, db_start_time, db_end_time);
    test_fdb_tsl_sector_bound_test(&mut db, &secs_info, 2, 0, db_start_time, db_end_time);
    test_fdb_tsl_sector_bound_test(&mut db, &secs_info, 2, 2, db_start_time, db_end_time);
}

#[test]
fn test_fdb_github_issue_249() {
    let dir = TempDir::new().unwrap();
    let sec_size: u32 = 16 * 1024;
    let db_size: u32 = 512 * 1024;

    let mut db = Tsdb::new();
    tsdb::fdb_tsdb_control(&mut db, TsdbCtrl::SetSecSize(sec_size));
    tsdb::fdb_tsdb_control(&mut db, TsdbCtrl::SetFileMode(true));
    tsdb::fdb_tsdb_control(&mut db, TsdbCtrl::SetNotFormat(false));
    tsdb::fdb_tsdb_control(&mut db, TsdbCtrl::SetMaxSize(db_size));

    let result = tsdb::fdb_tsdb_init(&mut db, "storage_tsdb", dir.path(), get_time, 10 * 1024);
    assert_eq!(result, FdbError::NoErr);

    tsdb::fdb_tsl_clean(&mut db);
    reset_cur_times();

    for &size in &[7 * 1024, 8 * 1024, 9 * 1024] {
        let data = vec![0u8; size];
        let result = tsdb::fdb_tsl_append(&mut db, &data);
        assert_eq!(result, FdbError::NoErr);
    }

    tsdb::fdb_tsdb_deinit(&mut db);
    let result = tsdb::fdb_tsdb_init(&mut db, "storage_tsdb", dir.path(), get_time, 10 * 1024);
    assert_eq!(result, FdbError::NoErr);

    assert_eq!(
        tsdb::fdb_tsl_query_count(&mut db, 2, 6, TslStatus::Write),
        3
    );
    assert_eq!(
        tsdb::fdb_tsl_query_count(&mut db, 0, i32::MAX as FdbTime, TslStatus::Write),
        3
    );
}
