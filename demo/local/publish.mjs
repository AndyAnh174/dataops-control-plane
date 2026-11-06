import fs from "node:fs";
const report = JSON.parse(fs.readFileSync("artifacts/data-quality-report.json", "utf8"));
if (!report.success) throw new Error("Refusing to publish invalid data");
fs.copyFileSync("rows.json", "trusted-output.json");
console.log("PUBLISHED " + report.dataset.row_count + " validated synthetic rows.");
