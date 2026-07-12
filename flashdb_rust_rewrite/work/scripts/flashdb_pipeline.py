#!/usr/bin/env python3
"""FlashDB C-to-Rust rewrite pipeline.

This script is intentionally dependency-free. It gives an evaluator LLM a
single non-interactive command surface for preflight checks, status reporting,
verification, report generation, and in-place execution healing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
RUST = REPO / "flashDB_rust"
WORK = REPO / "work"
STATE = WORK / "state"
PROGRESS = STATE / "task_progress"
LOGS = WORK / "logs"
RESULT = REPO / "result"
ISSUES = RESULT / "issues"

HEAL_FOCUS_MAX_LINES = 90

EXPECTED_MODULES = [
    "lib.rs",
    "error.rs",
    "def.rs",
    "blob.rs",
    "low_lvl.rs",
    "utils.rs",
    "file_backend.rs",
    "db.rs",
    "kvdb.rs",
    "tsdb.rs",
]

EXPECTED_TEST_FILES = ["kvdb_test.rs", "tsdb_test.rs"]

REQUIRED_SYMBOLS = {
    "kvdb.rs": [
        "create_kv_blob",
        "set_kv",
        "fdb_kv_set",
        "fdb_kv_del",
        "fdb_kv_set_blob",
        "fdb_kv_set_default",
        "fdb_kvdb_init",
        "fdb_kvdb_check",
        "fdb_kvdb_control",
        "fdb_kvdb_deinit",
        "fdb_kv_iterator_init",
        "fdb_kv_iterate",
    ],
    "tsdb.rs": [
        "fdb_tsdb_init",
        "fdb_tsdb_control",
        "fdb_tsdb_deinit",
        "fdb_tsl_append",
        "fdb_tsl_append_with_ts",
        "fdb_tsl_iter",
        "fdb_tsl_iter_reverse",
        "fdb_tsl_iter_by_time",
        "fdb_tsl_query_count",
        "fdb_tsl_max_blob_count",
        "fdb_tsl_set_status",
        "fdb_tsl_clean",
        "fdb_tsl_to_blob",
    ],
}

STUB_MARKERS = [
    "Stub",
    "stub",
    "implementation pending",
    "TODO",
    "todo!",
    "unimplemented!",
    "placeholder",
]

EXPECTED_CASES = [
    ("kvdb_init", ["test_fdb_kvdb_init"]),
    ("kvdb_init_check", ["test_fdb_kvdb_init_check"]),
    ("kvdb_create_blob", ["test_fdb_create_kv_blob"]),
    ("kvdb_change_blob", ["test_fdb_change_kv_blob"]),
    ("kvdb_del_blob", ["test_fdb_del_kv_blob"]),
    ("kvdb_create_string", ["test_fdb_create_kv"]),
    ("kvdb_change_string", ["test_fdb_change_kv"]),
    ("kvdb_del_string", ["test_fdb_del_kv"]),
    ("kvdb_gc", ["test_fdb_gc"]),
    ("kvdb_gc2", ["test_fdb_gc2"]),
    ("kvdb_scale_up", ["test_fdb_scale_up"]),
    ("kvdb_set_default", ["test_fdb_kvdb_set_default"]),
    ("kvdb_deinit", ["test_fdb_kvdb_deinit"]),
    ("tsdb_init_ex", ["test_fdb_tsdb_init_ex"]),
    ("tsdb_clean_initial", ["test_fdb_tsl_clean"]),
    ("tsdb_append", ["test_fdb_tsl_append"]),
    ("tsdb_iter", ["test_fdb_tsl_iter"]),
    ("tsdb_iter_by_time", ["test_fdb_tsl_iter_by_time"]),
    ("tsdb_query_count", ["test_fdb_tsl_query_count"]),
    ("tsdb_set_status", ["test_fdb_tsl_set_status"]),
    ("tsdb_clean_after_status", [
        "test_fdb_tsl_clean_again",
        "test_fdb_tsl_clean_2",
        "test_fdb_tsl_clean_restart",
    ]),
    ("tsdb_iter_by_time_1", ["test_fdb_tsl_iter_by_time_1"]),
    ("tsdb_deinit", ["test_fdb_tsdb_deinit"]),
    ("tsdb_github_issue_249", ["test_fdb_github_issue_249"]),
]

REQUIRED_C_MATERIAL = [
    "src/fdb.c",
    "src/fdb_file.c",
    "src/fdb_utils.c",
    "src/fdb_kvdb.c",
    "src/fdb_tsdb.c",
    "inc/fdb_def.h",
    "inc/flashdb.h",
    "inc/fdb_low_lvl.h",
    "tests/fdb_kvdb_tc.c",
    "tests/fdb_tsdb_tc.c",
]

MICRO_TASKS = [
    {
        "id": "T00-skeleton",
        "title": "Create Cargo skeleton and module stubs",
        "read": ["INSTRUCTION.md:1-140"],
        "write": ["flashDB_rust/Cargo.toml", "flashDB_rust/src/lib.rs"],
        "done": ["flashDB_rust/Cargo.toml", "flashDB_rust/src/lib.rs"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 140,
    },
    {
        "id": "T01-error-def-core",
        "title": "Port error enum and core constants/enums",
        "read": ["inc/fdb_def.h:80-170"],
        "write": ["flashDB_rust/src/error.rs", "flashDB_rust/src/def.rs"],
        "symbols": ["FdbError", "DbType", "KvStatus", "TslStatus"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 100,
    },
    {
        "id": "T02-def-blob-db-structs",
        "title": "Port remaining shared structs and Blob",
        "read": ["inc/fdb_def.h:171-332"],
        "write": ["flashDB_rust/src/def.rs", "flashDB_rust/src/blob.rs"],
        "symbols": ["FdbTime", "GetTimeFn", "struct Blob"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 170,
    },
    {
        "id": "T03-low-level-status",
        "title": "Port alignment and status-table helpers",
        "read": ["inc/fdb_low_lvl.h:1-80", "src/fdb_utils.c:91-180"],
        "write": ["flashDB_rust/src/low_lvl.rs"],
        "symbols": ["fdb_wg_align", "set_status", "get_status", "write_status_to_flash"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 180,
    },
    {
        "id": "T04-utils-crc",
        "title": "Port CRC32 helper",
        "read": ["src/fdb_utils.c:1-90"],
        "write": ["flashDB_rust/src/utils.rs"],
        "symbols": ["calc_crc32"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 100,
    },
    {
        "id": "T05-file-backend-open-read-write",
        "title": "Port file backend path/open/read/write",
        "read": ["src/fdb_file.c:1-180"],
        "write": ["flashDB_rust/src/file_backend.rs"],
        "symbols": ["file_read", "file_write"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 190,
    },
    {
        "id": "T06-file-erase",
        "title": "Port file erase helpers",
        "read": ["src/fdb_file.c:181-315"],
        "write": ["flashDB_rust/src/file_backend.rs"],
        "symbols": ["file_erase"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 150,
    },
    {
        "id": "T06a-db-init",
        "title": "Port base DB init and deinit",
        "read": ["src/fdb.c:1-117"],
        "write": ["flashDB_rust/src/db.rs"],
        "symbols": ["db_init", "db_init_finish", "db_deinit"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T07-kvdb-types-layout",
        "title": "Create KVDB structs and byte layout helpers",
        "read": ["src/fdb_kvdb.c:102-146", "inc/fdb_def.h:120-260"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["struct Kvdb", "struct KvNode", "struct KvdbSecInfo", "struct KvHdrData"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 190,
    },
    {
        "id": "T08-kvdb-read-kv",
        "title": "Port KV address scan and read_kv",
        "read": ["src/fdb_kvdb.c:280-415"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["find_next_kv_addr", "get_next_kv_addr", "read_kv"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 150,
    },
    {
        "id": "T09-kvdb-sector-info",
        "title": "Port KV sector info and next-sector logic",
        "read": ["src/fdb_kvdb.c:416-527"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["read_sector_info", "get_next_sector_addr"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T10-kvdb-cache",
        "title": "Port KVDB cache helpers",
        "read": ["src/fdb_kvdb.c:151-279"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["update_sector_cache", "get_sector_from_cache", "update_kv_cache", "get_kv_from_cache"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 140,
    },
    {
        "id": "T11-kvdb-iterator-find",
        "title": "Port KV iterator and lookup helpers",
        "read": ["src/fdb_kvdb.c:528-621"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["kv_iterator", "find_kv_no_cache", "find_kv", "fdb_is_str"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 110,
    },
    {
        "id": "T12-kvdb-get-blob",
        "title": "Port KV get/get_blob APIs",
        "read": ["src/fdb_kvdb.c:622-754"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["get_kv", "fdb_kv_get_obj", "fdb_kv_to_blob", "fdb_kv_get_blob", "fdb_kv_get"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 150,
    },
    {
        "id": "T13-kvdb-header-write-format",
        "title": "Port KV header write and sector format",
        "read": ["src/fdb_kvdb.c:755-828"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["write_kv_hdr", "format_sector"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 90,
    },
    {
        "id": "T14-kvdb-status-alloc",
        "title": "Port sector status, sector iteration, and KV allocation",
        "read": ["src/fdb_kvdb.c:829-939"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["update_sec_status", "sector_iterator", "alloc_kv"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T15-kvdb-delete-move-new",
        "title": "Port KV delete, move, and new allocation",
        "read": ["src/fdb_kvdb.c:940-1097"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["del_kv", "move_kv", "new_kv", "new_kv_ex"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 170,
    },
    {
        "id": "T16-kvdb-gc",
        "title": "Port KVDB garbage collection",
        "read": ["src/fdb_kvdb.c:1098-1183"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["do_gc", "gc_collect_by_free_size", "gc_collect"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 100,
    },
    {
        "id": "T17-kvdb-public-create-delete",
        "title": "Port KV create and delete APIs",
        "read": ["src/fdb_kvdb.c:1184-1294"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["create_kv_blob", "fdb_kv_del"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T17a-kvdb-public-set-default",
        "title": "Port KV set/blob/default APIs",
        "read": ["src/fdb_kvdb.c:1295-1431"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["set_kv", "fdb_kv_set", "fdb_kv_set_blob", "fdb_kv_set_default"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 150,
    },
    {
        "id": "T18-kvdb-print-auto-update",
        "title": "Port KVDB print and default auto-update helpers",
        "read": ["src/fdb_kvdb.c:1432-1545"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["fdb_kv_print", "kv_auto_update"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T18a-kvdb-load-recovery",
        "title": "Port KVDB load and recovery callbacks",
        "read": ["src/fdb_kvdb.c:1546-1663"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["check_oldest_addr_cb", "check_sec_hdr_cb", "check_and_recovery_gc_cb", "check_and_recovery_kv_cb", "_fdb_kv_load"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T18b-kvdb-control-init-deinit",
        "title": "Port KVDB control, init, and deinit",
        "read": ["src/fdb_kvdb.c:1665-1828"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["fdb_kvdb_control", "fdb_kvdb_init", "fdb_kvdb_deinit"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 180,
    },
    {
        "id": "T19-kvdb-iterator-check",
        "title": "Port KVDB iterator and integrity check APIs",
        "read": ["src/fdb_kvdb.c:1831-1944"],
        "write": ["flashDB_rust/src/kvdb.rs"],
        "symbols": ["fdb_kv_iterator_init", "fdb_kv_iterate", "fdb_kvdb_check"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T20-tsdb-types-read",
        "title": "Create TSDB structs and layout constants from TSDB source",
        "read": ["src/fdb_tsdb.c:1-146"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["struct Tsdb", "struct Tsl", "struct TsdbSecInfo"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 160,
    },
    {
        "id": "T21-tsdb-basic-readers",
        "title": "Port TSDB basic TSL and address readers",
        "read": ["src/fdb_tsdb.c:147-241"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["read_tsl", "get_next_sector_addr", "get_next_tsl_addr", "get_last_tsl_addr"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 110,
    },
    {
        "id": "T22-tsdb-sector-format",
        "title": "Port TSDB sector info, format, and sector iteration",
        "read": ["src/fdb_tsdb.c:242-349"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["read_sector_info", "format_sector", "sector_iterator"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 130,
    },
    {
        "id": "T23-tsdb-append",
        "title": "Port TSDB write_tsl, update_sec_status, and append APIs",
        "read": ["src/fdb_tsdb.c:350-489"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["write_tsl", "update_sec_status", "fdb_tsl_append", "fdb_tsl_append_with_ts"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 160,
    },
    {
        "id": "T24-tsdb-iterate",
        "title": "Port TSDB forward and reverse iterators",
        "read": ["src/fdb_tsdb.c:490-620"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["fdb_tsl_iter", "fdb_tsl_iter_reverse"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 150,
    },
    {
        "id": "T25-tsdb-query",
        "title": "Port TSDB time-range iteration and query count",
        "read": ["src/fdb_tsdb.c:621-770"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["fdb_tsl_iter_by_time", "fdb_tsl_query_count", "fdb_tsl_max_blob_count"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 170,
    },
    {
        "id": "T26-tsdb-status-clean-control",
        "title": "Port TSDB set_status, to_blob, clean, and control",
        "read": ["src/fdb_tsdb.c:771-930"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["fdb_tsl_set_status", "fdb_tsl_to_blob", "fdb_tsl_clean", "fdb_tsdb_control"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 180,
    },
    {
        "id": "T27-tsdb-init-deinit",
        "title": "Port TSDB init and deinit",
        "read": ["src/fdb_tsdb.c:931-1117"],
        "write": ["flashDB_rust/src/tsdb.rs"],
        "symbols": ["fdb_tsdb_init", "fdb_tsdb_deinit"],
        "check": "cargo check --manifest-path flashDB_rust/Cargo.toml",
        "max_read_lines": 200,
    },
    {
        "id": "T28-kvdb-tests-harness",
        "title": "Create KVDB Rust test harness, fixtures, and dynamic value sizing",
        "read": ["tests/fdb_kvdb_tc.c:1-178"],
        "write": ["flashDB_rust/tests/kvdb_test.rs"],
        "symbols": ["dir_delete", "kvdb_test_value_len", "init_kvdb_with_sectors"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test kvdb_test",
        "max_read_lines": 190,
    },
    {
        "id": "T28a-kvdb-tests-basic",
        "title": "Migrate basic KVDB init/blob/string/delete tests",
        "read": ["tests/fdb_kvdb_tc.c:179-358"],
        "write": ["flashDB_rust/tests/kvdb_test.rs"],
        "symbols": ["test_fdb_kvdb_init", "test_fdb_kvdb_init_check", "test_fdb_create_kv_blob", "test_fdb_change_kv_blob", "test_fdb_del_kv_blob", "test_fdb_create_kv", "test_fdb_change_kv", "test_fdb_del_kv"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test kvdb_test",
        "max_read_lines": 190,
    },
    {
        "id": "T28b-kvdb-tests-iteration-helpers",
        "title": "Migrate KVDB test iteration and assertion helpers",
        "read": ["tests/fdb_kvdb_tc.c:359-435"],
        "write": ["flashDB_rust/tests/kvdb_test.rs"],
        "symbols": ["iter_all_kv", "test_save_fdb_by_kvs", "test_check_fdb_by_kvs", "test_fdb_by_kvs"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test kvdb_test",
        "max_read_lines": 90,
    },
    {
        "id": "T29-kvdb-tests-gc1-prepare",
        "title": "Migrate first KVDB GC scenario preparation phases",
        "read": ["tests/fdb_kvdb_tc.c:437-512"],
        "write": ["flashDB_rust/tests/kvdb_test.rs"],
        "symbols": ["test_fdb_gc_prepare_phases"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test kvdb_test",
        "max_read_lines": 90,
    },
    {
        "id": "T29b-kvdb-tests-gc1-trigger",
        "title": "Migrate first KVDB GC scenario trigger phases",
        "read": ["tests/fdb_kvdb_tc.c:513-694"],
        "write": ["flashDB_rust/tests/kvdb_test.rs"],
        "symbols": ["test_fdb_gc"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test kvdb_test",
        "max_read_lines": 200,
    },
    {
        "id": "T29a-kvdb-tests-gc2",
        "title": "Migrate second KVDB GC scenario with large values",
        "read": ["tests/fdb_kvdb_tc.c:695-887"],
        "write": ["flashDB_rust/tests/kvdb_test.rs"],
        "symbols": ["test_fdb_gc2"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test kvdb_test",
        "max_read_lines": 200,
    },
    {
        "id": "T30-kvdb-tests-scale-default",
        "title": "Migrate KVDB scale-up/default/deinit registration tests",
        "read": ["tests/fdb_kvdb_tc.c:889-968"],
        "write": ["flashDB_rust/tests/kvdb_test.rs"],
        "symbols": ["test_fdb_scale_up", "test_fdb_kvdb_set_default", "test_fdb_kvdb_deinit"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test kvdb_test",
        "max_read_lines": 100,
    },
    {
        "id": "T31-tsdb-tests-harness",
        "title": "Create TSDB Rust test harness and dynamic count helpers",
        "read": ["tests/fdb_tsdb_tc.c:1-82"],
        "write": ["flashDB_rust/tests/tsdb_test.rs"],
        "symbols": ["get_time", "tsdb_test_count"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test tsdb_test",
        "max_read_lines": 100,
    },
    {
        "id": "T31a-tsdb-tests-basic",
        "title": "Migrate basic TSDB behavior tests",
        "read": ["tests/fdb_tsdb_tc.c:83-223"],
        "write": ["flashDB_rust/tests/tsdb_test.rs"],
        "symbols": ["test_fdb_tsdb_init_ex", "test_fdb_tsdb_deinit", "test_fdb_tsl_clean", "test_fdb_tsl_append", "test_fdb_tsl_iter", "test_fdb_tsl_iter_by_time", "test_fdb_tsl_query_count", "test_fdb_tsl_set_status"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test tsdb_test",
        "max_read_lines": 160,
    },
    {
        "id": "T31b-tsdb-tests-time-helpers",
        "title": "Migrate TSDB time-query helper callbacks",
        "read": ["tests/fdb_tsdb_tc.c:238-344"],
        "write": ["flashDB_rust/tests/tsdb_test.rs"],
        "symbols": ["query_cb", "get_sector_info_cb", "test_tsdb_data_by_time"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test tsdb_test",
        "max_read_lines": 120,
    },
    {
        "id": "T32-tsdb-tests-boundary",
        "title": "Migrate TSDB boundary, deinit, and issue regression tests",
        "read": ["tests/fdb_tsdb_tc.c:346-515"],
        "write": ["flashDB_rust/tests/tsdb_test.rs"],
        "symbols": ["test_fdb_tsl_clean_again", "test_fdb_tsl_iter_by_time_1", "test_fdb_tsdb_deinit", "test_fdb_github_issue_249"],
        "check": "cargo test --manifest-path flashDB_rust/Cargo.toml --test tsdb_test",
        "max_read_lines": 190,
    },
    {
        "id": "T33-final-verify",
        "title": "Run strict verification and finalize reports",
        "read": ["work/state/next_actions.md:1-120"],
        "write": ["result/output.md", "result/issues/00-summary.md"],
        "done": ["result/output.md", "result/issues/00-summary.md"],
        "check": "python3 work/scripts/flashdb_pipeline.py verify --strict",
        "max_read_lines": 120,
    },
]


def ensure_dirs() -> None:
    for path in (WORK, STATE, PROGRESS, LOGS, RESULT, ISSUES):
        path.mkdir(parents=True, exist_ok=True)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return {
            "cmd": cmd,
            "cwd": str(cwd or REPO),
            "code": proc.returncode,
            "output": proc.stdout or "",
        }
    except FileNotFoundError:
        return {"cmd": cmd, "cwd": str(cwd or REPO), "code": 127, "output": "command not found"}
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": cmd,
            "cwd": str(cwd or REPO),
            "code": 124,
            "output": (exc.stdout or "") + "\nTIMEOUT",
        }


def write_log(name: str, text: str) -> None:
    ensure_dirs()
    (LOGS / name).write_text(text, encoding="utf-8")


def error_fingerprint(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    first_error = next((line for line in lines if line.startswith("error")), "")
    material = first_error or "\n".join(lines[:4]) or "unknown failure"
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


RANGE_RE = re.compile(r"^(?P<path>[^:]+):(?P<start>\d+)-(?P<end>\d+)$")


def read_range_line_count(item: str) -> int:
    match = RANGE_RE.match(item)
    if not match:
        return 0
    start = int(match.group("start"))
    end = int(match.group("end"))
    return max(0, end - start + 1)


def task_read_line_count(task: dict[str, Any]) -> int:
    return sum(read_range_line_count(item) for item in task.get("read", []))


def task_read_budget(task: dict[str, Any]) -> int:
    return int(task.get("max_read_lines", 200))


def task_budget_ok(task: dict[str, Any]) -> bool:
    return task_read_line_count(task) <= task_read_budget(task)


def symbol_found(text: str, symbol: str) -> bool:
    if symbol.startswith("test_"):
        return re.search(r"\b" + re.escape(symbol) + r"\b", text) is not None
    if symbol.startswith("struct "):
        name = symbol.split(maxsplit=1)[1]
        return re.search(r"\bstruct\s+" + re.escape(name) + r"\b", text) is not None
    return re.search(
        r"\b(?:pub(?:\([^)]*\))?\s+)?(?:fn|enum|struct|type|trait|const|static)\s+"
        + re.escape(symbol)
        + r"\b",
        text,
    ) is not None


def task_missing_symbols(task: dict[str, Any]) -> list[str]:
    texts = "\n".join(read_text(REPO / rel) for rel in task.get("write", []))
    return [symbol for symbol in task.get("symbols", []) if not symbol_found(texts, symbol)]


def parse_source_range(item: str) -> tuple[str, int, int] | None:
    match = RANGE_RE.match(item)
    if not match:
        return None
    return match.group("path"), int(match.group("start")), int(match.group("end"))


def _brace_text(line: str) -> str:
    line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
    line = re.sub(r"'(?:\\.|[^'\\])*'", "''", line)
    return line.split("//", 1)[0]


def find_c_definition_range(task: dict[str, Any], symbol: str) -> str | None:
    name = symbol.split(maxsplit=1)[1] if symbol.startswith("struct ") else symbol
    call_pattern = re.compile(r"\b" + re.escape(name) + r"\s*\(")

    for item in task.get("read", []):
        parsed = parse_source_range(item)
        if parsed is None:
            continue
        rel, range_start, range_end = parsed
        lines = read_text(REPO / rel).splitlines()
        if not lines:
            continue
        lo = max(0, range_start - 1)
        hi = min(len(lines), range_end)
        for index in range(lo, hi):
            if not call_pattern.search(lines[index]):
                continue

            signature_end = min(hi, index + 12)
            joined = "\n".join(lines[index:signature_end])
            brace_pos = joined.find("{")
            semicolon_pos = joined.find(";")
            if brace_pos < 0 or (semicolon_pos >= 0 and semicolon_pos < brace_pos):
                continue

            signature_start = index
            for previous in range(index - 1, max(lo - 1, index - 4), -1):
                stripped = lines[previous].strip()
                if not stripped or stripped.endswith((";", "{", "}")):
                    break
                signature_start = previous

            depth = 0
            opened = False
            block_comment = False
            for end_index in range(index, hi):
                text = _brace_text(lines[end_index])
                if block_comment:
                    if "*/" not in text:
                        continue
                    text = text.split("*/", 1)[1]
                    block_comment = False
                while "/*" in text:
                    before, after = text.split("/*", 1)
                    if "*/" in after:
                        text = before + after.split("*/", 1)[1]
                    else:
                        text = before
                        block_comment = True
                        break
                opens = text.count("{")
                closes = text.count("}")
                if opens:
                    opened = True
                if opened:
                    depth += opens - closes
                    if depth == 0:
                        return f"{rel}:{signature_start + 1}-{end_index + 1}"
    return None


def chunk_source_ranges(items: list[str], max_lines: int = HEAL_FOCUS_MAX_LINES) -> list[str]:
    chunks: list[str] = []
    for item in items:
        parsed = parse_source_range(item)
        if parsed is None:
            chunks.append(item)
            continue
        rel, start, end = parsed
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + max_lines - 1)
            chunks.append(f"{rel}:{cursor}-{chunk_end}")
            cursor = chunk_end + 1
    return chunks


def build_healing_units(task: dict[str, Any]) -> list[dict[str, Any]]:
    missing = task_missing_symbols(task)
    requested = missing or list(task.get("symbols", []))
    units: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for symbol in requested:
        source_range = find_c_definition_range(task, symbol)
        if source_range is None:
            unresolved.append(symbol)
            continue
        units.append({
            "title": f"Implement only {symbol}",
            "read": [source_range],
            "symbols": [symbol],
            "max_read_lines": max(1, read_range_line_count(source_range)),
        })

    fallback_ranges = chunk_source_ranges(list(task.get("read", [])))
    for index, symbol in enumerate(unresolved):
        selected = fallback_ranges[min(index, len(fallback_ranges) - 1)] if fallback_ranges else None
        units.append({
            "title": f"Implement only {symbol}",
            "read": [selected] if selected else [],
            "symbols": [symbol],
            "max_read_lines": read_range_line_count(selected) if selected else HEAL_FOCUS_MAX_LINES,
        })

    if not units:
        for index, source_range in enumerate(fallback_ranges, 1):
            units.append({
                "title": f"Complete focused source slice {index}",
                "read": [source_range],
                "symbols": [],
                "max_read_lines": max(1, read_range_line_count(source_range)),
            })

    def source_order(unit: dict[str, Any]) -> tuple[str, int]:
        parsed = parse_source_range(unit.get("read", [""])[0]) if unit.get("read") else None
        return (parsed[0], parsed[1]) if parsed else ("", 0)

    units.sort(key=source_order)
    for index, unit in enumerate(units, 1):
        symbol_key = "-".join(unit.get("symbols", [])) or f"slice-{index}"
        safe_key = re.sub(r"[^A-Za-z0-9_.-]", "-", symbol_key).strip("-")
        unit["id"] = f"{task['id']}.F-{safe_key or index}"
    return units


def progress_path(task_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)
    return PROGRESS / f"{safe}.json"


def file_hash(rel: str) -> str | None:
    path = REPO / rel
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def target_hashes(task: dict[str, Any]) -> dict[str, str | None]:
    return {rel: file_hash(rel) for rel in task.get("write", [])}


def load_progress(task_id: str) -> dict[str, Any] | None:
    path = progress_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return None


def save_progress(task_id: str, data: dict[str, Any]) -> None:
    ensure_dirs()
    progress_path(task_id).write_text(json.dumps(data, indent=2), encoding="utf-8")


def progress_status(task: dict[str, Any]) -> dict[str, Any]:
    data = load_progress(task["id"])
    current = target_hashes(task)
    if data is None:
        return {
            "started": False,
            "target_changed": False,
            "failures": 0,
            "last_check_code": None,
            "source_reread_blocked": False,
            "healing_active": False,
            "healing_generation": 0,
        }
    baseline = data.get("target_hashes_at_start", {})
    changed = any(current.get(rel) != baseline.get(rel) for rel in current)
    failures = int(data.get("failures", 0))
    healing = data.get("healing", {})
    return {
        "started": True,
        "started_at": data.get("started_at"),
        "target_changed": changed,
        "failures": failures,
        "last_check_code": data.get("last_check_code"),
        "last_check_at": data.get("last_check_at"),
        "source_reread_blocked": not changed and not bool(data.get("read_authorized")),
        "healing_active": bool(healing.get("active")),
        "healing_generation": int(healing.get("generation", 0)),
    }


def healing_unit_done(task: dict[str, Any], unit: dict[str, Any], healing: dict[str, Any]) -> bool:
    return unit.get("id") in healing.get("completed_unit_ids", [])


def healing_unit_objective_present(task: dict[str, Any], unit: dict[str, Any]) -> bool:
    symbols = unit.get("symbols", [])
    if not symbols:
        return False
    focused = {"write": task.get("write", []), "symbols": symbols}
    return not task_missing_symbols(focused)


def active_healing_unit(task: dict[str, Any], healing: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    units = healing.get("units", [])
    preferred = int(healing.get("active_index", 0))
    order = list(range(preferred, len(units))) + list(range(0, preferred))
    for index in order:
        if not healing_unit_done(task, units[index], healing):
            return index, units[index]
    return None


def effective_task(task: dict[str, Any]) -> dict[str, Any]:
    progress = load_progress(task["id"])
    healing = progress.get("healing", {}) if progress else {}
    if not healing.get("active"):
        return dict(task)
    active = active_healing_unit(task, healing)
    if active is None:
        return dict(task)
    index, unit = active
    focused = dict(task)
    focused["parent_id"] = task["id"]
    focused["focus_id"] = unit["id"]
    focused["focus_index"] = index
    focused["focus_total"] = len(healing.get("units", []))
    focused["title"] = unit["title"]
    focused["read"] = list(unit.get("read", []))
    focused["symbols"] = list(unit.get("symbols", []))
    focused["max_read_lines"] = int(unit.get("max_read_lines", HEAL_FOCUS_MAX_LINES))
    focused["healing_strategy"] = healing.get("strategy", "symbol-focus")
    focused["healing_diagnosis"] = healing.get("diagnosis", "manual-recovery")
    focused["repair_log"] = healing.get("repair_log")
    if focused["healing_strategy"] == "repair-first-error":
        focused["title"] = f"Repair the first compiler error for {unit['id']}"
        focused["read"] = []
        focused["max_read_lines"] = 0
    return focused


def diagnose_task(task: dict[str, Any]) -> str:
    progress = load_progress(task["id"])
    if not task_budget_ok(task):
        return "read-budget-too-large"
    if progress is None:
        return "untracked-stall"
    if int(progress.get("failures", 0)) >= 2:
        return "repeated-check-failure"
    status = progress_status(task)
    if status["started"] and not status["target_changed"]:
        return "no-target-change"
    if progress.get("last_check_code") == 0 and not task_done(task):
        return "check-passed-objective-incomplete"
    return "manual-recovery"


def write_healing_action(task: dict[str, Any], progress: dict[str, Any]) -> None:
    healing = progress["healing"]
    active = active_healing_unit(task, healing)
    active_id = active[1]["id"] if active else "parent-task"
    lines = [
        "# Active Self-Heal Action",
        "",
        f"Generated: {healing['created_at']}",
        f"Parent task: {task['id']}",
        f"Diagnosis: {healing['diagnosis']}",
        f"Strategy: {healing['strategy']}",
        f"Generation: {healing['generation']}",
        f"Active focus: {active_id}",
        "Restarted migration: NO",
        "Preserved Rust targets: YES",
        "Preserved completed tasks: YES",
        "Contains source understanding: NO",
        "",
        "Continue immediately with `work/state/current_task.md`.",
        "Do not return to project discovery or restart the task queue.",
        "",
    ]
    (STATE / "healing_action.md").write_text("\n".join(lines), encoding="utf-8")


def apply_healing(
    task: dict[str, Any],
    diagnosis: str,
    reason: str = "",
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    progress = load_progress(task["id"]) or {
        "task_id": task["id"],
        "started_at": now(),
        "failures": 0,
        "contains_source_understanding": False,
    }
    previous = progress.get("healing", {})
    generation = int(previous.get("generation", 0)) + 1
    clean_reason = " ".join(reason.split())[:240]
    strategy = "repair-first-error" if diagnosis == "repeated-check-failure" else "symbol-focus"
    repair_log = progress.get("last_check_log") if strategy == "repair-first-error" else None
    units = build_healing_units(task)
    if strategy == "repair-first-error":
        units.insert(0, {
            "id": f"{task['id']}.R{generation}",
            "title": "Repair only the first compiler error",
            "read": [],
            "symbols": [],
            "max_read_lines": 0,
        })
    healing = {
        "active": True,
        "generation": generation,
        "created_at": now(),
        "diagnosis": diagnosis,
        "reason": clean_reason,
        "strategy": strategy,
        "units": units,
        "active_index": 0,
        "completed_unit_ids": [
            unit["id"]
            for unit in units
            if unit.get("symbols")
            and (
                unit["id"] in previous.get("completed_unit_ids", [])
                or healing_unit_objective_present(task, unit)
            )
        ],
        "repair_log": repair_log,
        "contains_source_understanding": False,
    }
    result = {
        "task_id": task["id"],
        "diagnosis": diagnosis,
        "strategy": strategy,
        "generation": generation,
        "focus_units": units,
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    progress["healing"] = healing
    progress["target_hashes_at_start"] = target_hashes(task)
    progress["active_started_at"] = now()
    progress["read_authorized"] = strategy == "symbol-focus"
    active = active_healing_unit(task, healing)
    progress["read_ranges"] = list(active[1].get("read", [])) if active else []
    progress["no_progress_events"] = int(progress.get("no_progress_events", 0)) + (
        1 if diagnosis in {"no-target-change", "untracked-stall"} else 0
    )
    progress["contains_source_understanding"] = False
    save_progress(task["id"], progress)

    stale_refresh = STATE / "refresh_note.md"
    if stale_refresh.exists():
        stale_refresh.unlink()
    write_healing_action(task, progress)
    write_current_task(task)
    return result


def write_json(path: Path, data: Any) -> None:
    ensure_dirs()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def preflight_data() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("root INSTRUCTION.md", (REPO / "INSTRUCTION.md").exists())
    for rel in REQUIRED_C_MATERIAL:
        add(rel, (REPO / rel).exists())

    rustc = run(["rustc", "--version"])
    cargo = run(["cargo", "--version"])
    python = run(["python3", "--version"])
    add("python3", python["code"] == 0, python["output"].strip())
    add("rustc", rustc["code"] == 0, rustc["output"].strip())
    add("cargo", cargo["code"] == 0, cargo["output"].strip())

    return {
        "generated_at": now(),
        "repo": str(REPO),
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }


def cmd_preflight(_args: argparse.Namespace) -> int:
    ensure_dirs()
    data = preflight_data()
    write_json(STATE / "preflight.json", data)
    print_report_table("Preflight", data["checks"])
    return 0 if data["ok"] else 1


def cmd_init(_args: argparse.Namespace) -> int:
    ensure_dirs()
    (RUST / "src").mkdir(parents=True, exist_ok=True)
    (RUST / "tests").mkdir(parents=True, exist_ok=True)

    cargo_toml = RUST / "Cargo.toml"
    if not cargo_toml.exists():
        cargo_toml.write_text(
            """[package]
name = "flashdb"
version = "0.1.0"
edition = "2021"

[dependencies]
crc32fast = "1.4"
bytemuck = "1.16"

[dev-dependencies]
tempfile = "3.10"
""",
            encoding="utf-8",
        )

    lib_rs = RUST / "src" / "lib.rs"
    if not lib_rs.exists():
        lib_rs.write_text(
            """pub mod blob;
pub mod db;
pub mod def;
pub mod error;
pub mod file_backend;
pub mod kvdb;
pub mod low_lvl;
pub mod tsdb;
pub mod utils;
""",
            encoding="utf-8",
        )

    print(f"Initialized skeleton under {RUST.relative_to(REPO)}")
    return 0


def status_data() -> dict[str, Any]:
    src = RUST / "src"
    tests = RUST / "tests"
    modules = {name: (src / name).exists() for name in EXPECTED_MODULES}
    module_quality = {name: module_quality_data(src / name) for name in EXPECTED_MODULES}
    test_files = {name: (tests / name).exists() for name in EXPECTED_TEST_FILES}
    test_quality = test_quality_data()
    cargo_exists = (RUST / "Cargo.toml").exists()
    coverage = coverage_data()
    unsafe = unsafe_stats()

    return {
        "generated_at": now(),
        "cargo_toml": cargo_exists,
        "modules": modules,
        "module_quality": module_quality,
        "test_files": test_files,
        "test_quality": test_quality,
        "coverage": coverage,
        "unsafe": unsafe,
    }


def cmd_status(_args: argparse.Namespace) -> int:
    ensure_dirs()
    data = status_data()
    write_json(STATE / "status.json", data)
    checks = [{"name": "Cargo.toml", "ok": data["cargo_toml"], "detail": "flashDB_rust/Cargo.toml"}]
    checks.extend(
        {
            "name": f"module {k}",
            "ok": data["module_quality"][k]["ok"],
            "detail": data["module_quality"][k]["detail"],
        }
        for k in data["modules"]
    )
    checks.extend({"name": f"test file {k}", "ok": v, "detail": ""} for k, v in data["test_files"].items())
    checks.append({
        "name": "test bodies look non-trivial",
        "ok": data["test_quality"]["ok"],
        "detail": data["test_quality"]["detail"],
    })
    checks.append({
        "name": "mapped test cases",
        "ok": data["coverage"]["found"] == data["coverage"]["total"],
        "detail": f"{data['coverage']['found']}/{data['coverage']['total']}",
    })
    checks.append({
        "name": "unsafe ratio",
        "ok": data["unsafe"]["ratio"] < 10.0,
        "detail": f"{data['unsafe']['ratio']:.2f}%",
    })
    print_report_table("Status", checks)
    write_next_actions(status_to_actions(data))
    write_current_task(first_open_task())
    return 0


def task_done(task: dict[str, Any]) -> bool:
    for rel in task.get("done", []):
        path = REPO / rel
        if not path.exists() or path.stat().st_size == 0:
            return False
    for rel in task.get("write", []):
        path = REPO / rel
        if not path.exists() or path.stat().st_size == 0:
            return False
    return not task_missing_symbols(task)


def plan_data() -> dict[str, Any]:
    tasks = []
    for task in MICRO_TASKS:
        item = dict(task)
        item["is_done"] = task_done(task)
        item["read_line_count"] = task_read_line_count(task)
        item["budget_ok"] = task_budget_ok(task)
        tasks.append(item)
    current = next((task for task in tasks if not task["is_done"]), tasks[-1])
    return {"generated_at": now(), "tasks": tasks, "current": current}


def first_open_task() -> dict[str, Any]:
    return plan_data()["current"]


def cmd_plan(_args: argparse.Namespace) -> int:
    ensure_dirs()
    data = plan_data()
    write_json(STATE / "plan.json", data)
    write_todo(data)
    write_current_task(data["current"])
    print(f"Plan written to {STATE.relative_to(REPO)}/todo.md")
    print(f"Current task: {data['current']['id']} - {data['current']['title']}")
    unsafe_tasks = [task for task in data["tasks"] if not task["budget_ok"]]
    if unsafe_tasks:
        print("Read-budget violation in guidance artifacts:", file=sys.stderr)
        for task in unsafe_tasks:
            print(
                f"- {task['id']}: {task['read_line_count']}/{task_read_budget(task)} lines",
                file=sys.stderr,
            )
        return 1
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    ensure_dirs()
    data = plan_data()
    task = data["current"]
    if args.id:
        matches = [item for item in data["tasks"] if item["id"] == args.id]
        if not matches:
            print(f"Unknown task id: {args.id}", file=sys.stderr)
            return 2
        task = matches[0]
    write_current_task(task)
    print(render_task(task))
    return 0 if task_budget_ok(task) else 1


def cmd_start_task(args: argparse.Namespace) -> int:
    ensure_dirs()
    data = plan_data()
    task_id = args.id or data["current"]["id"]
    task = next((item for item in data["tasks"] if item["id"] == task_id), None)
    if task is None:
        print(f"Unknown task id: {task_id}", file=sys.stderr)
        return 2
    status = progress_status(task)
    if status["started"] and not status["target_changed"]:
        result = apply_healing(
            task,
            "no-target-change",
            "start-task was repeated before any target-file change",
        )
        print(f"SELF-HEAL APPLIED: {result['diagnosis']} -> {result['strategy']}")
        print("The migration was not restarted. Continue the current task below.\n")
        print(render_task(task))
        return 0
    if status["started"] and status["target_changed"]:
        print(f"Task {task_id} target files changed after start.")
        print(f"Run `python3 work/scripts/flashdb_pipeline.py check-task {task_id}` now.")
        return 0
    active = effective_task(task)
    progress = load_progress(task_id) or {}
    progress.update({
        "task_id": task_id,
        "started_at": progress.get("started_at") or now(),
        "active_started_at": now(),
        "read_ranges": list(active.get("read", [])),
        "write_targets": list(task.get("write", [])),
        "target_hashes_at_start": target_hashes(task),
        "failures": int(progress.get("failures", 0)),
        "last_check_code": progress.get("last_check_code"),
        "last_check_at": progress.get("last_check_at"),
        "read_authorized": True,
        "contains_source_understanding": False,
    })
    save_progress(task_id, progress)
    print(f"Started task: {task_id}")
    if active.get("focus_id"):
        print(f"Active self-heal focus: {active['focus_id']}")
    print("Recorded only execution state and target file hashes; no source understanding was stored.")
    return 0


def cmd_check_task(args: argparse.Namespace) -> int:
    ensure_dirs()
    data = plan_data()
    task = next((item for item in data["tasks"] if item["id"] == args.id), None)
    if task is None:
        print(f"Unknown task id: {args.id}", file=sys.stderr)
        return 2
    command = task.get("check")
    if not command:
        print(f"Task {args.id} has no check command.", file=sys.stderr)
        return 2
    active = effective_task(task)
    progress = load_progress(args.id) or {
        "task_id": args.id,
        "started_at": now(),
        "read_ranges": list(active.get("read", [])),
        "write_targets": list(task.get("write", [])),
        "target_hashes_at_start": target_hashes(task),
        "failures": 0,
        "contains_source_understanding": False,
    }
    before = progress_status(task)
    if before["started"] and not before["target_changed"]:
        result = apply_healing(
            task,
            "no-target-change",
            "check-task was requested before any target-file change",
        )
        print(f"SELF-HEAL APPLIED: {result['diagnosis']} -> {result['strategy']}")
        print("Skipped the redundant cargo check. Continue the current task below.\n")
        print(render_task(task))
        return 0
    result = run(shlex.split(command), timeout=600)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", args.id)
    write_log(f"task_{safe_id}_check.log", result["output"])
    progress["last_check_code"] = result["code"]
    progress["last_check_at"] = now()
    progress["last_check_log"] = f"work/logs/task_{safe_id}_check.log"
    current_hashes = target_hashes(task)
    progress["target_hashes_after_last_check"] = current_hashes
    progress["read_authorized"] = False
    if result["code"] == 0:
        progress["failures"] = 0
        progress["last_error_fingerprint"] = None
    else:
        fingerprint = error_fingerprint(result["output"])
        previous = progress.get("last_error_fingerprint")
        progress["failures"] = int(progress.get("failures", 0)) + 1 if previous == fingerprint else 1
        progress["last_error_fingerprint"] = fingerprint
    save_progress(args.id, progress)
    print(result["output"], end="")
    print(f"\nTask check exit code: {result['code']}")
    print(f"Recorded failure count: {progress['failures']}")
    if result["code"] != 0:
        if progress["failures"] >= 2:
            healed = apply_healing(
                task,
                "repeated-check-failure",
                "the same check failure occurred twice",
            )
            print(f"SELF-HEAL APPLIED: {healed['diagnosis']} -> {healed['strategy']}")
            print("Use only the first error block, repair the current target, and continue.")
            return 0
        print("Fix only the first error block, then run check-task again.")
        return int(result["code"])

    progress = load_progress(args.id) or progress
    healing = progress.get("healing", {})
    if healing.get("active"):
        if healing.get("strategy") == "repair-first-error":
            healing["strategy"] = "symbol-focus"
            healing["repair_log"] = None
            progress["healing"] = healing
        active_info = active_healing_unit(task, healing)
        if active_info is not None:
            index, unit = active_info
            focus_complete = healing_unit_objective_present(task, unit) if unit.get("symbols") else before["target_changed"]
            if focus_complete:
                completed = set(healing.get("completed_unit_ids", []))
                completed.add(unit["id"])
                healing["completed_unit_ids"] = sorted(completed)
                healing["active_index"] = index + 1
                healing["strategy"] = "symbol-focus"
                healing["repair_log"] = None
                next_active = active_healing_unit(task, healing)
                if next_active is None:
                    healing["active"] = False
                    progress["read_authorized"] = False
                    progress["read_ranges"] = []
                else:
                    healing["active_index"] = next_active[0]
                    progress["read_authorized"] = True
                    progress["read_ranges"] = list(next_active[1].get("read", []))
                progress["healing"] = healing
                progress["target_hashes_at_start"] = current_hashes
                save_progress(args.id, progress)
                write_healing_action(task, progress)
                write_current_task(task)
                if healing["active"]:
                    print(f"Focused unit complete. Continue with {next_active[1]['id']} in work/state/current_task.md.")
                else:
                    print("All self-heal focus units are complete.")
            else:
                incomplete = int(progress.get("incomplete_checks", 0)) + 1
                progress["incomplete_checks"] = incomplete
                progress["target_hashes_at_start"] = current_hashes
                save_progress(args.id, progress)
                if incomplete >= 2:
                    healed = apply_healing(
                        task,
                        "check-passed-objective-incomplete",
                        "cargo check passed twice without completing the focused symbol",
                    )
                    print(f"SELF-HEAL APPLIED: {healed['diagnosis']} -> {healed['strategy']}")
                else:
                    print("The code compiles, but the focused completion symbol is still missing. Continue editing it.")

    if task_done(task):
        print(f"Task objective is present. Run `python3 work/scripts/flashdb_pipeline.py complete-task {args.id}`.")
    return 0


def cmd_complete_task(args: argparse.Namespace) -> int:
    ensure_dirs()
    data = plan_data()
    task = next((item for item in data["tasks"] if item["id"] == args.id), None)
    if task is None:
        print(f"Unknown task id: {args.id}", file=sys.stderr)
        return 2
    progress = load_progress(args.id)
    current_hashes = target_hashes(task)
    if (
        progress is None
        or progress.get("last_check_code") != 0
        or progress.get("target_hashes_after_last_check") != current_hashes
    ):
        print(f"Task {args.id} has not passed check-task for the current target content.", file=sys.stderr)
        print(f"Run `python3 work/scripts/flashdb_pipeline.py check-task {args.id}` first.", file=sys.stderr)
        return 1
    if not task_done(task):
        print(f"Task {args.id} is not complete according to file/symbol checks.", file=sys.stderr)
        print(render_task(task))
        return 1
    completed_path = STATE / "completed_tasks.txt"
    existing = read_text(completed_path).splitlines()
    if args.id not in existing:
        existing.append(args.id)
    completed_path.write_text("\n".join(existing) + "\n", encoding="utf-8")
    progress = load_progress(args.id) or {"task_id": args.id}
    progress["completed_at"] = now()
    save_progress(args.id, progress)
    data = plan_data()
    write_todo(data)
    write_current_task(data["current"])
    print(f"Marked complete: {args.id}")
    print(f"Next task: {data['current']['id']} - {data['current']['title']}")
    return 0


def render_task(task: dict[str, Any]) -> str:
    parent = task
    active = effective_task(parent)
    estimated_lines = task_read_line_count(active)
    max_lines = task_read_budget(active)
    budget_status = "OK" if estimated_lines <= max_lines else "VIOLATION"
    status = progress_status(parent)
    if active.get("healing_strategy") == "repair-first-error":
        next_action = "REPAIR_FIRST_ERROR"
    elif status["started"] and status["target_changed"]:
        next_action = "RUN_CHECK_TASK"
    elif status["started"] and not status["source_reread_blocked"]:
        next_action = "READ_FOCUS_THEN_EDIT"
    elif status["started"]:
        next_action = "RUN_HEAL"
    else:
        next_action = "START_TASK"
    lines = [
        f"# Current Micro Task: {parent['id']}",
        "",
        f"Title: {active['title']}",
        f"Parent done: {task_done(parent)}",
        f"Estimated read lines: {estimated_lines}",
        f"Max read lines before writing/checking: {max_lines}",
        f"Read budget: {budget_status}",
        f"Task started: {'YES' if status['started'] else 'NO'}",
        f"Target changed since start: {'YES' if status['target_changed'] else 'NO'}",
        f"Recorded failures: {status['failures']}",
        f"Source reread blocked: {'YES' if status['source_reread_blocked'] else 'NO'}",
        f"Self-heal active: {'YES' if status['healing_active'] else 'NO'}",
        f"Self-heal generation: {status['healing_generation']}",
        f"Next required action: {next_action}",
        "",
    ]
    if active.get("focus_id"):
        lines.extend([
            f"Active focus: {active['focus_id']} ({active['focus_index'] + 1}/{active['focus_total']})",
            f"Healing diagnosis: {active['healing_diagnosis']}",
            "",
        ])
    if active.get("healing_strategy") == "repair-first-error":
        lines.append("Read only the first compiler error block from:")
        lines.append(f"- {active.get('repair_log') or 'work/logs/task check log'}")
        lines.append("- Do not reread C source while repairing a compiler error.")
    else:
        lines.append("Read only these focused ranges:")
        for item in active.get("read", []):
            lines.append(f"- {item}")
    lines.extend(["", "Write/edit only these targets:"])
    for item in parent.get("write", []):
        lines.append(f"- {item}")
    if active.get("symbols"):
        lines.extend(["", "Focused completion symbols:"])
        for item in active["symbols"]:
            lines.append(f"- {item}")
    lines.extend([
        "",
        "Check command:",
        f"`{parent.get('check', 'python3 work/scripts/flashdb_pipeline.py status')}`",
        "",
        "Preferred check wrapper:",
        f"`python3 work/scripts/flashdb_pipeline.py check-task {parent['id']}`",
        "",
        "Protocol:",
        f"1. Obey `Next required action`; use `start-task {parent['id']}` only when it says START_TASK.",
        "2. When a focused read is authorized, read it once and edit before any other discovery.",
        "3. Do not write source-understanding notes; only execution state may persist.",
        "4. Run the preferred check wrapper immediately after the edit.",
        f"5. If progress is impossible or context was compacted before an edit, run `python3 work/scripts/flashdb_pipeline.py heal {parent['id']}`.",
        "6. Self-heal changes the active focus and continues this parent task; it never restarts migration.",
    ])
    return "\n".join(lines) + "\n"


def write_todo(data: dict[str, Any]) -> None:
    ensure_dirs()
    lines = ["# FlashDB Rewrite Micro-Task Queue", "", f"Generated: {data['generated_at']}", ""]
    for task in data["tasks"]:
        mark = "x" if task["is_done"] else " "
        budget = "OK" if task["budget_ok"] else "OVER"
        lines.append(
            f"- [{mark}] `{task['id']}` {task['title']} "
            f"({task['read_line_count']}/{task_read_budget(task)} read lines, {budget})"
        )
    lines.extend(["", f"Current: `{data['current']['id']}`", ""])
    (STATE / "todo.md").write_text("\n".join(lines), encoding="utf-8")


def write_current_task(task: dict[str, Any]) -> None:
    ensure_dirs()
    (STATE / "current_task.md").write_text(render_task(task), encoding="utf-8")


def module_quality_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "detail": "missing", "missing_symbols": [], "line_count": 0}
    text = read_text(path)
    line_count = len(text.splitlines())
    markers = [marker for marker in STUB_MARKERS if marker in text]
    required = REQUIRED_SYMBOLS.get(path.name, [])
    missing_symbols = [
        symbol
        for symbol in required
        if not re.search(r"\b(fn|pub\s+fn)\s+" + re.escape(symbol) + r"\b", text)
    ]
    too_small = path.name in REQUIRED_SYMBOLS and line_count < 50
    ok = not markers and not missing_symbols and not too_small
    detail_parts = [f"{line_count} lines"]
    if markers:
        detail_parts.append("markers=" + ",".join(markers[:4]))
    if missing_symbols:
        detail_parts.append("missing=" + ",".join(missing_symbols[:6]))
    if too_small:
        detail_parts.append("too small for required API surface")
    return {
        "ok": ok,
        "detail": "; ".join(detail_parts),
        "missing_symbols": missing_symbols,
        "line_count": line_count,
        "markers": markers,
    }


def test_quality_data() -> dict[str, Any]:
    text = test_file_text()
    test_count = len(re.findall(r"#\s*\[\s*test\s*\]", text))
    weak = []
    for match in re.finditer(r"#\s*\[\s*test\s*\]\s*fn\s+([A-Za-z0-9_]+)\s*\([^)]*\)\s*\{", text):
        name = match.group(1)
        body_start = match.end()
        body = text[body_start: body_start + 800]
        if not re.search(r"\b(assert|assert_eq|assert_ne|panic!|unwrap|expect|Kvdb|Tsdb|fdb_|Blob)\b", body):
            weak.append(name)
    ok = test_count >= len(EXPECTED_CASES) and not weak
    detail = f"{test_count} #[test] functions"
    if weak:
        detail += "; weak=" + ",".join(weak[:6])
    return {"ok": ok, "detail": detail, "test_count": test_count, "weak_tests": weak}


def test_file_text() -> str:
    if not (RUST / "tests").exists():
        return ""
    parts = []
    for path in sorted((RUST / "tests").glob("*.rs")):
        parts.append(read_text(path))
    return "\n".join(parts)


def coverage_data() -> dict[str, Any]:
    text = test_file_text()
    cases = []
    found = 0
    for case_id, symbols in EXPECTED_CASES:
        ok = any(re.search(r"\b" + re.escape(symbol) + r"\b", text) for symbol in symbols)
        if ok:
            found += 1
        cases.append({"id": case_id, "symbols": symbols, "ok": ok})
    return {"found": found, "total": len(EXPECTED_CASES), "cases": cases}


def unsafe_stats() -> dict[str, Any]:
    total = 0
    unsafe = 0
    if (RUST / "src").exists():
        for path in sorted((RUST / "src").glob("*.rs")):
            for line in read_text(path).splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("//"):
                    continue
                total += 1
                if "unsafe" in stripped:
                    unsafe += 1
    ratio = (unsafe * 100.0 / total) if total else 0.0
    return {"unsafe_lines": unsafe, "total_lines": total, "ratio": ratio}


def source_integrity() -> dict[str, Any]:
    if not (REPO / ".git").exists():
        return {"checked": False, "ok": False, "detail": ".git not found"}
    diff = run(["git", "diff", "--", "src/", "inc/", "tests/"], timeout=60)
    ok = diff["code"] == 0 and diff["output"].strip() == ""
    return {"checked": True, "ok": ok, "detail": diff["output"].strip()}


def run_cargo(kind: str) -> dict[str, Any]:
    if not (RUST / "Cargo.toml").exists():
        return {"code": 1, "output": "flashDB_rust/Cargo.toml missing", "tests_run": 0}
    cmd = ["cargo", kind]
    if kind == "test":
        cmd = ["cargo", "test", "--no-fail-fast"]
    result = run(cmd, cwd=RUST, timeout=600)
    write_log(f"cargo_{kind}.log", result["output"])
    if kind == "test":
        result["tests_run"] = parse_tests_run(result["output"])
    return result


def parse_tests_run(output: str) -> int:
    total = 0
    for match in re.finditer(r"test result: \w+\.\s+(\d+) passed;\s+(\d+) failed", output):
        total += int(match.group(1)) + int(match.group(2))
    return total


def verify_data(strict: bool) -> dict[str, Any]:
    ensure_dirs()
    preflight = preflight_data()
    status = status_data()
    build = run_cargo("build")
    test = run_cargo("test")
    integrity = source_integrity()
    coverage = coverage_data()
    unsafe = unsafe_stats()

    checks = [
        {"name": "root INSTRUCTION.md exists", "ok": (REPO / "INSTRUCTION.md").exists()},
        {"name": "Cargo.toml exists", "ok": (RUST / "Cargo.toml").exists()},
        {
            "name": "src modules complete",
            "ok": all(item["ok"] for item in status["module_quality"].values()),
            "detail": module_quality_summary(status["module_quality"]),
        },
        {"name": "test files present", "ok": all(status["test_files"].values())},
        {
            "name": "test bodies non-trivial",
            "ok": status["test_quality"]["ok"],
            "detail": status["test_quality"]["detail"],
        },
        {"name": "cargo build", "ok": build["code"] == 0, "detail": f"exit {build['code']}"},
        {
            "name": "cargo test",
            "ok": test["code"] == 0 and (not strict or test.get("tests_run", 0) >= 24),
            "detail": f"exit {test['code']}, tests_run={test.get('tests_run', 0)}",
        },
        {
            "name": "mapped C test coverage",
            "ok": coverage["found"] == coverage["total"],
            "detail": f"{coverage['found']}/{coverage['total']}",
        },
        {
            "name": "unsafe ratio < 10%",
            "ok": unsafe["ratio"] < 10.0,
            "detail": f"{unsafe['ratio']:.2f}%",
        },
        {"name": "original source unmodified", "ok": integrity["ok"], "detail": integrity["detail"][:200]},
    ]

    data = {
        "generated_at": now(),
        "strict": strict,
        "preflight": preflight,
        "status": status,
        "build": build,
        "test": test,
        "coverage": coverage,
        "unsafe": unsafe,
        "integrity": integrity,
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }
    return data


def cmd_verify(args: argparse.Namespace) -> int:
    data = verify_data(strict=args.strict)
    write_json(STATE / "verify.json", simplify_for_json(data))
    write_next_actions(verify_to_actions(data))
    generate_reports(data)
    print_report_table("Verification", data["checks"])
    print(f"Reports: {RESULT.relative_to(REPO)}/output.md, {ISSUES.relative_to(REPO)}/00-summary.md")
    return 0 if data["ok"] else 1


def simplify_for_json(data: Any) -> Any:
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key == "output" and isinstance(value, str) and len(value) > 4000:
                result[key] = value[-4000:]
            else:
                result[key] = simplify_for_json(value)
        return result
    if isinstance(data, list):
        return [simplify_for_json(item) for item in data]
    return data


def status_to_actions(data: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if not data["cargo_toml"]:
        actions.append("Run `python3 work/scripts/flashdb_pipeline.py init` or create flashDB_rust/Cargo.toml.")
    missing_modules = [name for name, ok in data["modules"].items() if not ok]
    if missing_modules:
        actions.append("Implement missing modules: " + ", ".join(missing_modules) + ".")
    incomplete_modules = [
        name
        for name, item in data["module_quality"].items()
        if data["modules"].get(name) and not item["ok"]
    ]
    if incomplete_modules:
        detail = "; ".join(
            f"{name}: {data['module_quality'][name]['detail']}" for name in incomplete_modules
        )
        actions.append("Complete incomplete/stub modules: " + detail + ".")
    missing_tests = [name for name, ok in data["test_files"].items() if not ok]
    if missing_tests:
        actions.append("Create Rust integration test files: " + ", ".join(missing_tests) + ".")
    if not data["test_quality"]["ok"]:
        actions.append("Add at least 24 non-trivial #[test] functions with assertions/API exercise.")
    missing_cases = [case["id"] for case in data["coverage"]["cases"] if not case["ok"]]
    if missing_cases:
        actions.append("Add equivalent Rust test coverage for: " + ", ".join(missing_cases) + ".")
    if data["unsafe"]["ratio"] >= 10.0:
        actions.append("Reduce unsafe usage below 10%.")
    if not actions:
        actions.append("Run `python3 work/scripts/flashdb_pipeline.py verify --strict`.")
    return actions


def verify_to_actions(data: dict[str, Any]) -> list[str]:
    actions = []
    for check in data["checks"]:
        if check["ok"]:
            continue
        name = check["name"]
        if name == "cargo build":
            actions.append("Fix the first cargo build error in work/logs/cargo_build.log, then rerun verify.")
        elif name == "cargo test":
            actions.append("Fix failing or missing Rust tests from work/logs/cargo_test.log.")
        elif name == "mapped C test coverage":
            missing = [case["id"] for case in data["coverage"]["cases"] if not case["ok"]]
            actions.append("Add missing test mappings: " + ", ".join(missing) + ".")
        elif name == "original source unmodified":
            actions.append("Revert unintended changes under src/, inc/, or tests/ without touching Rust output.")
        elif name == "src modules complete":
            incomplete = [
                f"{module}: {quality['detail']}"
                for module, quality in data["status"]["module_quality"].items()
                if not quality["ok"]
            ]
            actions.append("Complete required Rust modules: " + "; ".join(incomplete) + ".")
        elif name == "test files present":
            missing = [m for m, ok in data["status"]["test_files"].items() if not ok]
            actions.append("Create required Rust test files: " + ", ".join(missing) + ".")
        elif name == "test bodies non-trivial":
            actions.append("Replace empty/name-only tests with real Rust tests that assert FlashDB behavior.")
        else:
            actions.append(f"Repair failed check: {name}.")
    if not actions:
        actions.append("All checks passed. The rewrite is complete.")
    return actions


def module_quality_summary(module_quality: dict[str, Any]) -> str:
    bad = [f"{name}: {item['detail']}" for name, item in module_quality.items() if not item["ok"]]
    if not bad:
        return "all required module checks passed"
    return "; ".join(bad[:4])


def write_next_actions(actions: list[str]) -> None:
    ensure_dirs()
    lines = ["# Next Actions", "", f"Generated: {now()}", ""]
    for index, action in enumerate(actions, 1):
        lines.append(f"{index}. {action}")
    lines.append("")
    (STATE / "next_actions.md").write_text("\n".join(lines), encoding="utf-8")


def generate_reports(data: dict[str, Any]) -> None:
    ensure_dirs()
    checks_md = "\n".join(
        f"| {item['name']} | {'PASS' if item['ok'] else 'FAIL'} | {item.get('detail', '')} |"
        for item in data["checks"]
    )
    missing_cases = [case for case in data["coverage"]["cases"] if not case["ok"]]
    missing_cases_md = "\n".join(f"- {case['id']} ({', '.join(case['symbols'])})" for case in missing_cases)
    if not missing_cases_md:
        missing_cases_md = "- none"

    output_md = f"""# FlashDB C-to-Rust Rewrite Report

Generated: {data['generated_at']}

## Final Rust Project

`flashDB_rust/`

## Verification Summary

| Check | Status | Detail |
|---|---|---|
{checks_md}

## Build

- Command: `cargo build`
- Exit code: {data['build']['code']}
- Log: `work/logs/cargo_build.log`

## Test

- Command: `cargo test --no-fail-fast`
- Exit code: {data['test']['code']}
- Tests observed: {data['test'].get('tests_run', 0)}
- Log: `work/logs/cargo_test.log`

## Test Migration

- Mapped cases found: {data['coverage']['found']} / {data['coverage']['total']}

Missing cases:

{missing_cases_md}

## Unsafe Ratio

- Unsafe lines: {data['unsafe']['unsafe_lines']}
- Total source lines: {data['unsafe']['total_lines']}
- Ratio: {data['unsafe']['ratio']:.2f}%

## Original Source Integrity

- Checked: {data['integrity']['checked']}
- OK: {data['integrity']['ok']}

```text
{data['integrity']['detail'] or '(no changes)'}
```

## Completion

Strict completion status: {'PASS' if data['ok'] else 'FAIL'}
"""

    issue_actions = verify_to_actions(data)
    issues_md = f"""# Issues Summary

Generated: {data['generated_at']}

## Current Status

{'No unresolved issues. Strict verification passed.' if data['ok'] else 'Strict verification has not passed.'}

## Required Repair Actions

{chr(10).join(f'{i}. {action}' for i, action in enumerate(issue_actions, 1))}

## Missing Test Mappings

{missing_cases_md}

## Cargo Build Log

See `work/logs/cargo_build.log`.

## Cargo Test Log

See `work/logs/cargo_test.log`.
"""

    (RESULT / "output.md").write_text(output_md, encoding="utf-8")
    (ISSUES / "00-summary.md").write_text(issues_md, encoding="utf-8")


def cmd_report(_args: argparse.Namespace) -> int:
    verify_path = STATE / "verify.json"
    if verify_path.exists():
        data = json.loads(read_text(verify_path))
    else:
        data = verify_data(strict=False)
    generate_reports(data)
    print(f"Wrote {RESULT / 'output.md'}")
    print(f"Wrote {ISSUES / '00-summary.md'}")
    return 0


def cmd_heal(args: argparse.Namespace) -> int:
    ensure_dirs()
    data = plan_data()
    task_id = args.id or data["current"]["id"]
    task = next((item for item in data["tasks"] if item["id"] == task_id), None)
    if task is None:
        print(f"Unknown task id: {task_id}", file=sys.stderr)
        return 2
    if task_done(task):
        print(f"Task {task_id} is already complete; no healing was applied.")
        return 0
    diagnosis = diagnose_task(task)
    result = apply_healing(task, diagnosis, args.reason or "automatic diagnosis", dry_run=args.dry_run)
    prefix = "SELF-HEAL DRY RUN" if args.dry_run else "SELF-HEAL APPLIED"
    print(f"{prefix}: {result['diagnosis']} -> {result['strategy']}")
    print(f"Parent task: {task_id}")
    print(f"Focused units: {len(result['focus_units'])}")
    for unit in result["focus_units"]:
        ranges = ", ".join(unit.get("read", [])) or "compiler error only"
        symbols = ", ".join(unit.get("symbols", [])) or "target slice"
        print(f"- {unit['id']}: {ranges} -> {symbols}")
    if args.dry_run:
        print("No files were changed.")
    else:
        print("Migration state and Rust sources were preserved.")
        print("Continue immediately with work/state/current_task.md; do not restart the queue.\n")
        print(render_task(task))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    print("`refresh` is a compatibility alias for in-place `heal`; no new attempt is started.")
    forwarded = argparse.Namespace(id=None, reason=args.reason, dry_run=False)
    return cmd_heal(forwarded)


def print_report_table(title: str, checks: list[dict[str, Any]]) -> None:
    print(f"== {title} ==")
    for item in checks:
        status = "PASS" if item["ok"] else "FAIL"
        detail = item.get("detail", "")
        suffix = f" - {detail}" if detail else ""
        print(f"[{status}] {item['name']}{suffix}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight")
    sub.add_parser("init")
    sub.add_parser("plan")
    sub.add_parser("status")

    task = sub.add_parser("task")
    task.add_argument("--id", help="show a specific task id instead of the current task")

    start = sub.add_parser("start-task")
    start.add_argument("id", nargs="?", help="task id to start; defaults to current task")

    check = sub.add_parser("check-task")
    check.add_argument("id", help="task id whose check command should run")

    heal = sub.add_parser("heal")
    heal.add_argument("id", nargs="?", help="task id to heal; defaults to current task")
    heal.add_argument("--reason", default="", help="short process-level observation; no source summary")
    heal.add_argument("--dry-run", action="store_true", help="diagnose and show the optimized focus without writing state")

    complete = sub.add_parser("complete-task")
    complete.add_argument("id", help="task id to mark complete after file/symbol checks pass")

    verify = sub.add_parser("verify")
    verify.add_argument("--strict", action="store_true", help="require all completion criteria")

    sub.add_parser("report")

    refresh = sub.add_parser("refresh")
    refresh.add_argument("--reason", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "preflight": cmd_preflight,
        "init": cmd_init,
        "plan": cmd_plan,
        "status": cmd_status,
        "task": cmd_task,
        "start-task": cmd_start_task,
        "check-task": cmd_check_task,
        "heal": cmd_heal,
        "complete-task": cmd_complete_task,
        "verify": cmd_verify,
        "report": cmd_report,
        "refresh": cmd_refresh,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
