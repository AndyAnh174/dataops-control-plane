import fs from "node:fs";

const rows = JSON.parse(fs.readFileSync("rows.json", "utf8"));
const schemaBad = rows.filter(
  (row) => !Number.isInteger(row.customer_id) || !Number.isFinite(row.amount),
);
const rangeBad = rows.filter(
  (row) => !Number.isFinite(row.amount) || row.amount < 0 || row.amount > 10000,
);
const checks = [
  {
    id: "customer_schema", dimension: "schema", success: schemaBad.length === 0,
    expectation: "customer_id is an integer and amount is a finite number",
    expected: { customer_id: "integer", amount: "number" },
    observed: { invalid_rows: schemaBad.length },
  },
  {
    id: "amount_range", dimension: "validity", success: rangeBad.length === 0,
    expectation: "amount must be between 0 and 10000",
    expected: { min: 0, max: 10000 },
    observed: { invalid_count: rangeBad.length, invalid_customer_ids: rangeBad.map((r) => r.customer_id) },
  },
];
const failed = checks.filter((check) => !check.success).length;
const report = {
  schema_version: "1.0",
  contract: { name: "local-customer-contract", version: "1.0" },
  scenario: failed ? "range_failure" : "healthy",
  success: failed === 0,
  summary: { checks: checks.length, passed: checks.length - failed, failed },
  checks,
  dataset: { row_count: rows.length, columns: ["customer_id", "amount"] },
  generated_at: new Date().toISOString(),
};
fs.mkdirSync("artifacts", { recursive: true });
fs.writeFileSync("artifacts/data-quality-report.json", JSON.stringify(report, null, 2));
console.log("Checked " + rows.length + " synthetic rows against local-customer-contract v1.0");
if (failed) {
  console.error("DATA QUALITY FAILED: amount must be between 0 and 10000.");
  console.error("Invalid customer IDs: " + JSON.stringify(rangeBad.map((r) => r.customer_id)));
  console.error("No data has been published. Inspect the JSON data-quality report.");
  process.exitCode = 1;
} else {
  console.log("DATA QUALITY PASSED: schema and amount-range checks.");
}
