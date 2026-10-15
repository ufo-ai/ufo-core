//! The metering batcher: the proxy enqueues one record per metered CONNECT or model call, and a
//! background task drains them on a short window and posts a batch to the control meter RPC. Batching
//! keeps a burst of egress off the control plane; a post failure is logged and the worker continues,
//! never dropping the channel.

use std::sync::Arc;

use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::control::Control;
use crate::types::MeterRecord;

const QUEUE_MAX: usize = 4096;
const BATCH_MAX: usize = 256;
const BATCH_WINDOW: std::time::Duration = std::time::Duration::from_millis(10);

/// The enqueue half, cloned into every connection task. Dropping all clones closes the channel; the
/// batcher then drains what is queued and exits, which is how a clean shutdown flushes.
#[derive(Clone)]
pub struct MeterSink {
    tx: mpsc::Sender<MeterRecord>,
}

impl MeterSink {
    pub async fn enqueue(&self, record: MeterRecord) {
        let _ = self.tx.send(record).await;
    }
}

/// The owning half: the batcher worker plus the origin sink. `main` holds this; the proxy holds only
/// `sink()` clones. After the connection drain, `main` drops the proxy (releasing its clones) and
/// awaits `shutdown`, so the last window posts before the process exits.
pub struct Meter {
    sink: MeterSink,
    worker: JoinHandle<()>,
}

impl Meter {
    pub fn start(control: Arc<Control>) -> Meter {
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        let worker = tokio::spawn(run(control, rx));
        Meter {
            sink: MeterSink { tx },
            worker,
        }
    }

    pub fn sink(&self) -> MeterSink {
        self.sink.clone()
    }

    /// Close this owner's sink and await the batcher. With every other sink already dropped, the
    /// channel closes, the worker drains and posts its remaining records, and this returns — a
    /// SIGTERM flushes queued egress and token records rather than discarding them.
    pub async fn shutdown(self) {
        drop(self.sink);
        let _ = self.worker.await;
    }
}

async fn run(control: Arc<Control>, mut rx: mpsc::Receiver<MeterRecord>) {
    while let Some(first) = rx.recv().await {
        let mut batch = vec![first];
        tokio::time::sleep(BATCH_WINDOW).await;
        while batch.len() < BATCH_MAX {
            match rx.try_recv() {
                Ok(record) => batch.push(record),
                Err(_) => break,
            }
        }
        if let Err(error) = control.meter(&batch).await {
            tracing::error!(error = %error, records = batch.len(), "egress.meter_batch_failed");
        }
    }
}
