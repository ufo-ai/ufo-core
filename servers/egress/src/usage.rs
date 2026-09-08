//! Decode one HTTP response and recover model-reported usage from its SSE or JSON body — the Rust
//! port of `HttpTokenUsage` in the Python egress proxy. `feed` consumes raw response bytes across
//! arbitrary chunk boundaries; it parses the HTTP head, dechunks a `transfer-encoding: chunked`
//! body, decompresses gzip/deflate, buffers SSE `data:` lines (or a bare JSON body), and reads the
//! Anthropic and OpenAI usage shapes. `usage` returns the model and split only when a usage was seen.
//!
//! An OpenAI prompt count is inclusive of its cached prefix, so the cached and cache-write shares
//! are carried out of `input` into their own dimensions. `cache_write_30m` lands un-gated: whether
//! a model prices a 30m tier is a pricing fact core holds, and core folds it back into input when
//! it does not, rather than the data plane carrying the price table. `cached` is clamped to the
//! prompt, then `cached + cache_write` the same way — an impossible split is reshaped, not billed.

use std::io::Write;

use flate2::write::{GzDecoder, ZlibDecoder};
use serde_json::Value;

use crate::types::{Usage, ANTHROPIC_HOST, OPENAI_HOST};

const MAX_HEADER_BYTES: usize = 65536;
const MAX_SSE_BUFFER_BYTES: usize = 1_048_576;

/// A streaming content-decoder: each chunk inflates what it can now, so the buffer holds only the
/// incomplete tail — a long stream is never refused past the cap, a one-line bomb still trips it.
enum Decoder {
    Identity,
    Gzip(GzDecoder<Vec<u8>>),
    Zlib(ZlibDecoder<Vec<u8>>),
    Done,
}

pub struct HttpTokenUsage {
    host: String,
    head: Vec<u8>,
    body: Vec<u8>,
    chunk_buffer: Vec<u8>,
    headers_complete: bool,
    chunked: bool,
    chunk_remaining: Option<i64>,
    chunk_needs_crlf: bool,
    chunk_done: bool,
    decoder: Decoder,
    decoder_finished: bool,
    overflowed: bool,
    seen: bool,
    model: String,
    input: i64,
    output: i64,
    cache_read: i64,
    cache_write_5m: i64,
    cache_write_30m: i64,
    cache_write_1h: i64,
}

impl HttpTokenUsage {
    pub fn new(host: &str) -> HttpTokenUsage {
        HttpTokenUsage {
            host: host.to_string(),
            head: Vec::new(),
            body: Vec::new(),
            chunk_buffer: Vec::new(),
            headers_complete: false,
            chunked: false,
            chunk_remaining: None,
            chunk_needs_crlf: false,
            chunk_done: false,
            decoder: Decoder::Identity,
            decoder_finished: false,
            overflowed: false,
            seen: false,
            model: String::new(),
            input: 0,
            output: 0,
            cache_read: 0,
            cache_write_5m: 0,
            cache_write_30m: 0,
            cache_write_1h: 0,
        }
    }

    pub fn feed(&mut self, chunk: &[u8]) {
        if self.overflowed {
            return;
        }
        if self.headers_complete {
            self.feed_wire_body(chunk);
            return;
        }
        self.head.extend_from_slice(chunk);
        let marker = match find(&self.head, b"\r\n\r\n") {
            Some(m) => m,
            None => {
                if self.head.len() > MAX_HEADER_BYTES {
                    self.fail();
                }
                return;
            }
        };
        if marker > MAX_HEADER_BYTES {
            self.fail();
            return;
        }
        let raw_head = self.head[..marker].to_vec();
        let body = self.head[marker + 4..].to_vec();
        self.head.clear();
        let mut content_encoding: Vec<u8> = b"identity".to_vec();
        let mut transfer: Vec<u8> = Vec::new();
        for line in raw_head.split(|&b| b == b'\n') {
            let line: Vec<u8> = line.iter().copied().filter(|&b| b != b'\r').collect();
            if let Some(colon) = line.iter().position(|&b| b == b':') {
                let name = ascii_lower(trim(&line[..colon]));
                let value = ascii_lower(trim(&line[colon + 1..]));
                match name.as_slice() {
                    b"transfer-encoding" => transfer = value,
                    b"content-encoding" => content_encoding = value,
                    _ => {}
                }
            }
        }
        self.chunked = transfer
            .split(|&b| b == b',')
            .any(|part| trim(part) == b"chunked");
        match content_encoding.as_slice() {
            b"identity" | b"" => {}
            b"gzip" => self.decoder = Decoder::Gzip(GzDecoder::new(Vec::new())),
            b"deflate" => self.decoder = Decoder::Zlib(ZlibDecoder::new(Vec::new())),
            _ => {
                self.fail();
                return;
            }
        }
        self.headers_complete = true;
        self.feed_wire_body(&body);
    }

    pub fn usage(&mut self) -> Option<(String, Usage)> {
        self.finish_decoder();
        if !self.seen {
            let payload = trim(&self.body).to_vec();
            self.maybe_json_body(&payload);
        }
        if !self.seen {
            return None;
        }
        Some((
            self.model.clone(),
            Usage {
                input_tokens: self.input,
                output_tokens: self.output,
                cache_read_tokens: self.cache_read,
                cache_write_5m_tokens: self.cache_write_5m,
                cache_write_30m_tokens: self.cache_write_30m,
                cache_write_1h_tokens: self.cache_write_1h,
            },
        ))
    }

    fn feed_wire_body(&mut self, chunk: &[u8]) {
        if self.chunk_done {
            return;
        }
        if !self.chunked {
            self.decode(chunk);
            return;
        }
        self.chunk_buffer.extend_from_slice(chunk);
        while !self.chunk_done {
            if self.chunk_remaining.is_none() {
                let marker = match find(&self.chunk_buffer, b"\r\n") {
                    Some(m) => m,
                    None => {
                        if self.chunk_buffer.len() > MAX_HEADER_BYTES {
                            self.fail();
                        }
                        return;
                    }
                };
                let size_field: Vec<u8> = self.chunk_buffer[..marker]
                    .split(|&b| b == b';')
                    .next()
                    .unwrap_or(&[])
                    .to_vec();
                self.chunk_buffer.drain(..marker + 2);
                let size = match parse_hex(&size_field) {
                    Some(n) => n,
                    None => {
                        self.fail();
                        return;
                    }
                };
                if size < 0 {
                    self.fail();
                    return;
                }
                if size == 0 {
                    self.chunk_done = true;
                    self.finish_decoder();
                    return;
                }
                self.chunk_remaining = Some(size);
            }
            if let Some(remaining) = self.chunk_remaining {
                if remaining > 0 {
                    let consumed = std::cmp::min(remaining as usize, self.chunk_buffer.len());
                    if consumed == 0 {
                        return;
                    }
                    let payload: Vec<u8> = self.chunk_buffer[..consumed].to_vec();
                    self.chunk_buffer.drain(..consumed);
                    let left = remaining - consumed as i64;
                    self.chunk_remaining = Some(left);
                    self.decode(&payload);
                    if left > 0 {
                        return;
                    }
                    self.chunk_needs_crlf = true;
                }
            }
            if !self.chunk_needs_crlf || self.chunk_buffer.len() < 2 {
                return;
            }
            if &self.chunk_buffer[..2] != b"\r\n" {
                self.fail();
                return;
            }
            self.chunk_buffer.drain(..2);
            self.chunk_remaining = None;
            self.chunk_needs_crlf = false;
        }
    }

    fn decode(&mut self, chunk: &[u8]) {
        // Inflate whatever this chunk yields now and feed it straight on, so a long compressed SSE
        // is consumed line by line and never buffered whole. `feed_body` bounds what stays.
        let produced: Result<Vec<u8>, ()> = match &mut self.decoder {
            Decoder::Identity => Ok(chunk.to_vec()),
            Decoder::Gzip(d) => write_and_drain(d, chunk),
            Decoder::Zlib(d) => write_and_drain(d, chunk),
            Decoder::Done => Ok(Vec::new()),
        };
        match produced {
            Ok(bytes) => self.feed_body(&bytes),
            Err(()) => self.fail(),
        }
    }

    fn finish_decoder(&mut self) {
        if self.decoder_finished || self.overflowed {
            return;
        }
        self.decoder_finished = true;
        let tail = match std::mem::replace(&mut self.decoder, Decoder::Done) {
            Decoder::Identity | Decoder::Done => return,
            Decoder::Gzip(d) => d.finish(),
            Decoder::Zlib(d) => d.finish(),
        };
        match tail {
            Ok(bytes) => self.feed_body(&bytes),
            Err(_) => self.fail(),
        }
    }

    fn feed_body(&mut self, chunk: &[u8]) {
        if self.overflowed {
            return;
        }
        self.body.extend_from_slice(chunk);
        let mut consumed = 0usize;
        while let Some(rel) = find(&self.body[consumed..], b"\n") {
            let newline = consumed + rel;
            let line = self.body[consumed..newline].to_vec();
            self.consume(&line);
            consumed = newline + 1;
        }
        if consumed > 0 {
            self.body.drain(..consumed);
        }
        if self.body.len() > MAX_SSE_BUFFER_BYTES {
            self.fail();
        }
    }

    fn consume(&mut self, line: &[u8]) {
        let payload = trim(line).to_vec();
        if !payload.starts_with(b"data:") {
            self.maybe_json_body(&payload);
            return;
        }
        let payload = trim(&payload[b"data:".len()..]).to_vec();
        if !payload.starts_with(b"{") {
            return;
        }
        let event: Value = match serde_json::from_slice(&payload) {
            Ok(v) => v,
            Err(_) => return,
        };
        if !event.is_object() {
            return;
        }
        if self.host == ANTHROPIC_HOST {
            self.anthropic(&event);
        } else if self.host == OPENAI_HOST {
            self.openai(&event);
        }
    }

    fn maybe_json_body(&mut self, payload: &[u8]) {
        if self.seen || !payload.starts_with(b"{") {
            return;
        }
        let event: Value = match serde_json::from_slice(payload) {
            Ok(v) => v,
            Err(_) => return,
        };
        if !event.is_object() {
            return;
        }
        if self.host == ANTHROPIC_HOST {
            if let Some(model) = event.get("model").and_then(|v| v.as_str()) {
                self.model = model.to_string();
            }
            self.absorb_anthropic(event.get("usage"), true);
        } else if self.host == OPENAI_HOST {
            self.openai(&event);
        }
    }

    fn anthropic(&mut self, event: &Value) {
        match event.get("type").and_then(|v| v.as_str()) {
            Some("message_start") => {
                if let Some(message) = event.get("message").filter(|v| v.is_object()) {
                    if let Some(model) = message.get("model").and_then(|v| v.as_str()) {
                        self.model = model.to_string();
                    }
                    self.absorb_anthropic(message.get("usage"), true);
                }
            }
            Some("message_delta") => self.absorb_anthropic(event.get("usage"), false),
            _ => {}
        }
    }

    fn absorb_anthropic(&mut self, usage: Option<&Value>, initial: bool) {
        let usage = match usage.filter(|v| v.is_object()) {
            Some(u) => u,
            None => return,
        };
        if initial {
            self.input = int_field(usage, "input_tokens");
            self.cache_read = int_field(usage, "cache_read_input_tokens");
            match usage.get("cache_creation").filter(|v| v.is_object()) {
                Some(creation) => {
                    self.cache_write_5m = int_field(creation, "ephemeral_5m_input_tokens");
                    self.cache_write_1h = int_field(creation, "ephemeral_1h_input_tokens");
                }
                None => self.cache_write_1h = int_field(usage, "cache_creation_input_tokens"),
            }
        }
        if let Some(output) = usage.get("output_tokens").and_then(int_value) {
            self.output = output;
        }
        self.seen = true;
    }

    fn openai(&mut self, event: &Value) {
        if let Some(model) = event.get("model").and_then(|v| v.as_str()) {
            self.model = model.to_string();
        }
        let usage = match event.get("usage").filter(|v| v.is_object()) {
            Some(u) => u,
            None => return,
        };
        if usage.get("prompt_tokens").is_some() {
            let (cached, cache_write) = cache_fields(usage, "prompt_tokens_details");
            self.absorb_openai(
                int_field(usage, "prompt_tokens"),
                int_field(usage, "completion_tokens"),
                cached,
                cache_write,
            );
        } else if usage.get("input_tokens").is_some() {
            let (cached, cache_write) = cache_fields(usage, "input_tokens_details");
            self.absorb_openai(
                int_field(usage, "input_tokens"),
                int_field(usage, "output_tokens"),
                cached,
                cache_write,
            );
        }
        // A usage object matching neither shape yields nothing for this event (the Python logs
        // egress.tokens_usage_unparsed); a billable call is never silently zeroed here.
    }

    /// Carry the cached and cache-write shares out of the inclusive OpenAI prompt count, clamping
    /// an impossible split. See the module doc.
    fn absorb_openai(&mut self, prompt: i64, output: i64, cached: i64, cache_write: i64) {
        let cached = if cached > prompt { prompt } else { cached };
        let cache_write = if cached + cache_write > prompt {
            prompt - cached
        } else {
            cache_write
        };
        self.input = prompt - cached - cache_write;
        self.output = output;
        self.cache_read = cached;
        self.cache_write_30m = cache_write;
        self.seen = true;
    }

    fn fail(&mut self) {
        self.overflowed = true;
        self.head.clear();
        self.body.clear();
        self.chunk_buffer.clear();
    }
}

/// Drain the bytes a `write::` decoder has inflated into its inner `Vec` so far, leaving the decoder
/// live for the next chunk.
trait DrainInner {
    fn drain_inner(&mut self) -> Vec<u8>;
}

impl DrainInner for GzDecoder<Vec<u8>> {
    fn drain_inner(&mut self) -> Vec<u8> {
        std::mem::take(self.get_mut())
    }
}

impl DrainInner for ZlibDecoder<Vec<u8>> {
    fn drain_inner(&mut self) -> Vec<u8> {
        std::mem::take(self.get_mut())
    }
}

fn write_and_drain<W: Write + DrainInner>(decoder: &mut W, chunk: &[u8]) -> Result<Vec<u8>, ()> {
    decoder.write_all(chunk).map_err(|_| ())?;
    Ok(decoder.drain_inner())
}

fn int_value(value: &Value) -> Option<i64> {
    // Integers only — a float or bool reads as absent, matching Python's `isinstance(int) and not
    // isinstance(bool)`.
    value.as_i64()
}

fn int_field(object: &Value, name: &str) -> i64 {
    object.get(name).and_then(int_value).unwrap_or(0)
}

/// The (cached, cache_write) split OpenAI reports inside a `*_tokens_details` object.
fn cache_fields(object: &Value, details_name: &str) -> (i64, i64) {
    match object.get(details_name).filter(|v| v.is_object()) {
        Some(details) => (
            int_field(details, "cached_tokens"),
            int_field(details, "cache_write_tokens"),
        ),
        None => (0, 0),
    }
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn trim(bytes: &[u8]) -> &[u8] {
    let start = bytes.iter().position(|b| !b.is_ascii_whitespace());
    let start = match start {
        Some(s) => s,
        None => return &[],
    };
    let end = bytes
        .iter()
        .rposition(|b| !b.is_ascii_whitespace())
        .unwrap();
    &bytes[start..=end]
}

fn ascii_lower(bytes: &[u8]) -> Vec<u8> {
    bytes.iter().map(|b| b.to_ascii_lowercase()).collect()
}

fn parse_hex(bytes: &[u8]) -> Option<i64> {
    let text = std::str::from_utf8(trim(bytes)).ok()?;
    if text.is_empty() {
        return None;
    }
    i64::from_str_radix(text, 16).ok()
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;

    const SSE_HEAD: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\r\n";
    const JSON_HEAD: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n";

    const ANTHROPIC_SSE_BODY: &[u8] = b"event: message_start\r\n\
data: {\"type\":\"message_start\",\"message\":{\"id\":\"m\",\"model\":\"claude-opus-4-8\",\"usage\":{\"input_tokens\":1000,\"cache_read_input_tokens\":3000,\"cache_creation_input_tokens\":4000,\"cache_creation\":{\"ephemeral_5m_input_tokens\":0,\"ephemeral_1h_input_tokens\":4000},\"output_tokens\":1}}}\r\n\r\n\
event: content_block_delta\r\n\
data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"hi\"}}\r\n\r\n\
event: message_delta\r\n\
data: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":2000}}\r\n\r\n\
event: message_stop\r\n\
data: {\"type\":\"message_stop\"}\r\n\r\n";

    const OPENAI_SSE_BODY: &[u8] = b"data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.4\",\"choices\":[{\"delta\":{\"content\":\"hi\"}}],\"usage\":null}\n\n\
data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.4\",\"choices\":[],\"usage\":{\"prompt_tokens\":1000000,\"completion_tokens\":1000000,\"total_tokens\":2000000}}\n\n\
data: [DONE]\n\n";

    const ANTHROPIC_JSON: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"id\":\"msg\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"claude-opus-4-8\",\"content\":[{\"type\":\"text\",\"text\":\"hi\"}],\"stop_reason\":\"end_turn\",\"usage\":{\"input_tokens\":1000,\"cache_read_input_tokens\":3000,\"cache_creation_input_tokens\":4000,\"cache_creation\":{\"ephemeral_5m_input_tokens\":0,\"ephemeral_1h_input_tokens\":4000},\"output_tokens\":2000}}";

    const OPENAI_JSON: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"id\":\"c\",\"object\":\"chat.completion\",\"model\":\"gpt-5.4\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"hi\"}}],\"usage\":{\"prompt_tokens\":1000000,\"completion_tokens\":1000000,\"total_tokens\":2000000}}";

    const OPENAI_CACHED_JSON: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"id\":\"c\",\"object\":\"chat.completion\",\"model\":\"gpt-5.5\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"hi\"}}],\"usage\":{\"prompt_tokens\":100000,\"completion_tokens\":500,\"total_tokens\":100500,\"prompt_tokens_details\":{\"cached_tokens\":90000}}}";

    const OPENAI_RESPONSES_JSON: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"id\":\"resp\",\"object\":\"response\",\"model\":\"gpt-5.6-terra\",\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"hi\"}]}],\"usage\":{\"input_tokens\":100000,\"input_tokens_details\":{\"cached_tokens\":90000},\"output_tokens\":500,\"output_tokens_details\":{\"reasoning_tokens\":100},\"total_tokens\":100500}}";

    const OPENAI_OVER_CACHED_JSON: &[u8] = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"id\":\"c\",\"object\":\"chat.completion\",\"model\":\"gpt-5.5\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"hi\"}}],\"usage\":{\"prompt_tokens\":100000,\"completion_tokens\":500,\"total_tokens\":100500,\"prompt_tokens_details\":{\"cached_tokens\":100001}}}";

    fn full_anthropic() -> Usage {
        Usage {
            input_tokens: 1000,
            output_tokens: 2000,
            cache_read_tokens: 3000,
            cache_write_5m_tokens: 0,
            cache_write_30m_tokens: 0,
            cache_write_1h_tokens: 4000,
        }
    }

    fn feed_all(host: &str, response: &[u8]) -> Option<(String, Usage)> {
        let mut acc = HttpTokenUsage::new(host);
        acc.feed(response);
        acc.usage()
    }

    fn feed_split(host: &str, response: &[u8], step: usize) -> Option<(String, Usage)> {
        let mut acc = HttpTokenUsage::new(host);
        let mut start = 0;
        while start < response.len() {
            let end = std::cmp::min(start + step, response.len());
            acc.feed(&response[start..end]);
            start = end;
        }
        acc.usage()
    }

    fn sse(body: &[u8]) -> Vec<u8> {
        let mut v = SSE_HEAD.to_vec();
        v.extend_from_slice(body);
        v
    }

    #[test]
    fn sse_usage_parses_an_anthropic_stream() {
        assert_eq!(
            feed_all(ANTHROPIC_HOST, &sse(ANTHROPIC_SSE_BODY)),
            Some(("claude-opus-4-8".to_string(), full_anthropic()))
        );
    }

    #[test]
    fn sse_usage_reassembles_across_chunk_boundaries() {
        assert_eq!(
            feed_split(ANTHROPIC_HOST, &sse(ANTHROPIC_SSE_BODY), 1),
            Some(("claude-opus-4-8".to_string(), full_anthropic()))
        );
    }

    #[test]
    fn sse_usage_parses_an_openai_stream() {
        assert_eq!(
            feed_all(OPENAI_HOST, &sse(OPENAI_SSE_BODY)),
            Some((
                "gpt-5.4".to_string(),
                Usage {
                    input_tokens: 1_000_000,
                    output_tokens: 1_000_000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn sse_usage_without_a_usage_event_is_none() {
        let body = b"data: {\"type\":\"message_stop\"}\n\n";
        assert_eq!(feed_all(ANTHROPIC_HOST, &sse(body)), None);
    }

    #[test]
    fn json_body_usage_parses_an_anthropic_response() {
        assert_eq!(
            feed_all(ANTHROPIC_HOST, ANTHROPIC_JSON),
            Some(("claude-opus-4-8".to_string(), full_anthropic()))
        );
    }

    #[test]
    fn json_body_usage_parses_an_openai_response() {
        assert_eq!(
            feed_all(OPENAI_HOST, OPENAI_JSON),
            Some((
                "gpt-5.4".to_string(),
                Usage {
                    input_tokens: 1_000_000,
                    output_tokens: 1_000_000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn json_body_usage_reassembles_across_chunk_boundaries() {
        assert_eq!(
            feed_split(ANTHROPIC_HOST, ANTHROPIC_JSON, 7),
            Some(("claude-opus-4-8".to_string(), full_anthropic()))
        );
    }

    #[test]
    fn openai_cached_prompt_tokens_are_metered_at_the_cache_read_rate() {
        assert_eq!(
            feed_all(OPENAI_HOST, OPENAI_CACHED_JSON),
            Some((
                "gpt-5.5".to_string(),
                Usage {
                    input_tokens: 10_000,
                    output_tokens: 500,
                    cache_read_tokens: 90_000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn a_responses_shaped_body_is_metered_rather_than_billed_nothing() {
        assert_eq!(
            feed_all(OPENAI_HOST, OPENAI_RESPONSES_JSON),
            Some((
                "gpt-5.6-terra".to_string(),
                Usage {
                    input_tokens: 10_000,
                    output_tokens: 500,
                    cache_read_tokens: 90_000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn openai_cache_write_tokens_are_carried_out_of_input_ungated() {
        // prompt 100000 = 90000 cached + 5000 cache-write + 5000 fresh input; core decides whether
        // to price the 30m tier or fold it back.
        let body = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"id\":\"c\",\"object\":\"chat.completion\",\"model\":\"gpt-5.5\",\"choices\":[{\"message\":{\"role\":\"assistant\",\"content\":\"hi\"}}],\"usage\":{\"prompt_tokens\":100000,\"completion_tokens\":500,\"total_tokens\":100500,\"prompt_tokens_details\":{\"cached_tokens\":90000,\"cache_write_tokens\":5000}}}";
        assert_eq!(
            feed_all(OPENAI_HOST, body),
            Some((
                "gpt-5.5".to_string(),
                Usage {
                    input_tokens: 5_000,
                    output_tokens: 500,
                    cache_read_tokens: 90_000,
                    cache_write_30m_tokens: 5_000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn anthropic_undifferentiated_cache_creation_bills_at_the_1h_tier() {
        // With no cache_creation breakdown, a bare cache_creation_input_tokens is the 1h write tier
        // (Anthropic's default), not 5m — the billing the deleted Python proxy was corrected to.
        let body = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"type\":\"message\",\"model\":\"claude-opus-4-8\",\"usage\":{\"input_tokens\":1000,\"cache_creation_input_tokens\":4000,\"output_tokens\":2000}}";
        assert_eq!(
            feed_all(ANTHROPIC_HOST, body),
            Some((
                "claude-opus-4-8".to_string(),
                Usage {
                    input_tokens: 1000,
                    output_tokens: 2000,
                    cache_write_1h_tokens: 4000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn cached_tokens_over_the_prompt_count_are_clamped() {
        assert_eq!(
            feed_all(OPENAI_HOST, OPENAI_OVER_CACHED_JSON),
            Some((
                "gpt-5.5".to_string(),
                Usage {
                    input_tokens: 0,
                    output_tokens: 500,
                    cache_read_tokens: 100_000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn a_usage_object_the_parser_cannot_read_is_not_billed() {
        let body = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"model\":\"gpt-5.5\",\"usage\":{\"tokens_read\":100000,\"tokens_written\":500}}";
        assert_eq!(feed_all(OPENAI_HOST, body), None);
    }

    #[test]
    fn sse_usage_decodes_chunked_http_framing() {
        let mut framed = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n".to_vec();
        let mut start = 0;
        while start < ANTHROPIC_SSE_BODY.len() {
            let end = std::cmp::min(start + 11, ANTHROPIC_SSE_BODY.len());
            let piece = &ANTHROPIC_SSE_BODY[start..end];
            framed.extend_from_slice(format!("{:x}\r\n", piece.len()).as_bytes());
            framed.extend_from_slice(piece);
            framed.extend_from_slice(b"\r\n");
            start = end;
        }
        framed.extend_from_slice(b"0\r\n\r\n");
        assert_eq!(
            feed_split(ANTHROPIC_HOST, &framed, 7),
            Some(("claude-opus-4-8".to_string(), full_anthropic()))
        );
    }

    #[test]
    fn sse_usage_decodes_gzipped_http_body() {
        use flate2::write::GzEncoder;
        use flate2::Compression;
        let mut enc = GzEncoder::new(Vec::new(), Compression::default());
        enc.write_all(OPENAI_SSE_BODY).unwrap();
        let compressed = enc.finish().unwrap();
        let mut response =
            b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncontent-encoding: gzip\r\n\r\n"
                .to_vec();
        response.extend_from_slice(&compressed);
        assert_eq!(
            feed_split(OPENAI_HOST, &response, 7),
            Some((
                "gpt-5.4".to_string(),
                Usage {
                    input_tokens: 1_000_000,
                    output_tokens: 1_000_000,
                    ..Usage::default()
                }
            ))
        );
    }

    #[test]
    fn compressed_usage_overflow_is_bounded_and_refused() {
        use flate2::write::GzEncoder;
        use flate2::Compression;
        let mut enc = GzEncoder::new(Vec::new(), Compression::default());
        enc.write_all(&vec![b'x'; MAX_SSE_BUFFER_BYTES + 1])
            .unwrap();
        let compressed = enc.finish().unwrap();
        let mut response = b"HTTP/1.1 200 OK\r\ncontent-encoding: gzip\r\n\r\n".to_vec();
        response.extend_from_slice(&compressed);
        let mut acc = HttpTokenUsage::new(OPENAI_HOST);
        acc.feed(&response);
        assert_eq!(acc.usage(), None);
        assert!(acc.overflowed);
    }

    #[test]
    fn a_large_gzipped_stream_is_metered_line_by_line_not_refused_as_overflow() {
        use flate2::write::GzEncoder;
        use flate2::Compression;
        // A completion whose decompressed body exceeds the buffer cap: inflating it whole would
        // drop the bill, streaming consumes each line so the trailing usage still meters.
        let filler = b"data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.4\",\
\"choices\":[{\"delta\":{\"content\":\"lorem ipsum dolor sit amet consectetur\"}}],\"usage\":null}\n\n";
        let mut body = Vec::new();
        while body.len() < MAX_SSE_BUFFER_BYTES + MAX_SSE_BUFFER_BYTES / 2 {
            body.extend_from_slice(filler);
        }
        body.extend_from_slice(
            b"data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.4\",\
\"choices\":[],\"usage\":{\"prompt_tokens\":1000000,\"completion_tokens\":1000000,\
\"total_tokens\":2000000}}\n\ndata: [DONE]\n\n",
        );
        assert!(body.len() > MAX_SSE_BUFFER_BYTES);
        let mut enc = GzEncoder::new(Vec::new(), Compression::default());
        enc.write_all(&body).unwrap();
        let compressed = enc.finish().unwrap();
        let mut response =
            b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncontent-encoding: gzip\r\n\r\n"
                .to_vec();
        response.extend_from_slice(&compressed);
        assert_eq!(
            feed_split(OPENAI_HOST, &response, 4096),
            Some((
                "gpt-5.4".to_string(),
                Usage {
                    input_tokens: 1_000_000,
                    output_tokens: 1_000_000,
                    ..Usage::default()
                }
            ))
        );
    }
}
