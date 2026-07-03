//! The bus backend facade: one handle, two transports. Local = the rusqlite file (bus.rs,
//! the OOTB default, byte-for-byte unchanged); Remote = an `hn bus serve` instance over the
//! mesh (remote.rs, opt-in via the `bus_url` config key). commands.rs and poller.rs talk to
//! THIS enum so neither ever branches on transport — resolution lives in config::bus_endpoint
//! (which also enforces the demo-seal-trumps-remote rule).

use crate::bus::{self, DistinctMeta, Event};
use crate::config::{self, AppConfig, BusEndpoint};
use crate::remote::RemoteBus;

pub enum BusHandle {
    Local(rusqlite::Connection),
    Remote(RemoteBus),
}

pub fn open(cfg: &AppConfig) -> Result<BusHandle, String> {
    match config::bus_endpoint(cfg) {
        BusEndpoint::Local(path) => bus::open(&path).map(BusHandle::Local),
        BusEndpoint::Remote { url, token } => RemoteBus::new(url, token).map(BusHandle::Remote),
    }
}

impl BusHandle {
    /// This instance's delivered_via marker. Local keeps the historical `"desktop"`; a remote
    /// watchman is its OWN transport instance — `"desktop:{hostname}"` — so every device
    /// delivers its own notification exactly once (the always-on node notifying must not
    /// swallow a remote desk's notification, and vice versa). Acks stay global (read_at), so
    /// acking anywhere still clears badges everywhere.
    pub fn transport_marker(&self) -> String {
        match self {
            BusHandle::Local(_) => "desktop".to_string(),
            BusHandle::Remote(_) => {
                let host = gethostname::gethostname().to_string_lossy().into_owned();
                let host = host.trim();
                if host.is_empty() {
                    "desktop:remote".to_string()
                } else {
                    format!("desktop:{host}")
                }
            }
        }
    }

    pub fn list_events(
        &self,
        unread_only: bool,
        lane: Option<&str>,
        kind: Option<&str>,
        limit: i64,
    ) -> Result<Vec<Event>, String> {
        match self {
            BusHandle::Local(conn) => bus::list_events(conn, unread_only, lane, kind, limit),
            BusHandle::Remote(r) => r.list_events(unread_only, lane, kind, limit),
        }
    }

    pub fn unread_count(&self) -> Result<i64, String> {
        match self {
            BusHandle::Local(conn) => bus::unread_count(conn),
            BusHandle::Remote(r) => r.unread_count(),
        }
    }

    pub fn ack(&self, ids: &[i64]) -> Result<usize, String> {
        match self {
            BusHandle::Local(conn) => bus::ack(conn, ids),
            BusHandle::Remote(r) => r.ack(ids),
        }
    }

    pub fn distinct_meta(&self) -> Result<DistinctMeta, String> {
        match self {
            BusHandle::Local(conn) => bus::distinct_meta(conn),
            BusHandle::Remote(r) => r.distinct_meta(),
        }
    }

    pub fn undelivered_unread(&self, marker: &str) -> Result<Vec<Event>, String> {
        match self {
            BusHandle::Local(conn) => bus::undelivered_unread(conn, marker),
            BusHandle::Remote(r) => r.undelivered_unread(marker),
        }
    }

    pub fn mark_delivered(&self, id: i64, marker: &str) -> Result<(), String> {
        match self {
            BusHandle::Local(conn) => bus::mark_delivered(conn, id, marker),
            BusHandle::Remote(r) => r.mark_delivered(id, marker),
        }
    }
}
