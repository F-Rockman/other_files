use flashdb::blob::Blob;
use flashdb::def::{DbType, FdbDb};
use flashdb::error::FdbError;
use flashdb::kvdb::{
    self, FDB_KVDB_CTRL_SET_FILE_MODE, FDB_KVDB_CTRL_SET_MAX_SIZE, FDB_KVDB_CTRL_SET_SEC_SIZE,
    Kvdb, KvdbCtrlArg, KvIterator, KvNode,
};
use std::fs;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tempfile::TempDir;

const TEST_KVDB_SECTOR_SIZE: u32 = 4096;
const TEST_KVDB_SECTOR_NUM: u32 = 4;
const TEST_KV_MAX_NUM: usize = 8;
const TEST_KV_NAME: &str = "kv_test";
const TEST_KV_BLOB_NAME: &str = "kv_blob_test";

const SEC_HDR_DATA_SIZE: u32 = 16;
const KV_HDR_DATA_SIZE: u32 = 24;
const KV_NAME_MAX: u32 = 64;

const fn wg_align(size: u32) -> u32 {
    let w = (1u32 + 7) / 8;
    ((size + w - 1) / w) * w
}

const fn kvdb_test_value_len() -> usize {
    let name_aligned = wg_align(3);
    let usable = TEST_KVDB_SECTOR_SIZE - wg_align(SEC_HDR_DATA_SIZE);
    let base = wg_align(KV_HDR_DATA_SIZE) + name_aligned;
    let v = (usable - 3 * base + 3) / 4;
    wg_align(v) as usize
}

struct TestKv {
    name: String,
    value: Vec<u8>,
    addr: u32,
    saved_data_size: u32,
    is_changed: bool,
}

impl TestKv {
    fn new(name: &str, value_len: usize) -> Self {
        let mut value = vec![0u8; value_len];
        for (i, b) in value.iter_mut().enumerate() {
            *b = (i & 0xFF) as u8;
        }
        TestKv {
            name: name.to_string(),
            value,
            addr: 0,
            saved_data_size: 0,
            is_changed: false,
        }
    }

    fn with_str(
        name: &str,
        value_str: &str,
        value_len: usize,
        addr: u32,
        is_changed: bool,
    ) -> Self {
        let mut value = vec![0u8; value_len];
        let bytes = value_str.as_bytes();
        let copy_len = bytes.len().min(value_len);
        value[..copy_len].copy_from_slice(&bytes[..copy_len]);
        TestKv {
            name: name.to_string(),
            value,
            addr,
            saved_data_size: 0,
            is_changed,
        }
    }

    fn value_len(&self) -> usize {
        self.value.len()
    }

    fn value_str(&self) -> &str {
        let end = self
            .value
            .iter()
            .position(|&b| b == 0)
            .unwrap_or(self.value.len());
        std::str::from_utf8(&self.value[..end]).unwrap_or("")
    }
}

fn init_kvdb_with_sectors(sector_num: u32) -> (TempDir, Kvdb) {
    let dir = TempDir::new().unwrap();
    let sec_size = TEST_KVDB_SECTOR_SIZE;
    let db_size = sec_size * sector_num;
    let file_mode = true;

    let parent = FdbDb::new("", DbType::Kv, std::path::PathBuf::new());
    let mut db = Kvdb::new(parent);

    kvdb::fdb_kvdb_control(
        &mut db,
        FDB_KVDB_CTRL_SET_SEC_SIZE,
        KvdbCtrlArg::SecSize(sec_size),
    );
    kvdb::fdb_kvdb_control(
        &mut db,
        FDB_KVDB_CTRL_SET_FILE_MODE,
        KvdbCtrlArg::FileMode(file_mode),
    );
    kvdb::fdb_kvdb_control(
        &mut db,
        FDB_KVDB_CTRL_SET_MAX_SIZE,
        KvdbCtrlArg::MaxSize(db_size),
    );

    let result = kvdb::fdb_kvdb_init(&mut db, "test_kv", dir.path(), None);
    assert_eq!(result, FdbError::NoErr, "fdb_kvdb_init failed");
    (dir, db)
}

fn dir_delete(path: &std::path::Path) {
    let _ = fs::remove_dir_all(path);
}

fn test_tick_get() -> u32 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u32)
        .unwrap_or(0)
}

fn test_msleep(ms: u64) {
    std::thread::sleep(Duration::from_millis(ms));
}

fn iter_all_kv(db: &mut Kvdb) -> Vec<TestKv> {
    let mut result = Vec::new();
    let mut iterator = KvIterator::new();
    kvdb::fdb_kv_iterator_init(db, &mut iterator);
    while kvdb::fdb_kv_iterate(db, &mut iterator) {
        let cur_kv = iterator.curr_kv;
        let data_size = cur_kv.value_len as usize;
        let buf = vec![0u8; data_size];
        let mut blob = Blob::make(buf, data_size);
        kvdb::fdb_kv_to_blob(&cur_kv, &mut blob);
        let _ = flashdb::utils::fdb_blob_read(&db.parent, &mut blob);
        result.push(TestKv {
            name: cur_kv.name_str().to_string(),
            value: blob.buf,
            addr: cur_kv.addr.start,
            saved_data_size: data_size as u32,
            is_changed: false,
        });
    }
    result
}

fn test_save_fdb_by_kvs(db: &mut Kvdb, kv_tbl: &[TestKv]) {
    for kv in kv_tbl {
        if kv.is_changed {
            let blob = Blob::make(kv.value.clone(), kv.value_len());
            let result = kvdb::fdb_kv_set_blob(db, kv.name.as_bytes(), &blob);
            assert_eq!(result, FdbError::NoErr);
        }
    }
}

fn test_check_fdb_by_kvs(db: &mut Kvdb, kv_tbl: &[TestKv]) {
    let saved = iter_all_kv(db);
    for kv in kv_tbl {
        let found = saved.iter().find(|s| s.name == kv.name);
        assert!(found.is_some(), "KV '{}' not found", kv.name);
        let s = found.unwrap();
        assert_eq!(s.name, kv.name);
        assert_eq!(s.value_str(), kv.value_str());
        let aligned = flashdb::low_lvl::align_down(s.addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * kv.addr);
    }
}

fn test_fdb_by_kvs(db: &mut Kvdb, kv_tbl: &[TestKv]) {
    test_save_fdb_by_kvs(db, kv_tbl);
    test_check_fdb_by_kvs(db, kv_tbl);
}

#[test]
fn test_fdb_kvdb_init() {
    let (_dir, _db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
}

#[test]
fn test_fdb_kvdb_init_check() {
    let (_dir, db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let aligned = flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
    assert_eq!(aligned, 0);
}

#[test]
fn test_fdb_create_kv_blob() {
    let (_dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let tick = test_tick_get();

    let mut blob = Blob::make(tick.to_le_bytes().to_vec(), std::mem::size_of::<u32>());
    let result = kvdb::fdb_kv_set_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &blob);
    assert_eq!(result, FdbError::NoErr);

    let mut read_blob = Blob::make(vec![0u8; std::mem::size_of::<u32>()], std::mem::size_of::<u32>());
    let read_len = kvdb::fdb_kv_get_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &mut read_blob);
    assert_eq!(read_blob.saved.len, std::mem::size_of::<u32>());
    assert_eq!(read_blob.saved.len, read_len);
    let read_tick = u32::from_le_bytes(read_blob.buf[..4].try_into().unwrap());
    assert_eq!(tick, read_tick);

    let mut kv_obj = KvNode::new();
    assert!(kvdb::fdb_kv_get_obj(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &mut kv_obj));

    let mut value_buf = vec![0u8; std::mem::size_of::<u32>()];
    let mut read_blob2 = Blob::make(value_buf.clone(), value_buf.len());
    kvdb::fdb_kv_to_blob(&kv_obj, &mut read_blob2);
    let read_len2 = flashdb::utils::fdb_blob_read(&db.parent, &mut read_blob2);
    assert_eq!(read_len2, std::mem::size_of::<u32>());
    let value_tick = u32::from_le_bytes(read_blob2.buf[..4].try_into().unwrap());
    assert_eq!(tick, value_tick);
}

#[test]
fn test_fdb_change_kv_blob() {
    let (_dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let tick0 = test_tick_get();
    let mut blob = Blob::make(tick0.to_le_bytes().to_vec(), std::mem::size_of::<u32>());
    let result = kvdb::fdb_kv_set_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &blob);
    assert_eq!(result, FdbError::NoErr);

    let mut read_blob = Blob::make(vec![0u8; std::mem::size_of::<u32>()], std::mem::size_of::<u32>());
    let read_len = kvdb::fdb_kv_get_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &mut read_blob);
    assert_eq!(read_blob.saved.len, std::mem::size_of::<u32>());
    assert_eq!(read_blob.saved.len, read_len);
    let read_tick = u32::from_le_bytes(read_blob.buf[..4].try_into().unwrap());

    test_msleep(2);
    let tick = test_tick_get();
    assert_ne!(tick, read_tick);

    let mut set_blob = Blob::make(tick.to_le_bytes().to_vec(), std::mem::size_of::<u32>());
    let result = kvdb::fdb_kv_set_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &set_blob);
    assert_eq!(result, FdbError::NoErr);

    let mut read_blob2 = Blob::make(vec![0u8; std::mem::size_of::<u32>()], std::mem::size_of::<u32>());
    let read_len2 = kvdb::fdb_kv_get_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &mut read_blob2);
    assert_eq!(read_blob2.saved.len, std::mem::size_of::<u32>());
    assert_eq!(read_blob2.saved.len, read_len2);
    let read_tick2 = u32::from_le_bytes(read_blob2.buf[..4].try_into().unwrap());
    assert_eq!(tick, read_tick2);
}

#[test]
fn test_fdb_del_kv_blob() {
    let (_dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let tick = test_tick_get();
    let blob = Blob::make(tick.to_le_bytes().to_vec(), std::mem::size_of::<u32>());
    let result = kvdb::fdb_kv_set_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &blob);
    assert_eq!(result, FdbError::NoErr);

    let mut read_blob = Blob::make(vec![0u8; std::mem::size_of::<u32>()], std::mem::size_of::<u32>());
    let read_len = kvdb::fdb_kv_get_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &mut read_blob);
    assert_eq!(read_blob.saved.len, std::mem::size_of::<u32>());
    assert_eq!(read_blob.saved.len, read_len);
    let read_tick = u32::from_le_bytes(read_blob.buf[..4].try_into().unwrap());

    test_msleep(2);
    let tick2 = test_tick_get();
    assert_ne!(tick2, read_tick);

    let del_blob = Blob::make(vec![], 0);
    let result = kvdb::fdb_kv_set_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &del_blob);
    assert_eq!(result, FdbError::NoErr);

    let mut read_blob2 = Blob::make(vec![0u8; std::mem::size_of::<u32>()], std::mem::size_of::<u32>());
    let read_len2 = kvdb::fdb_kv_get_blob(&mut db, TEST_KV_BLOB_NAME.as_bytes(), &mut read_blob2);
    assert_eq!(read_blob2.saved.len, 0);
    assert_eq!(read_len2, 0);
}

#[test]
fn test_fdb_create_kv() {
    let (_dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let tick = test_tick_get();
    let value_buf = tick.to_string();

    let result = kvdb::fdb_kv_set(&mut db, TEST_KV_NAME.as_bytes(), Some(&value_buf));
    assert_eq!(result, FdbError::NoErr);

    let read_value = kvdb::fdb_kv_get(&mut db, TEST_KV_NAME.as_bytes());
    assert!(read_value.is_some());
    let read_tick: u32 = read_value.unwrap()
        .iter()
        .map(|&b| b as char)
        .collect::<String>()
        .parse()
        .unwrap();
    assert_eq!(tick, read_tick);
}

#[test]
fn test_fdb_change_kv() {
    let (_dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let tick0 = test_tick_get();
    let value_buf0 = tick0.to_string();
    let result = kvdb::fdb_kv_set(&mut db, TEST_KV_NAME.as_bytes(), Some(&value_buf0));
    assert_eq!(result, FdbError::NoErr);

    let read_value = kvdb::fdb_kv_get(&mut db, TEST_KV_NAME.as_bytes());
    assert!(read_value.is_some());
    let read_tick: u32 = read_value
        .unwrap()
        .iter()
        .map(|&b| b as char)
        .collect::<String>()
        .parse()
        .unwrap();

    test_msleep(2);
    let tick = test_tick_get();
    assert_ne!(tick, read_tick);

    let value_buf = tick.to_string();
    let result = kvdb::fdb_kv_set(&mut db, TEST_KV_NAME.as_bytes(), Some(&value_buf));
    assert_eq!(result, FdbError::NoErr);

    let read_value = kvdb::fdb_kv_get(&mut db, TEST_KV_NAME.as_bytes());
    assert!(read_value.is_some());
    let read_tick2: u32 = read_value
        .unwrap()
        .iter()
        .map(|&b| b as char)
        .collect::<String>()
        .parse()
        .unwrap();
    assert_eq!(tick, read_tick2);
}

fn fdb_reboot(dir: &TempDir, db: &mut Kvdb) {
    let _ = kvdb::fdb_kvdb_deinit(db);
    let result = kvdb::fdb_kvdb_init(db, "test_kv", dir.path(), None);
    assert_eq!(result, FdbError::NoErr, "fdb_kvdb_init failed after reboot");
}

#[test]
fn test_fdb_del_kv() {
    let (dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let tick0 = test_tick_get();
    let value_buf0 = tick0.to_string();
    let result = kvdb::fdb_kv_set(&mut db, TEST_KV_NAME.as_bytes(), Some(&value_buf0));
    assert_eq!(result, FdbError::NoErr);

    let read_value = kvdb::fdb_kv_get(&mut db, TEST_KV_NAME.as_bytes());
    assert!(read_value.is_some());
    let read_tick: u32 = read_value
        .unwrap()
        .iter()
        .map(|&b| b as char)
        .collect::<String>()
        .parse()
        .unwrap();

    test_msleep(2);
    let tick = test_tick_get();
    assert_ne!(tick, read_tick);

    let result = kvdb::fdb_kv_del(&mut db, TEST_KV_NAME.as_bytes());
    assert_eq!(result, FdbError::NoErr);

    let read_value = kvdb::fdb_kv_get(&mut db, TEST_KV_NAME.as_bytes());
    assert!(read_value.is_none());

    let aligned =
        flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
    assert_eq!(aligned, 0);

    fdb_reboot(&dir, &mut db);

    let aligned =
        flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
    assert_eq!(aligned, 0);
}

#[test]
fn test_fdb_gc_prepare_phases() {
    let (dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let vlen = kvdb_test_value_len();

    {
        let kv_tbl = [
            TestKv::with_str("kv0", "0", vlen, 0, true),
            TestKv::with_str("kv1", "1", vlen, 0, true),
            TestKv::with_str("kv2", "2", vlen, 0, true),
            TestKv::with_str("kv3", "3", vlen, 1, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
    }

    {
        let kv_tbl = [
            TestKv::with_str("kv1", "1", vlen, 0, false),
            TestKv::with_str("kv2", "2", vlen, 0, false),
            TestKv::with_str("kv0", "00", vlen, 1, true),
            TestKv::with_str("kv3", "33", vlen, 1, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
    }
}

#[test]
fn test_fdb_gc() {
    let (dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let vlen = kvdb_test_value_len();

    {
        let kv_tbl = [
            TestKv::with_str("kv0", "0", vlen, 0, true),
            TestKv::with_str("kv1", "1", vlen, 0, true),
            TestKv::with_str("kv2", "2", vlen, 0, true),
            TestKv::with_str("kv3", "3", vlen, 1, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
    }

    {
        let kv_tbl = [
            TestKv::with_str("kv1", "1", vlen, 0, false),
            TestKv::with_str("kv2", "2", vlen, 0, false),
            TestKv::with_str("kv0", "00", vlen, 1, true),
            TestKv::with_str("kv3", "33", vlen, 1, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
    }

    {
        let kv_tbl = [
            TestKv::with_str("kv0", "000", vlen, 2, true),
            TestKv::with_str("kv1", "111", vlen, 2, true),
            TestKv::with_str("kv2", "222", vlen, 2, true),
            TestKv::with_str("kv3", "333", vlen, 3, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 1);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 1);
    }

    {
        let kv_tbl = [
            TestKv::with_str("kv0", "0000", vlen, 3, true),
            TestKv::with_str("kv1", "1111", vlen, 3, true),
            TestKv::with_str("kv2", "2222", vlen, 0, true),
            TestKv::with_str("kv3", "3333", vlen, 0, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 2);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 2);
    }
}

#[test]
fn test_fdb_gc2() {
    let (dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let vlen = kvdb_test_value_len();
    let _ = kvdb::fdb_kv_set_default(&mut db);

    {
        let kv_tbl = [
            TestKv::with_str("kv0", "0", vlen, 0, true),
            TestKv::with_str("kv1", "1", vlen, 0, true),
            TestKv::with_str("kv2", "2", vlen, 0, true),
            TestKv::with_str("kv3", "3", vlen, 1, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
    }

    {
        let kv_tbl = [
            TestKv::with_str("kv1", "1", vlen, 0, false),
            TestKv::with_str("kv2", "2", vlen, 0, false),
            TestKv::with_str("kv0", "00", vlen, 1, true),
            TestKv::with_str("kv3", "33", vlen, 1, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
    }

    {
        let kv_tbl = [
            TestKv::with_str("kv1", "1", vlen, 0, false),
            TestKv::with_str("kv2", "2", vlen, 0, false),
            TestKv::with_str("kv0", "00", vlen, 1, false),
            TestKv::with_str("kv3", "33", vlen, 1, false),
            TestKv::with_str("kv4", "4", vlen * 3, 2, true),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 0);
    }

    {
        let kv_tbl = [
            TestKv::with_str("kv3", "33", vlen, 0, false),
            TestKv::with_str("kv5", "5", vlen * 2, 0, true),
            TestKv::with_str("kv4", "4", vlen * 3, 2, false),
            TestKv::with_str("kv1", "1", vlen, 3, false),
            TestKv::with_str("kv2", "2", vlen, 3, false),
            TestKv::with_str("kv0", "00", vlen, 3, false),
        ];
        test_fdb_by_kvs(&mut db, &kv_tbl);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 2);
        fdb_reboot(&dir, &mut db);
        let aligned =
            flashdb::low_lvl::align_down(db.parent.oldest_addr, TEST_KVDB_SECTOR_SIZE);
        assert_eq!(aligned, TEST_KVDB_SECTOR_SIZE * 2);
    }
}

#[test]
fn kvdb_test_value_len_meets_gc_constraint() {
    let v = kvdb_test_value_len() as u32;
    let name_aligned = wg_align(3);
    let usable = TEST_KVDB_SECTOR_SIZE - wg_align(SEC_HDR_DATA_SIZE);
    let base = wg_align(KV_HDR_DATA_SIZE) + name_aligned;
    let v_min = (usable - 3 * base + 3) / 4;
    assert!(
        v >= v_min,
        "kvdb_test_value_len {} < gc constraint minimum {}",
        v,
        v_min
    );
}

#[test]
fn kvdb_test_value_len_exactly_3_kvs_per_sector() {
    let v = kvdb_test_value_len() as u32;
    let name_aligned = wg_align(3);
    let usable = TEST_KVDB_SECTOR_SIZE - wg_align(SEC_HDR_DATA_SIZE);
    let base = wg_align(KV_HDR_DATA_SIZE) + name_aligned;
    let kv_size = base + wg_align(v);
    let three_kvs = 3 * kv_size;
    let four_kvs = 4 * kv_size;
    assert!(
        three_kvs <= usable,
        "3 KVs ({}) should fit in usable space ({})",
        three_kvs,
        usable
    );
    assert!(
        four_kvs > usable,
        "4 KVs ({}) should NOT fit in usable space ({})",
        four_kvs,
        usable
    );
}

#[test]
fn test_fdb_scale_up() {
    let (dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    let vlen = kvdb_test_value_len();
    let _ = kvdb::fdb_kv_set_default(&mut db);

    let old_kv_tbl = [
        TestKv::with_str("kv0", "0", vlen, 0, true),
        TestKv::with_str("kv1", "1", vlen, 0, true),
        TestKv::with_str("kv2", "2", vlen, 0, true),
        TestKv::with_str("kv3", "3", vlen, 1, true),
    ];
    test_save_fdb_by_kvs(&mut db, &old_kv_tbl);

    let _ = kvdb::fdb_kvdb_deinit(&mut db);
    let db_size = TEST_KVDB_SECTOR_SIZE * 8;
    kvdb::fdb_kvdb_control(
        &mut db,
        FDB_KVDB_CTRL_SET_MAX_SIZE,
        KvdbCtrlArg::MaxSize(db_size),
    );
    let result = kvdb::fdb_kvdb_init(&mut db, "test_kv", dir.path(), None);
    assert_eq!(result, FdbError::NoErr);

    test_check_fdb_by_kvs(&mut db, &old_kv_tbl);

    let new_kv_tbl = [
        TestKv::with_str("kv4", "4", vlen, 4, true),
        TestKv::with_str("kv5", "5", vlen, 4, true),
        TestKv::with_str("kv6", "6", vlen, 4, true),
        TestKv::with_str("kv7", "7", vlen, 5, true),
    ];
    test_save_fdb_by_kvs(&mut db, &new_kv_tbl);
    test_save_fdb_by_kvs(&mut db, &new_kv_tbl);
    test_save_fdb_by_kvs(&mut db, &new_kv_tbl);
    test_check_fdb_by_kvs(&mut db, &new_kv_tbl);
    test_check_fdb_by_kvs(&mut db, &old_kv_tbl);
}

#[test]
fn test_fdb_kvdb_set_default() {
    let (_dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    assert_eq!(kvdb::fdb_kv_set_default(&mut db), FdbError::NoErr);
}

#[test]
fn test_fdb_kvdb_deinit() {
    let (_dir, mut db) = init_kvdb_with_sectors(TEST_KVDB_SECTOR_NUM);
    assert_eq!(kvdb::fdb_kvdb_deinit(&mut db), FdbError::NoErr);
}
