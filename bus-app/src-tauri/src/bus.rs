//! rusqlite half of the bus contract (docs/BUS.md). The app READS events, ACKS them (read_at),
//! and writes ONLY its own transport marker ("desktop") into delivered_via. Producers stay
//! Python-side; this module never inserts events.
//!
//! Connections are opened per call — SQLite open is microseconds, and it keeps every surface
//! free of shared mutable state (no Mutex, no poisoning).

use std::path::Path;
use std::time::Duration;

use rusqlite::Connection;
use serde::Serialize;

/// Identical DDL to harness.bus.store — the app must work even if it launches before the first
/// producer ever runs (fresh-machine OSS case). Idempotent.
const DDL: &str = r#"
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    producer        TEXT NOT NULL,
    lane            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    severity        TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warn', 'alert')),
    idempotency_key TEXT NOT NULL,
    read_at         TEXT,
    delivered_via   TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_idem ON events(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_events_unread ON events(read_at) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_events_lane_kind ON events(lane, kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');
"#;

#[derive(Clone, Debug, Serialize)]
pub struct Event {
    pub id: i64,
    pub created_at: String,
    pub producer: String,
    pub lane: String,
    pub kind: String,
    pub subject: String,
    pub title: String,
    pub body: String,
    pub payload_json: String,
    pub severity: String,
    pub read_at: Option<String>,
    pub delivered_via: Vec<String>,
}

pub fn open(path: &Path) -> Result<Connection, String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let conn = Connection::open(path).map_err(|e| e.to_string())?;
    conn.busy_timeout(Duration::from_millis(5000)).map_err(|e| e.to_string())?;
    conn.pragma_update(None, "journal_mode", "WAL").map_err(|e| e.to_string())?;
    conn.execute_batch(DDL).map_err(|e| e.to_string())?;
    Ok(conn)
}

fn row_to_event(row: &rusqlite::Row<'_>) -> rusqlite::Result<Event> {
    let delivered_raw: String = row.get("delivered_via")?;
    let delivered_via: Vec<String> = serde_json::from_str(&delivered_raw).unwrap_or_default();
    Ok(Event {
        id: row.get("id")?,
        created_at: row.get("created_at")?,
        producer: row.get("producer")?,
        lane: row.get("lane")?,
        kind: row.get("kind")?,
        subject: row.get("subject")?,
        title: row.get("title")?,
        body: row.get("body")?,
        payload_json: row.get("payload_json")?,
        severity: row.get("severity")?,
        read_at: row.get("read_at")?,
        delivered_via,
    })
}

pub fn list_events(
    conn: &Connection,
    unread_only: bool,
    lane: Option<&str>,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<Event>, String> {
    let mut sql = String::from("SELECT * FROM events");
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    if unread_only {
        clauses.push("read_at IS NULL".into());
    }
    if let Some(l) = lane {
        clauses.push("lane = ?".into());
        params.push(Box::new(l.to_string()));
    }
    if let Some(k) = kind {
        clauses.push("kind = ?".into());
        params.push(Box::new(k.to_string()));
    }
    if !clauses.is_empty() {
        sql.push_str(&format!(" WHERE {}", clauses.join(" AND ")));
    }
    sql.push_str(" ORDER BY id DESC LIMIT ?");
    params.push(Box::new(limit));
    let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map(rusqlite::params_from_iter(params.iter().map(|p| p.as_ref())), row_to_event)
        .map_err(|e| e.to_string())?;
    rows.collect::<rusqlite::Result<Vec<_>>>().map_err(|e| e.to_string())
}

pub fn unread_count(conn: &Connection) -> Result<i64, String> {
    conn.query_row("SELECT COUNT(*) FROM events WHERE read_at IS NULL", [], |r| r.get(0))
        .map_err(|e| e.to_string())
}

pub fn ack(conn: &Connection, ids: &[i64]) -> Result<usize, String> {
    if ids.is_empty() {
        return Ok(0);
    }
    let marks = vec!["?"; ids.len()].join(",");
    let now = now_utc();
    let sql = format!("UPDATE events SET read_at = ? WHERE id IN ({marks}) AND read_at IS NULL");
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(now)];
    params.extend(ids.iter().map(|i| Box::new(*i) as Box<dyn rusqlite::ToSql>));
    conn.execute(&sql, rusqlite::params_from_iter(params.iter().map(|p| p.as_ref())))
        .map_err(|e| e.to_string())
}

/// Unread events this transport hasn't delivered yet (delivered_via lacks `marker`).
pub fn undelivered_unread(conn: &Connection, marker: &str) -> Result<Vec<Event>, String> {
    let all = list_events(conn, true, None, None, 200)?;
    Ok(all.into_iter().filter(|e| !e.delivered_via.iter().any(|m| m == marker)).collect())
}

/// Append THIS transport's marker (never touching others') — see docs/BUS.md delivered_via rules.
pub fn mark_delivered(conn: &Connection, id: i64, marker: &str) -> Result<(), String> {
    let raw: String = conn
        .query_row("SELECT delivered_via FROM events WHERE id = ?", [id], |r| r.get(0))
        .map_err(|e| e.to_string())?;
    let mut markers: Vec<String> = serde_json::from_str(&raw).unwrap_or_default();
    if !markers.iter().any(|m| m == marker) {
        markers.push(marker.to_string());
    }
    conn.execute(
        "UPDATE events SET delivered_via = ? WHERE id = ?",
        rusqlite::params![serde_json::to_string(&markers).map_err(|e| e.to_string())?, id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[derive(Serialize)]
pub struct DistinctMeta {
    pub lanes: Vec<String>,
    pub kinds: Vec<String>,
}

pub fn distinct_meta(conn: &Connection) -> Result<DistinctMeta, String> {
    let collect = |sql: &str| -> Result<Vec<String>, String> {
        let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
        let rows = stmt.query_map([], |r| r.get::<_, String>(0)).map_err(|e| e.to_string())?;
        rows.collect::<rusqlite::Result<Vec<_>>>().map_err(|e| e.to_string())
    };
    Ok(DistinctMeta {
        lanes: collect("SELECT DISTINCT lane FROM events ORDER BY lane")?,
        kinds: collect("SELECT DISTINCT kind FROM events ORDER BY kind")?,
    })
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    // civil date from days since epoch (Howard Hinnant's algorithm)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if mo <= 2 { y + 1 } else { y };
    (y, mo, d)
}

/// YYYY-MM-DD for today+offset (UTC) — surfaces' {today+N} token substitution rides this.
pub fn civil_date_offset(offset_days: i64) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let (y, mo, d) = civil_from_days(now as i64 / 86_400 + offset_days);
    format!("{y:04}-{mo:02}-{d:02}")
}

fn now_utc() -> String {
    // ISO-8601 UTC seconds without a chrono dependency (keep the crate tree lean).
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let (h, m, s) = ((now % 86_400) / 3600, (now % 3600) / 60, now % 60);
    let (y, mo, d) = civil_from_days(now as i64 / 86_400);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}+00:00")
}
