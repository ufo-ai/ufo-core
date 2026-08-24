import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const output = new URL("fixture-print-layout.xlsx", import.meta.url).pathname;
const qa = "/tmp/ufo-preview-xlsx-fixture";
await fs.mkdir(qa, { recursive: true });

const workbook = Workbook.create();
const overview = workbook.worksheets.add("Overview");
const detail = workbook.worksheets.add("Print Detail");

overview.showGridLines = false;
overview.getRange("A1:D1").merge();
overview.getRange("A1").values = [["XLSX print layout page one"]];
overview.getRange("A1:D1").format = {
  fill: "#0F4C5C",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  rowHeight: 30,
};
overview.getRange("A3:D8").values = [
  ["Item", "Units", "Rate", "Amount"],
  ["Alpha", 12, 4.5, null],
  ["Beta", 8, 7.25, null],
  ["Gamma", 15, 3.75, null],
  ["Delta", 6, 9.5, null],
  ["Total", null, null, null],
];
overview.getRange("D4").formulas = [["=B4*C4"]];
overview.getRange("D4:D7").fillDown();
overview.getRange("D8").formulas = [["=SUM(D4:D7)"]];
overview.getRange("A3:D3").format = {
  fill: "#DCEAF0",
  font: { bold: true, color: "#17324D" },
  borders: { preset: "outside", style: "thin", color: "#9AB3C1" },
};
overview.getRange("A8:D8").format = {
  fill: "#EEF4F7",
  font: { bold: true, color: "#17324D" },
  borders: { preset: "doubleBottom", style: "medium", color: "#527786" },
};
overview.getRange("C4:D8").format.numberFormat = "$#,##0.00";
overview.getRange("A1:A8").format.columnWidth = 24;
overview.getRange("B1:D8").format.columnWidth = 14;
overview.getRange("A3:D8").format.rowHeight = 22;

detail.showGridLines = false;
detail.getRange("A1:F1").merge();
detail.getRange("A1").values = [["XLSX print layout page two"]];
detail.getRange("A1:F1").format = {
  fill: "#5F0F40",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  rowHeight: 30,
};
detail.getRange("A3:F13").values = [
  ["Code", "Description", "Jan", "Feb", "Mar", "Quarter"],
  ["A-01", "A wrapped description that must remain visible in the printed page", 11, 13, 15, null],
  ["B-02", "Short line", 7, 9, 12, null],
  ["C-03", "Another value", 19, 17, 21, null],
  ["D-04", "Print width boundary", 6, 8, 10, null],
  ["E-05", "No clipped headers", 14, 16, 18, null],
  ["F-06", "Visible row six", 4, 5, 7, null],
  ["G-07", "Visible row seven", 10, 12, 13, null],
  ["H-08", "Visible row eight", 9, 11, 14, null],
  ["I-09", "Visible row nine", 8, 10, 12, null],
  ["J-10", "Visible row ten", 5, 7, 9, null],
];
detail.getRange("F4").formulas = [["=SUM(C4:E4)"]];
detail.getRange("F4:F13").fillDown();
detail.getRange("A3:F3").format = {
  fill: "#F3D9E8",
  font: { bold: true, color: "#4C1235" },
  borders: { preset: "outside", style: "thin", color: "#C59AB4" },
};
detail.getRange("A1:A13").format.columnWidth = 10;
detail.getRange("B1:B13").format = { columnWidth: 26, wrapText: true };
detail.getRange("C1:F13").format.columnWidth = 8;
detail.getRange("A3:F13").format.rowHeight = 28;
detail.getRange("C4:F13").format.numberFormat = "#,##0";

console.log(
  (await workbook.inspect({
    kind: "table",
    range: "Overview!A1:D8",
    include: "values,formulas",
    tableMaxRows: 10,
    tableMaxCols: 6,
  })).ndjson,
);
console.log(
  (await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 100 },
    summary: "fixture formula error scan",
  })).ndjson,
);
for (const sheetName of ["Overview", "Print Detail"]) {
  const preview = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 2,
    format: "png",
  });
  const name = sheetName.toLowerCase().replaceAll(" ", "-");
  await fs.writeFile(`${qa}/${name}.png`, new Uint8Array(await preview.arrayBuffer()));
}
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(output);
