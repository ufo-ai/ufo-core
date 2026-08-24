fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 9 {
        eprintln!(
            "usage: preview-worker <pdfium-lib> <pdf> <outdir> <max-w> <max-h> <start-page> <pages> <crop>"
        );
        std::process::exit(2);
    }
    std::process::exit(ufo_preview::worker::run(ufo_preview::worker::Request {
        lib: &args[1],
        pdf: &args[2],
        outdir: &args[3],
        max_width: &args[4],
        max_height: &args[5],
        start_page: &args[6],
        pages: &args[7],
        crop: args[8] == "1",
    }));
}
