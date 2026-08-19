fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 7 {
        eprintln!("usage: preview-worker <pdfium-lib> <pdf> <outdir> <max-w> <max-h> <pages>");
        std::process::exit(2);
    }
    std::process::exit(ufo_preview::worker::run(
        &args[1], &args[2], &args[3], &args[4], &args[5], &args[6],
    ));
}
