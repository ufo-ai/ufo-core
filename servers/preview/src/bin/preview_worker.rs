fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 9 {
        eprintln!(
            "usage: preview-worker <pdfium-lib> <pdf> <outdir> <max-w> <max-h> <start-page> <pages> <crop-mode>"
        );
        std::process::exit(2);
    }
    let Some(crop) = ufo_preview::worker::CropMode::parse(&args[8]) else {
        eprintln!("bad crop-mode");
        std::process::exit(2);
    };
    std::process::exit(ufo_preview::worker::run(ufo_preview::worker::Request {
        lib: &args[1],
        pdf: &args[2],
        outdir: &args[3],
        max_width: &args[4],
        max_height: &args[5],
        start_page: &args[6],
        pages: &args[7],
        crop,
    }));
}
