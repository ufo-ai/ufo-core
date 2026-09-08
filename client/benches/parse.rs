use ufo::wire::parse_line;

const FIXTURE: &str = include_str!("../tests/fixtures/directives.jsonl");

fn main() {
    divan::main();
}

fn fixture_lines() -> Vec<String> {
    FIXTURE
        .lines()
        .map(|row| {
            let row: serde_json::Value = serde_json::from_str(row).expect("fixture row");
            row["line"].as_str().expect("fixture line").to_string()
        })
        .collect()
}

#[divan::bench]
fn parse_every_directive(bencher: divan::Bencher) {
    let lines = fixture_lines();
    bencher
        .counter(divan::counter::ItemsCount::new(lines.len()))
        .bench(|| {
            for line in &lines {
                divan::black_box(parse_line(line));
            }
        });
}

#[divan::bench]
fn parse_one_text_chunk(bencher: divan::Bencher) {
    bencher.bench(|| divan::black_box(parse_line("txt\tthe answer, streamed one chunk at a time")));
}
