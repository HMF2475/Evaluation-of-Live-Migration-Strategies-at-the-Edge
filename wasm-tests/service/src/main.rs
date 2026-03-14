//! Tactical Edge Service — WebAssembly (WASI) implementation.
//!
//! This service runs as a WASI module and demonstrates:
//! - Stateful edge processing
//! - Application-level checkpoint/restore via state serialisation
//! - Compatibility across heterogeneous WASM runtimes (wasmtime, wasmedge, wamr)
//!
//! Build for WASI:
//!   cargo build --target wasm32-wasip1 --release
//!
//! Run with wasmtime:
//!   wasmtime --dir=. target/wasm32-wasip1/release/edge-service.wasm

use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::io::{self, BufRead, Write};
use std::time::{SystemTime, UNIX_EPOCH};

/// Application state that can be serialised for migration checkpoints.
#[derive(Debug, Serialize, Deserialize, Clone)]
struct ServiceState {
    service_name: String,
    node_id: String,
    request_count: u64,
    start_time_secs: u64,
    last_processed_secs: Option<u64>,
    data_buffer: Vec<BufferEntry>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct BufferEntry {
    timestamp_secs: u64,
    data: String,
}

/// Simple request/response types for the line-oriented protocol.
#[derive(Debug, Deserialize)]
#[serde(tag = "action")]
enum Request {
    #[serde(rename = "process")]
    Process { data: String },
    #[serde(rename = "state")]
    GetState,
    #[serde(rename = "checkpoint")]
    Checkpoint,
    #[serde(rename = "restore")]
    Restore { state_file: String },
    #[serde(rename = "metrics")]
    GetMetrics,
    #[serde(rename = "health")]
    Health,
}

#[derive(Debug, Serialize)]
struct Response {
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    data: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

impl ServiceState {
    fn new(service_name: &str, node_id: &str) -> Self {
        Self {
            service_name: service_name.to_string(),
            node_id: node_id.to_string(),
            request_count: 0,
            start_time_secs: now_secs(),
            last_processed_secs: None,
            data_buffer: Vec::new(),
        }
    }

    /// Persist state to disk (used as migration checkpoint).
    fn save(&self, path: &str) -> Result<(), String> {
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| format!("Serialisation error: {e}"))?;
        fs::write(path, json).map_err(|e| format!("Write error: {e}"))?;
        Ok(())
    }

    /// Load state from a checkpoint file.
    fn load(path: &str) -> Result<Self, String> {
        let json = fs::read_to_string(path)
            .map_err(|e| format!("Read error: {e}"))?;
        serde_json::from_str(&json)
            .map_err(|e| format!("Deserialisation error: {e}"))
    }

    fn process(&mut self, data: &str) -> serde_json::Value {
        self.request_count += 1;
        self.last_processed_secs = Some(now_secs());

        // Keep buffer bounded
        if self.data_buffer.len() >= 1000 {
            let drain_count = self.data_buffer.len() - 999;
            self.data_buffer.drain(..drain_count);
        }

        let truncated: String = data.chars().take(64).collect();
        self.data_buffer.push(BufferEntry {
            timestamp_secs: now_secs(),
            data: truncated.clone(),
        });

        serde_json::json!({
            "echo": truncated,
            "request_count": self.request_count,
        })
    }

    fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "service_name":         self.service_name,
            "node_id":              self.node_id,
            "request_count":        self.request_count,
            "start_time_secs":      self.start_time_secs,
            "last_processed_secs":  self.last_processed_secs,
            "buffer_length":        self.data_buffer.len(),
            "uptime_secs":          now_secs().saturating_sub(self.start_time_secs),
        })
    }

    fn metrics(&self) -> serde_json::Value {
        serde_json::json!({
            "request_count":  self.request_count,
            "uptime_secs":    now_secs().saturating_sub(self.start_time_secs),
            "buffer_entries": self.data_buffer.len(),
        })
    }
}

fn handle(req: &Request, state: &mut ServiceState, state_file: &str) -> Response {
    match req {
        Request::Health => Response {
            status: "ok".to_string(),
            data: Some(serde_json::json!({"service": state.service_name})),
            error: None,
        },
        Request::Process { data } => {
            let result = state.process(data);
            // Auto-checkpoint after every request to keep state file fresh
            let _ = state.save(state_file);
            Response {
                status: "ok".to_string(),
                data: Some(result),
                error: None,
            }
        }
        Request::GetState => Response {
            status: "ok".to_string(),
            data: Some(state.to_json()),
            error: None,
        },
        Request::GetMetrics => Response {
            status: "ok".to_string(),
            data: Some(state.metrics()),
            error: None,
        },
        Request::Checkpoint => match state.save(state_file) {
            Ok(()) => Response {
                status: "checkpointed".to_string(),
                data: Some(serde_json::json!({"state_file": state_file})),
                error: None,
            },
            Err(e) => Response {
                status: "error".to_string(),
                data: None,
                error: Some(e),
            },
        },
        Request::Restore { state_file: sf } => match ServiceState::load(sf) {
            Ok(loaded) => {
                *state = loaded;
                Response {
                    status: "restored".to_string(),
                    data: Some(serde_json::json!({"state_file": sf})),
                    error: None,
                }
            }
            Err(e) => Response {
                status: "error".to_string(),
                data: None,
                error: Some(e),
            },
        },
    }
}

fn main() {
    let service_name = env::var("SERVICE_NAME").unwrap_or_else(|_| "edge-service".to_string());
    let node_id = env::var("NODE_ID").unwrap_or_else(|_| "node-0".to_string());
    let state_file = env::var("STATE_FILE").unwrap_or_else(|_| "service_state.json".to_string());

    // Attempt to restore from existing checkpoint on startup
    let mut state = ServiceState::load(&state_file).unwrap_or_else(|_| {
        ServiceState::new(&service_name, &node_id)
    });

    eprintln!("[edge-service] Starting service '{}' on node '{}'", service_name, node_id);
    eprintln!("[edge-service] State file: {}", state_file);
    eprintln!("[edge-service] Awaiting JSON requests on stdin (one per line)…");

    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        let response = match serde_json::from_str::<Request>(line) {
            Ok(req) => handle(&req, &mut state, &state_file),
            Err(e) => Response {
                status: "error".to_string(),
                data: None,
                error: Some(format!("Parse error: {e}")),
            },
        };

        match serde_json::to_string(&response) {
            Ok(json) => {
                let _ = writeln!(out, "{json}");
            }
            Err(e) => {
                let _ = writeln!(out, "{{\"status\":\"error\",\"error\":\"{e}\"}}");
            }
        }
        let _ = out.flush();
    }

    eprintln!("[edge-service] Shutting down — persisting final state");
    let _ = state.save(&state_file);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_state() -> ServiceState {
        ServiceState::new("test-service", "test-node")
    }

    #[test]
    fn test_process_increments_count() {
        let mut s = make_state();
        s.process("hello");
        assert_eq!(s.request_count, 1);
        s.process("world");
        assert_eq!(s.request_count, 2);
    }

    #[test]
    fn test_buffer_bounded() {
        let mut s = make_state();
        for i in 0..1100 {
            s.process(&format!("item-{i}"));
        }
        assert!(s.data_buffer.len() <= 1000);
    }

    #[test]
    fn test_serialise_roundtrip() {
        let s = make_state();
        let json = serde_json::to_string(&s).expect("serialise");
        let s2: ServiceState = serde_json::from_str(&json).expect("deserialise");
        assert_eq!(s.service_name, s2.service_name);
        assert_eq!(s.request_count, s2.request_count);
    }

    #[test]
    fn test_save_and_load() {
        let s = make_state();
        let path = "/tmp/test_edge_state.json";
        s.save(path).expect("save");
        let loaded = ServiceState::load(path).expect("load");
        assert_eq!(s.service_name, loaded.service_name);
        assert_eq!(s.node_id, loaded.node_id);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn test_handle_health() {
        let mut s = make_state();
        let req = Request::Health;
        let resp = handle(&req, &mut s, "/tmp/unused.json");
        assert_eq!(resp.status, "ok");
    }

    #[test]
    fn test_handle_process() {
        let mut s = make_state();
        let req = Request::Process { data: "test-data".to_string() };
        let resp = handle(&req, &mut s, "/tmp/test_handle.json");
        assert_eq!(resp.status, "ok");
        assert_eq!(s.request_count, 1);
        let _ = std::fs::remove_file("/tmp/test_handle.json");
    }

    #[test]
    fn test_handle_checkpoint_restore() {
        let mut s = make_state();
        s.process("pre-checkpoint");

        let path = "/tmp/test_cr.json";
        let ck_req = Request::Checkpoint;
        let resp = handle(&ck_req, &mut s, path);
        assert_eq!(resp.status, "checkpointed");

        let mut s2 = make_state();
        let restore_req = Request::Restore { state_file: path.to_string() };
        let resp2 = handle(&restore_req, &mut s2, path);
        assert_eq!(resp2.status, "restored");
        assert_eq!(s2.request_count, 1);

        let _ = std::fs::remove_file(path);
    }
}
