//! HTTP client half of `hn bus serve` (docs/BUS.md "Serving the bus over HTTP") — the app's
//! `bus_url` remote mode. Centralize, don't sync: the always-on node owns the one authoritative
//! bus.db; this client gives a remote watchman the SAME read/ack/deliver surface bus.rs gives a
//! local one. Every method mirrors a bus.rs function 1:1 so backend.rs can enum-dispatch.
//!
//! Sync by design (ureq): the poller is a plain thread and invoke commands are sync fns — an
//! async runtime would be pure weight. Hard timeouts keep a dead mesh a skipped tick / an error
//! zone, never a hung UI.

use std::time::Duration;

use serde_json::Value;

use crate::bus::{DistinctMeta, Event};

pub struct RemoteBus {
    base: String,
    token: String,
    agent: ureq::Agent,
}

/// Server events are the Python `Event` model: `payload` is a JSON OBJECT there, while the app's
/// local row (and the webview contract) carry it as the `payload_json` STRING — re-serialize on
/// the way in so the frontend sees one shape regardless of transport.
fn event_from_json(v: &Value) -> Result<Event, String> {
    let s = |key: &str| v.get(key).and_then(Value::as_str).unwrap_or_default().to_string();
    Ok(Event {
        id: v.get("id").and_then(Value::as_i64).ok_or("event missing integer id")?,
        created_at: s("created_at"),
        producer: s("producer"),
        lane: s("lane"),
        kind: s("kind"),
        subject: s("subject"),
        title: s("title"),
        body: s("body"),
        payload_json: v
            .get("payload")
            .map(|p| p.to_string())
            .unwrap_or_else(|| "{}".to_string()),
        severity: s("severity"),
        read_at: v.get("read_at").and_then(Value::as_str).map(str::to_string),
        delivered_via: v
            .get("delivered_via")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
            .unwrap_or_default(),
    })
}

impl RemoteBus {
    pub fn new(url: String, token: String) -> Result<Self, String> {
        if token.is_empty() {
            // Explicit error over silent local fallback — see config::bus_endpoint rule 2.
            return Err(format!(
                "bus_url is set ({url}) but bus_token is missing — paste the server's \
                 ~/.config/harness/bus-token value into bus-app.json"
            ));
        }
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(4))
            .timeout(Duration::from_secs(10))
            .build();
        Ok(Self { base: url, token, agent })
    }

    fn auth(&self) -> String {
        format!("Bearer {}", self.token)
    }

    /// Map a ureq result to the response JSON, folding transport + HTTP-status failures into
    /// one actionable string (these render verbatim in the Inbox error zone).
    fn read_json(&self, result: Result<ureq::Response, ureq::Error>) -> Result<Value, String> {
        match result {
            Ok(res) => res
                .into_json::<Value>()
                .map_err(|e| format!("remote bus ({}): bad JSON in response: {e}", self.base)),
            Err(ureq::Error::Status(code, res)) => {
                let detail = res
                    .into_json::<Value>()
                    .ok()
                    .and_then(|v| v.get("error").and_then(Value::as_str).map(str::to_string))
                    .unwrap_or_default();
                let hint = if code == 401 { " (check bus_token)" } else { "" };
                Err(format!("remote bus ({}): HTTP {code}{hint} {detail}", self.base))
            }
            Err(e) => Err(format!("remote bus ({}): unreachable: {e}", self.base)),
        }
    }

    fn post(&self, path: &str, body: Value) -> Result<Value, String> {
        let req = self
            .agent
            .post(&format!("{}{path}", self.base))
            .set("Authorization", &self.auth());
        self.read_json(req.send_json(body))
    }

    pub fn list_events(
        &self,
        unread_only: bool,
        lane: Option<&str>,
        kind: Option<&str>,
        limit: i64,
    ) -> Result<Vec<Event>, String> {
        let mut req = self
            .agent
            .get(&format!("{}/api/bus/events", self.base))
            .set("Authorization", &self.auth())
            .query("limit", &limit.to_string());
        if unread_only {
            req = req.query("unread", "1");
        }
        if let Some(l) = lane {
            req = req.query("lane", l);
        }
        if let Some(k) = kind {
            req = req.query("kind", k);
        }
        let body = self.read_json(req.call())?;
        body.get("events")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("remote bus ({}): response missing events[]", self.base))?
            .iter()
            .map(event_from_json)
            .collect()
    }

    fn stats(&self) -> Result<Value, String> {
        let req = self
            .agent
            .get(&format!("{}/api/bus/stats", self.base))
            .set("Authorization", &self.auth());
        self.read_json(req.call())
    }

    pub fn unread_count(&self) -> Result<i64, String> {
        Ok(self.stats()?.get("unread").and_then(Value::as_i64).unwrap_or(0))
    }

    /// Lanes/kinds for the Inbox filter chips, derived from `/stats` maps — the served API has
    /// no dedicated meta route because stats already carries the distinct sets as map keys.
    pub fn distinct_meta(&self) -> Result<DistinctMeta, String> {
        let stats = self.stats()?;
        let keys = |field: &str| -> Vec<String> {
            let mut v: Vec<String> = stats
                .get(field)
                .and_then(Value::as_object)
                .map(|m| m.keys().cloned().collect())
                .unwrap_or_default();
            v.sort();
            v
        };
        Ok(DistinctMeta { lanes: keys("by_lane"), kinds: keys("by_kind") })
    }

    pub fn ack(&self, ids: &[i64]) -> Result<usize, String> {
        if ids.is_empty() {
            return Ok(0);
        }
        let body = self.post("/api/bus/ack", serde_json::json!({ "ids": ids }))?;
        Ok(body.get("acked").and_then(Value::as_u64).unwrap_or(0) as usize)
    }

    /// Client-side twin of bus.rs::undelivered_unread — same 200-event window, same filter.
    pub fn undelivered_unread(&self, marker: &str) -> Result<Vec<Event>, String> {
        let all = self.list_events(true, None, None, 200)?;
        Ok(all.into_iter().filter(|e| !e.delivered_via.iter().any(|m| m == marker)).collect())
    }

    pub fn mark_delivered(&self, id: i64, marker: &str) -> Result<(), String> {
        self.post("/api/bus/delivered", serde_json::json!({ "id": id, "marker": marker }))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_mapping_reserializes_payload_and_tolerates_nulls() {
        let v: Value = serde_json::json!({
            "id": 7, "created_at": "2026-07-02T21:00:00+00:00", "producer": "finance.pulse",
            "lane": "finance", "kind": "day_move", "subject": "TSM", "title": "TSM −5%",
            "body": "…", "payload": {"pct": -5.0}, "severity": "warn",
            "read_at": null, "delivered_via": ["desktop"]
        });
        let e = event_from_json(&v).unwrap();
        assert_eq!(e.id, 7);
        assert_eq!(e.read_at, None);
        assert_eq!(e.delivered_via, vec!["desktop"]);
        // payload object → payload_json string (the webview contract).
        let parsed: Value = serde_json::from_str(&e.payload_json).unwrap();
        assert_eq!(parsed["pct"], -5.0);
        // Missing payload/delivered_via degrade to empty, not errors.
        let sparse: Value = serde_json::json!({"id": 1});
        let e = event_from_json(&sparse).unwrap();
        assert_eq!(e.payload_json, "{}");
        assert!(e.delivered_via.is_empty());
        // No id = a real contract violation.
        assert!(event_from_json(&serde_json::json!({"title": "x"})).is_err());
    }

    #[test]
    fn missing_token_is_an_actionable_error_not_a_fallback() {
        let err = match RemoteBus::new("http://bus-host:8787".into(), String::new()) {
            Err(e) => e,
            Ok(_) => panic!("empty token must be rejected"),
        };
        assert!(err.contains("bus_token is missing"));
    }

    /// Live end-to-end against a REAL `hn bus serve` (ignored by default; run explicitly with
    /// BUS_E2E_URL + BUS_E2E_TOKEN pointing at a sandboxed serve instance):
    /// `cargo test live_e2e -- --ignored`. Drives the full remote surface the app uses —
    /// list / unread / meta / undelivered / mark_delivered / ack — against the Python server.
    #[test]
    #[ignore]
    fn live_e2e_full_surface_against_real_serve() {
        let url = std::env::var("BUS_E2E_URL").expect("set BUS_E2E_URL");
        let token = std::env::var("BUS_E2E_TOKEN").expect("set BUS_E2E_TOKEN");
        let remote = RemoteBus::new(url, token).unwrap();

        let events = remote.list_events(true, None, None, 50).unwrap();
        assert!(!events.is_empty(), "seed the sandbox bus before running");
        let target = events[0].id;

        let marker = "desktop:e2e-test";
        let undelivered = remote.undelivered_unread(marker).unwrap();
        assert!(undelivered.iter().any(|e| e.id == target));
        remote.mark_delivered(target, marker).unwrap();
        let undelivered = remote.undelivered_unread(marker).unwrap();
        assert!(!undelivered.iter().any(|e| e.id == target), "marker must stick");

        let meta = remote.distinct_meta().unwrap();
        assert!(!meta.lanes.is_empty());
        let before = remote.unread_count().unwrap();
        assert_eq!(remote.ack(&[target]).unwrap(), 1);
        assert_eq!(remote.unread_count().unwrap(), before - 1);
    }

    /// Wire-contract round-trip against a one-shot stub server: auth header sent, query encoded,
    /// events parsed. A ~30-line std TcpListener beats a mock framework dependency.
    #[test]
    fn list_events_round_trip_sends_bearer_and_parses() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let handle = std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().unwrap();
            let mut buf = [0u8; 4096];
            let n = sock.read(&mut buf).unwrap();
            let request = String::from_utf8_lossy(&buf[..n]).to_string();
            let body = r#"{"events":[{"id":1,"created_at":"t","producer":"p","lane":"finance","kind":"k","subject":"s","title":"hello","body":"b","payload":{"a":1},"severity":"info","read_at":null,"delivered_via":[]}]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            sock.write_all(response.as_bytes()).unwrap();
            request
        });
        let remote = RemoteBus::new(format!("http://{addr}"), "tok123".into()).unwrap();
        let events = remote.list_events(true, Some("finance"), None, 5).unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].title, "hello");
        assert_eq!(events[0].payload_json, r#"{"a":1}"#);
        let request = handle.join().unwrap();
        assert!(request.starts_with("GET /api/bus/events?"));
        assert!(request.contains("limit=5"));
        assert!(request.contains("unread=1"));
        assert!(request.contains("lane=finance"));
        assert!(request.contains("Authorization: Bearer tok123"));
    }
}
