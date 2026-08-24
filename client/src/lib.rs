//! The ufo client's parts: the wire it speaks, the terminal it draws, the ops it runs, and
//! the `$UFO_HOME` state it keeps.

pub mod clipboard;
pub mod config;
pub mod egress;
pub mod fscli;
#[cfg(unix)]
pub mod guard;
#[cfg(unix)]
pub mod interrupt;
pub mod jsonio;
pub mod llm;
pub mod ops;
pub mod pr;
pub mod ui;
pub mod wire;
